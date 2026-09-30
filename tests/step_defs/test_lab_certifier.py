"""
Step defs (pytest-bdd) para tests/features/lab_certifier.feature.

HISTÓRICO: este arquivo chegou a ser reescrito pra uma arquitetura "lab
stateless" (POST /avaliar no lab, coop-a persistindo o resultado via
POST /lote/{batchNumber}/resultado-analise) por um turno — REVERTIDO.
Voltou à versão federada original: lab com Fuseki próprio (POST /analise),
certifier consultando lab + coop-a/coop-b via SPARQL federado. Ver
docs/known-issues.md/CHANGELOG.md.

LIMPEZA DE DADO LEGADO (fixture `autouse` abaixo): o certifier já tem
MS-2026-042 no PRÓPRIO Fuseki, com moistureContent=18.0, inserido há muito
tempo via certifier/data/sample-lab-result-valid.ttl (pipeline.py, ambos já
removidos do repo — histórico, não mais executável). Sem apagar esse dado
antes do Cenário 1, `/credential` responderia 200 usando-o — um falso
"verde" que mascararia a ausência total de integração com o lab. A fixture
`_limpa_lote_no_certifier` remove esse dado (SPARQL DELETE WHERE) antes de
cada cenário deste arquivo.
"""
import sys
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from jwcrypto.jwk import JWK
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.test_credential_flow import GRANT_TYPE, b64url_json, build_proof_jwt  # noqa: E402

scenarios("../features/lab_certifier.feature")

LAB_URL = "http://localhost:8005"
CERTIFIER_URL = "http://localhost:8003"

# Lidos do PRÓPRIO certifier/.env (fonte real que o config.py do certifier
# usa) — não hardcoded, não reimplementado com valor diferente.
_certifier_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERTIFIER_FUSEKI_URL = _certifier_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERTIFIER_FUSEKI_ADMIN_USER = "admin"
CERTIFIER_FUSEKI_ADMIN_PASSWORD = _certifier_env.get("FUSEKI_ADMIN_PASSWORD")
CERTIFIER_PRE_AUTH_CODE = "certifier-demo-pre-auth-code"
# Qualquer chave Ed25519 serve como holder do proof — o certifier não
# verifica a assinatura (mesma simplificação de M3, ver credential_endpoint.py).
HOLDER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"

BATCH_VALID = "MS-2026-042"
BATCH_REJEITADO = "MS-2026-042-LAB-REJEITADO"


@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _limpa_lote_no_certifier():
    """Remove qualquer dado pré-existente de MS-2026-042 no Fuseki do
    certifier ANTES de cada cenário deste arquivo (SPARQL DELETE WHERE,
    idempotente — não erra se não houver nada a apagar). Ver docstring do
    módulo: sem isso, o fixture legado local mascararia o RED. Roda antes
    dos dois cenários (o Cenário 2 usa um batchNumber diferente, então a
    limpeza é inócua ali)."""
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    DELETE WHERE {{
      ?s agritrust:batchNumber "{BATCH_VALID}" .
      ?s ?p ?o .
    }}
    """
    resp = requests.post(
        f"{CERTIFIER_FUSEKI_URL}/update",
        data={"update": query},
        auth=(CERTIFIER_FUSEKI_ADMIN_USER, CERTIFIER_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    resp.raise_for_status()
    yield


def _emitir_credencial_certifier(batch_number: str) -> requests.Response:
    """Roda o fluxo OID4VCI completo contra o certifier para um batch_number,
    devolvendo a resposta CRUA de POST /credential (pode ser erro)."""
    meta = requests.get(f"{CERTIFIER_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))

    token_resp = requests.post(
        f"{CERTIFIER_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": CERTIFIER_PRE_AUTH_CODE},
        timeout=10,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    nonce_resp = requests.post(f"{CERTIFIER_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]

    key = JWK.from_pem(HOLDER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    return requests.post(
        f"{CERTIFIER_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": batch_number,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )


# --- Cenário 1: lote dentro do padrão ---------------------------------------


@given("que o lote MS-2026-042 já existe no coop-a")
def lote_existe_no_coop_a(context):
    # Fato de contexto (narrativa), não uma ação — já verificado em
    # tests/step_defs/test_rastreabilidade.py. Confere de leve que o coop-a
    # está no ar, sem duplicar aquele teste.
    resp = requests.get("http://localhost:8001/.well-known/openid-credential-issuer", timeout=10)
    assert resp.status_code == 200
    context["batch_number"] = BATCH_VALID


@when("o laboratório registra análise via POST /analise no lab (umidade 18.5%, HMF 12.3, acidez, diastase)")
def lab_registra_analise_valida(context):
    body = {
        "batchNumber": BATCH_VALID,
        "moistureContent": 18.5,
        "hmfContent": 12.3,
        "acidityContent": 30.0,
        "diastaseActivity": 10.0,
    }
    context["analise_response"] = requests.post(f"{LAB_URL}/analise", json=body, timeout=20)


@then("o gate SHACL do lab aprova a forma do dado e insere no Fuseki do lab")
def gate_lab_aprova(context):
    resp = context["analise_response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"


@when("o certifier consulta o Fuseki do lab via SPARQL federado para o lote MS-2026-042")
def certifier_consulta_lab(context):
    context["credential_response"] = _emitir_credencial_certifier(context["batch_number"])


@when("roda o SHACL regulatório (HoneyApisMoistureShape) sobre esse dado")
def certifier_roda_shacl_regulatorio(context):
    # A ação real (rodar SHACL) acontece DENTRO da chamada acima, no
    # servidor — este step só documenta o passo Gherkin "E ...". Nada a
    # fazer aqui além de deixar explícito no fluxo de leitura do cenário.
    pass


@then('aprova (dentro de 20%) e classifica "Aprovado para Comercialização"')
def certifier_aprova_e_classifica(context):
    resp = context["credential_response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    sd_jwt = resp.json()["credential"]
    parts = sd_jwt.split("~")
    payload = b64url_json(parts[0].split(".")[1])
    disclosures = [b64url_json(p) for p in parts[1:] if p]
    disclosed = {d[1]: d[2] for d in disclosures if len(d) == 3}

    assert payload.get("legalStatus") == "Aprovado para Comercialização"
    # Checagem de dado de domínio (não é mais discriminador de origem — o
    # dado legado local foi apagado pela fixture _limpa_lote_no_certifier
    # antes deste cenário rodar, então a única fonte possível para este
    # valor é o lab).
    assert disclosed.get("moistureContent") == "18.5", (
        f"moistureContent veio {disclosed.get('moistureContent')!r}, esperado '18.5'."
    )


# --- Cenário 2: lote fora do padrão ------------------------------------------


@when("o laboratório registra análise com 24.5% de umidade")
def lab_registra_analise_invalida(context):
    body = {
        "batchNumber": BATCH_REJEITADO,
        "moistureContent": 24.5,
        "hmfContent": 12.0,
        "acidityContent": 30.0,
        "diastaseActivity": 10.0,
    }
    context["batch_number"] = BATCH_REJEITADO
    context["analise_response"] = requests.post(f"{LAB_URL}/analise", json=body, timeout=20)


@then("o gate SHACL do lab aprova a forma (é só validação de tipo/presença)")
def gate_lab_aprova_forma(context):
    resp = context["analise_response"]
    assert resp.status_code == 200, f"esperado 200 (lab só valida forma, não regra regulatória), veio {resp.status_code}: {resp.text}"


@then("quando o certifier roda o SHACL regulatório sobre esse dado")
def certifier_roda_shacl_sobre_dado_invalido(context):
    context["credential_response"] = _emitir_credencial_certifier(context["batch_number"])


@then("rejeita, e o lote não é certificado")
def certifier_rejeita(context):
    resp = context["credential_response"]
    assert resp.status_code >= 400, (
        f"esperado erro (>=400) — SHACL regulatório deveria rejeitar 24.5% de "
        f"umidade para HoneyApis, mas veio {resp.status_code}: {resp.text}"
    )
