"""
Step defs (pytest-bdd) para tests/features/rastreabilidade_mel.feature.

Chama os SERVIÇOS REAIS via HTTP (coop-a :8001, verifier-demo :8004) — não
sobe nada, não faz mock. Pré-condição: os 4 nós + os 3 Fuseki no ar, com os
fixtures de coop-a já inseridos (pipeline.py, ver docs/demo-ivan.md).

DESVIO DELIBERADO no Cenário 2, documentado aqui (e reportado ao usuário):
o pedido original testava a rejeição SHACL "via POST /lote", igual ao
Cenário 1. Isso não é possível hoje: POST /lote (coop-a/lote_endpoint.py)
DELIBERADAMENTE não aceita campos de qualidade (moistureContent, hmfContent,
acidityContent, diastaseActivity) — são exclusivos do certifier (ver
docstring de lote_endpoint.py e docs/notas-arquitetura/narrativa-como-criterio-mvp.md).
Sem esses campos, as shapes de Violation que dependem deles
(HoneyApisMoistureShape etc.) nunca disparam via /lote — é a mesma lacuna
já registrada em docs/known-issues.md ("POST /lote: gate SHACL, na prática,
sempre passa"). Por isso o Cenário 2 testa a rejeição via `pipeline.py`
(CLI, subprocess), contra o fixture real
coop-a/data/sample-instance-invalid.ttl (umidade 24.5%, já existente no
repo) — é o único caminho real que hoje dispara essa violação específica.

Reuso: a montagem do proof JWT (posse de chave do holder) e o decode do
SD-JWT reaproveitam scripts/test_credential_flow.py (build_proof_jwt,
b64url_json) em vez de duplicar a lógica de assinatura/decodificação.
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from jwcrypto.jwk import JWK
from pytest_bdd import given, parsers, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.test_credential_flow import GRANT_TYPE, b64url_json, build_proof_jwt  # noqa: E402

scenarios("../features/rastreabilidade_mel.feature")

COOP_A_URL = "http://localhost:8001"
VERIFIER_URL = "http://localhost:8004"

# Dado real da narrativa (MS-2026-042), já inserido no Fuseki via
# coop-a/data/sample-instance.ttl — POST /lote nos steps abaixo REAFIRMA
# essas triplas (idempotente, mesma URI determinística), não inventa lote
# novo. Ver docstring do módulo sobre por que "umidade" não é um campo que
# o /lote aceita: ela já está no Fuseki desde o fixture original.
BATCH_NUMBER_VALID = "MS-2026-042"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"
FLORAL_ORIGIN = "Aroeira (Myracrodruon urundeuva)"
APIARY_LAT = -15.7
APIARY_LON = -47.9
PRE_AUTHORIZED_CODE = "coop-a-demo-pre-auth-code"
PRODUCER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"

INVALID_FIXTURE = "data/sample-instance-invalid.ttl"  # relativo a coop-a/


@pytest.fixture
def context():
    """Estado compartilhado entre os steps de UM cenário (pytest-bdd usa
    fixtures pytest normais para isso; um dict simples é suficiente aqui)."""
    return {}


# --- Cenário 1: fluxo feliz -------------------------------------------------


@given(parsers.parse("um lote de mel de Apis mellifera com umidade {moisture}%"))
def lote_com_umidade(context, moisture):
    # MESMO texto de step nos dois cenários (reuso normal de BDD) — a
    # fixture `context` é recriada do zero a cada cenário pelo pytest, então
    # não há vazamento de estado entre o Cenário 1 e o Cenário 2. Aqui só
    # guardamos a umidade esperada para referência/asserção; no Cenário 1
    # ela já está gravada no Fuseki (fixture original, não criada por este
    # step); no Cenário 2, ela corresponde ao fixture inválido usado no
    # step "Quando" (sample-instance-invalid.ttl, 24.5%).
    context["moisture_esperada"] = moisture
    context["batch_number"] = BATCH_NUMBER_VALID


@when("o produtor cadastra o lote via POST /lote no coop-a")
def cadastra_lote(context):
    body = {
        "batchNumber": context["batch_number"],
        "productType": "HoneyApis",
        "producerName": PRODUCER_NAME,
        "producerDID": PRODUCER_DID,
        "floralOrigin": FLORAL_ORIGIN,
        "productionDate": "2026-04-15",
        "apiaryLocation": {"lat": APIARY_LAT, "lon": APIARY_LON},
    }
    context["lote_response"] = requests.post(f"{COOP_A_URL}/lote", json=body, timeout=20)


@then("o gate SHACL aprova e o lote é inserido no Fuseki")
def gate_aprova(context):
    resp = context["lote_response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "inserted"
    assert body["batchNumber"] == context["batch_number"]


@when("o coop-a emite a credencial via POST /credential")
def emite_credencial(context):
    batch_number = context["batch_number"]

    meta = requests.get(f"{COOP_A_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))

    token_resp = requests.post(
        f"{COOP_A_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": PRE_AUTHORIZED_CODE},
        timeout=10,
    )
    assert token_resp.status_code == 200, token_resp.text
    access_token = token_resp.json()["access_token"]

    nonce_resp = requests.post(f"{COOP_A_URL}/nonce", timeout=10)
    assert nonce_resp.status_code == 200, nonce_resp.text
    c_nonce = nonce_resp.json()["c_nonce"]

    key = JWK.from_pem(PRODUCER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    cred_resp = requests.post(
        f"{COOP_A_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": batch_number,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    assert cred_resp.status_code == 200, cred_resp.text
    context["sd_jwt"] = cred_resp.json()["credential"]


@then("o SD-JWT VC contém floralOrigin e demais claims disclosable corretos")
def confere_claims_disclosable(context):
    sd_jwt = context["sd_jwt"]
    parts = sd_jwt.split("~")
    disclosures = [b64url_json(p) for p in parts[1:] if p]
    # cada disclosure é [salt, nome_do_claim, valor]
    disclosed = {d[1]: d[2] for d in disclosures if len(d) == 3}

    assert disclosed.get("floralOrigin") == FLORAL_ORIGIN
    assert disclosed.get("producerName") == PRODUCER_NAME
    assert disclosed.get("producerDID") == PRODUCER_DID
    assert disclosed.get("moistureContent") == context["moisture_esperada"]
    assert "apiaryLocation" in disclosed
    assert disclosed["apiaryLocation"]["lat"] == APIARY_LAT
    assert disclosed["apiaryLocation"]["lon"] == APIARY_LON


@when(parsers.parse('o verifier-demo consulta GET /verify com propósito "{purpose}"'))
def consulta_verify(context, purpose):
    batch_number = context["batch_number"]
    context["verify_response"] = requests.get(
        f"{VERIFIER_URL}/verify/{batch_number}",
        headers={"X-Access-Purpose": purpose},
        timeout=20,
    )


@then("retorna 200 com o dado agregado correto")
def confere_verify(context):
    resp = context["verify_response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["batchNumber"] == context["batch_number"]
    assert body["sourceNode"] == "coop-a"
    assert body["batch"]["floralOrigin"] == FLORAL_ORIGIN


# --- Cenário 2: rejeição SHACL ----------------------------------------------


@when("o produtor tenta cadastrar esse lote via pipeline.py no coop-a")
def tenta_cadastrar_invalido(context):
    import os

    coop_a_dir = REPO_ROOT / "coop-a"
    python_exe = coop_a_dir / ".venv" / "Scripts" / "python.exe"
    # PYTHONIOENCODING=utf-8: sem isso, o pipeline.py filho escreve
    # stdout/stderr na codepage padrão do Windows (cp1252) — daí o
    # `encoding="utf-8"` abaixo (usado pra decodificar o que o filho
    # escreveu) não bate com o que ele realmente escreveu, e o
    # subprocess.run derruba silenciosamente (result.stdout vira None em vez
    # de levantar UnicodeDecodeError de forma visível). Achado real ao
    # rodar este teste pela primeira vez — não é comportamento hipotético.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [str(python_exe), "pipeline.py", INVALID_FIXTURE],
        cwd=str(coop_a_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    context["pipeline_result"] = result


@then("o gate SHACL rejeita e nada é inserido no Fuseki")
def confere_rejeicao(context):
    result = context["pipeline_result"]
    assert result.returncode != 0, f"esperava rejeição (exit != 0), saída:\n{result.stdout}\n{result.stderr}"
    assert "conforms=False" in result.stdout
    assert "Umidade acima de 20%" in result.stdout
