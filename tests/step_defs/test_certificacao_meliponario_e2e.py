"""
Step defs (pytest-bdd) para tests/features/certificacao_meliponario_e2e.feature.

Cenário de INTEGRAÇÃO, não de descoberta de comportamento novo: compõe
peças já testadas isoladamente (POST /lote com meliponaryId/
environmentalLicenseNumber, POST /analise no lab, GET
/lote/{batch}/pre-authorized-code no certifier, /token, /credential) numa
única cadeia real, contra coop-b (mel nativo) em vez de coop-a. Se falhar,
expõe uma integração que os testes unitários/isolados não cobriam
(ex: SHACL regulatório de mel nativo aplicado corretamente via o fluxo do
certifier, não só via POST /lote).
"""
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from jwcrypto.jwk import JWK
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.test_credential_flow import GRANT_TYPE, b64url_json, build_proof_jwt  # noqa: E402

scenarios("../features/certificacao_meliponario_e2e.feature")

COOP_B_URL = "http://localhost:8002"
LAB_URL = "http://localhost:8005"
CERTIFIER_URL = "http://localhost:8003"

PRODUCER_NAME = "João"
PRODUCER_DID = "did:key:z6Mkkc7de5XEc1sUGBYxHo8yLWM9yTsnorqCNK7HnE8SZnVT"
BATCH_NUMBER = "MN-2026-015"

_coop_b_env = dotenv_values(REPO_ROOT / "coop-b" / ".env")
COOP_B_FUSEKI_URL = _coop_b_env.get("FUSEKI_URL", "http://127.0.0.1:3031/coop-b")
COOP_B_FUSEKI_ADMIN_PASSWORD = _coop_b_env.get("FUSEKI_ADMIN_PASSWORD")

_certifier_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERTIFIER_FUSEKI_URL = _certifier_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERTIFIER_FUSEKI_ADMIN_PASSWORD = _certifier_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"

# Chave da coop-b como holder — mesma simplificação de test_holder_certificacao.py
# (o certifier não verifica a assinatura do proof, ver credential_endpoint.py).
HOLDER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"


@pytest.fixture
def context():
    return {}


def _apaga_lote_no_coop_b():
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?lote agritrust:batchNumber "{BATCH_NUMBER}" .
      ?lote ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_B_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_B_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


def _apaga_meliponary_no_coop_b(meliponary_id: str):
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?m agritrust:hasIdentifier "{meliponary_id}" .
      ?m ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_B_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_B_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


def _apaga_lote_no_certifier():
    """Remove qualquer dado (próprio ou federado via SERVICE) que o
    certifier tenha persistido localmente sobre este batchNumber — mesma
    limpeza defensiva de test_lab_certifier.py/test_holder_certificacao.py."""
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    DELETE WHERE {{
      ?s agritrust:batchNumber "{BATCH_NUMBER}" .
      ?s ?p ?o .
    }}
    """
    requests.post(
        f"{CERTIFIER_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", CERTIFIER_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


@pytest.fixture(autouse=True)
def _limpeza(context):
    _apaga_lote_no_certifier()
    yield
    _apaga_lote_no_coop_b()
    if "meliponary_id" in context:
        _apaga_meliponary_no_coop_b(context["meliponary_id"])
    _apaga_lote_no_certifier()


@given("que o produtor João cadastra o lote MN-2026-015 via POST /lote no coop-b, incluindo meliponaryId e environmentalLicenseNumber")
def produtor_cadastra_lote(context):
    suffix = uuid.uuid4().hex[:8]
    meliponary_id = f"MEL-E2E-{suffix}"
    license_number = f"IBRAM-LA-E2E-{suffix}"
    body = {
        "batchNumber": BATCH_NUMBER,
        "productType": "HoneyNativeStinglessBee",
        "producerName": PRODUCER_NAME,
        "producerDID": PRODUCER_DID,
        "meliponaryId": meliponary_id,
        "environmentalLicenseNumber": license_number,
    }
    resp = requests.post(f"{COOP_B_URL}/lote", json=body, timeout=20)
    assert resp.status_code == 200, f"esperado 200 no cadastro do lote, veio {resp.status_code}: {resp.text}"
    context["batch_number"] = BATCH_NUMBER
    context["meliponary_id"] = meliponary_id
    context["license_number"] = license_number


@given("o laboratório analisa a amostra (umidade 25.4%, acidez dentro do limite condicional de meliponíneos)")
def lab_analisa_amostra(context):
    body = {
        "batchNumber": BATCH_NUMBER,
        "moistureContent": 25.4,
        "hmfContent": 12.0,
        "acidityContent": 70.0,
        "diastaseActivity": 10.0,
    }
    resp = requests.post(f"{LAB_URL}/analise", json=body, timeout=20)
    assert resp.status_code == 200, f"esperado 200 na análise do lab, veio {resp.status_code}: {resp.text}"


@when("a coop-b usa POST /lote/MN-2026-015/pre-authorized-code no certifier")
def coop_b_busca_pre_authorized_code(context):
    context["lookup_response"] = requests.post(
        f"{CERTIFIER_URL}/lote/{BATCH_NUMBER}/pre-authorized-code",
        timeout=10,
    )


@then("o código é gerado (SHACL regulatório de mel nativo aprova, mesmo com umidade acima do limite de Apis)")
def codigo_e_gerado(context):
    resp = context["lookup_response"]
    assert resp.status_code == 200, (
        f"esperado 200 (SHACL regulatório de mel nativo deveria aprovar 25.4% "
        f"de umidade, dentro da faixa de warning/info de HoneyNativeStinglessBee, "
        f"não violation), veio {resp.status_code}: {resp.text}"
    )
    context["pre_authorized_code"] = resp.json()["pre_authorized_code"]


@then("a coop-b retira a credencial de certificação com sucesso")
def coop_b_retira_credencial(context):
    token_resp = requests.post(
        f"{CERTIFIER_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": context["pre_authorized_code"]},
        timeout=10,
    )
    assert token_resp.status_code == 200, (
        f"esperado 200 na troca do código por access_token, veio "
        f"{token_resp.status_code}: {token_resp.text}"
    )
    access_token = token_resp.json()["access_token"]

    meta = requests.get(f"{CERTIFIER_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))

    nonce_resp = requests.post(f"{CERTIFIER_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]

    key = JWK.from_pem(HOLDER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    resp = requests.post(
        f"{CERTIFIER_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": context["batch_number"],
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    assert resp.status_code == 200, f"esperado 200 na emissão da VC, veio {resp.status_code}: {resp.text}"
    sd_jwt = resp.json()["credential"]
    payload = b64url_json(sd_jwt.split("~")[0].split(".")[1])
    assert payload.get("batchNumber") == BATCH_NUMBER
