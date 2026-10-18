"""
Step defs (pytest-bdd) para tests/features/rastreabilidade_mel.feature.

Chama os SERVIÇOS REAIS via HTTP (coop-a :8001, verifier-demo :8004) — não
sobe nada, não faz mock. Pré-condição: coop-a e verifier-demo no ar, com
MS-2026-042 já cadastrado via POST /lote (lote canônico, migrado de
pipeline.py+fixture — ver docs/known-issues.md e CHANGELOG.md).

HISTÓRICO: este arquivo tinha um Cenário 2 que testava rejeição SHACL por
medição via `pipeline.py` (CLI) + fixture inválida — REMOVIDO junto com
pipeline.py (ver docs/known-issues.md). Cobertura equivalente (rejeição por
medição fora do limite) já está em tests/features/lab_certifier.feature,
Cenário 2. Também não é mais reafirmado moistureContent na credencial do
coop-a: desde a migração pra /lote, o lote canônico não carrega mais campos
de qualidade (são exclusivos do certifier/lab, ver
docs/notas-arquitetura/narrativa-como-criterio-mvp.md) — o coop-a que
tinha esses campos residuais (`:Lote_Mel_Silvestre_2026_042`) era
justamente o known-issue já registrado e agora resolvido pela migração.

Reuso: a montagem do proof JWT (posse de chave do holder) e o decode do
SD-JWT reaproveitam scripts/test_credential_flow.py (build_proof_jwt,
b64url_json) em vez de duplicar a lógica de assinatura/decodificação.
"""
import sys
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from jwcrypto.jwk import JWK
from pytest_bdd import given, parsers, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.test_credential_flow import GRANT_TYPE, b64url_json, build_proof_jwt  # noqa: E402

scenarios("../features/rastreabilidade_mel.feature")

COOP_A_URL = "http://127.0.0.1:8001"
VERIFIER_URL = "http://127.0.0.1:8004"

BATCH_NUMBER_VALID = "MS-2026-042"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"
FLORAL_ORIGIN = "Aroeira (Myracrodruon urundeuva)"
PRE_AUTHORIZED_CODE = "coop-a-demo-pre-auth-code"
PRODUCER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")


@pytest.fixture
def context():
    """Estado compartilhado entre os steps de UM cenário (pytest-bdd usa
    fixtures pytest normais para isso; um dict simples é suficiente aqui)."""
    return {}


# --- Cenário 1: fluxo feliz -------------------------------------------------


@given(parsers.parse("o lote {batch_number} já cadastrado no coop-a"))
def lote_ja_cadastrado(context, batch_number):
    # Verifica, não cria — MS-2026-042 é o lote canônico, cadastrado via
    # POST /lote fora deste teste (idempotência de negócio impede
    # recadastro por este step, ver lote_idempotente.feature).
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    SELECT (COUNT(*) AS ?n) WHERE {{ ?lote agritrust:batchNumber "{batch_number}" }}
    """
    resp = requests.post(
        f"{COOP_A_FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    resp.raise_for_status()
    count = int(resp.json()["results"]["bindings"][0]["n"]["value"])
    assert count >= 1, (
        f"esperava {batch_number!r} já cadastrado no coop-a (via POST /lote), "
        f"achou {count} recursos"
    )
    context["batch_number"] = batch_number


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
    # moistureContent NÃO é mais claim do coop-a (campo de qualidade,
    # exclusivo do certifier/lab desde a migração pra POST /lote — ver
    # docstring do módulo). apiaryLocation CONTINUA presente: vem do
    # PRODUTOR (agritrust:hasOperatingLocation), não do lote em si — a
    # migração só recriou o recurso do lote, não tocou no produtor
    # (reusado por find_existing_producer), então a geolocalização
    # original do fixture sobrevive intacta.
    assert "moistureContent" not in disclosed
    assert "apiaryLocation" in disclosed
    assert disclosed["apiaryLocation"]["lat"] == -15.7
    assert disclosed["apiaryLocation"]["lon"] == -47.9


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
