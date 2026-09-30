"""
Step defs (pytest-bdd) para tests/features/holder_certificacao.feature.

GREEN (pós-implementação): certifier agora tem
POST /lote/{batch_number}/pre-authorized-code (pre_authorized_code_endpoint.py),
que roda a MESMA consulta federada + SHACL regulatório de
credential_endpoint.py e, se conforms=True, gera um código único/de uso
único vinculado ao lote (certifier/pre_auth_codes.py). token_endpoint.py do
certifier agora aceita esse código dinâmico (mesma lógica já usada em
coop-a/coop-b), além do código fixo legado (bypass, mantido).

Fluxo do cenário: lab analisa (POST /analise) -> coop-a busca o código do
lote (POST /lote/{batch}/pre-authorized-code) -> troca por access_token
(POST /token) -> emite a VC de certificação (POST /credential) usando esse
token -> a VC retornada é o que a coop-a guardaria no dossiê do lote.
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

scenarios("../features/holder_certificacao.feature")

LAB_URL = "http://localhost:8005"
CERTIFIER_URL = "http://localhost:8003"

_certifier_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERTIFIER_FUSEKI_URL = _certifier_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERTIFIER_FUSEKI_ADMIN_USER = "admin"
CERTIFIER_FUSEKI_ADMIN_PASSWORD = _certifier_env.get("FUSEKI_ADMIN_PASSWORD")

# Chave da coop-a como holder — a coop-a é quem vai armazenar a VC de
# certificação no dossiê do lote (holder != issuer, aqui o issuer é o
# certifier e o holder passa a ser a cooperativa produtora).
HOLDER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"

BATCH_NUMBER = "MS-2026-042"


@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _limpa_lote_no_certifier():
    """Mesma limpeza de test_lab_certifier.py — remove dado legado de
    MS-2026-042 no Fuseki do certifier antes do cenário."""
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    DELETE WHERE {{
      ?s agritrust:batchNumber "{BATCH_NUMBER}" .
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


@given("que o lote MS-2026-042 foi analisado pelo lab e aprovado pelo certifier")
def lote_analisado_e_aprovado(context):
    body = {
        "batchNumber": BATCH_NUMBER,
        "moistureContent": 18.5,
        "hmfContent": 12.3,
        "acidityContent": 30.0,
        "diastaseActivity": 10.0,
    }
    analise_resp = requests.post(f"{LAB_URL}/analise", json=body, timeout=20)
    assert analise_resp.status_code == 200, (
        f"lab deveria aceitar análise válida, veio {analise_resp.status_code}: {analise_resp.text}"
    )
    context["batch_number"] = BATCH_NUMBER


@when("a coop-a usa o pre_authorized_code do lote para chamar POST /credential no certifier")
def coop_a_usa_pre_authorized_code_do_lote(context):
    batch_number = context["batch_number"]

    # 1. Busca o código pre_authorized_code ESPECÍFICO deste lote — só
    # existe porque o certifier já rodou a consulta federada + SHACL
    # regulatório e confirmou conforms=True (mesma decisão de /credential).
    lookup_resp = requests.post(
        f"{CERTIFIER_URL}/lote/{batch_number}/pre-authorized-code",
        timeout=10,
    )
    assert lookup_resp.status_code == 200, (
        f"esperado 200 ao buscar pre_authorized_code do lote no certifier, "
        f"veio {lookup_resp.status_code}: {lookup_resp.text}"
    )
    pre_authorized_code = lookup_resp.json()["pre_authorized_code"]

    # 2. Troca o código por um access_token (mesmo grant de sempre).
    token_resp = requests.post(
        f"{CERTIFIER_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": pre_authorized_code},
        timeout=10,
    )
    assert token_resp.status_code == 200, (
        f"esperado 200 ao trocar pre_authorized_code por access_token, "
        f"veio {token_resp.status_code}: {token_resp.text}"
    )
    access_token = token_resp.json()["access_token"]

    # 3. Emite a VC de certificação usando esse access_token.
    meta = requests.get(f"{CERTIFIER_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))

    nonce_resp = requests.post(f"{CERTIFIER_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]

    key = JWK.from_pem(HOLDER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    context["credential_response"] = requests.post(
        f"{CERTIFIER_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": batch_number,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )


@then("a VC de certificação é emitida e retornada à coop-a")
def vc_emitida_e_retornada(context):
    resp = context["credential_response"]
    assert resp.status_code == 200, (
        f"esperado 200 ao emitir a VC de certificação, veio {resp.status_code}: {resp.text}"
    )
    assert "credential" in resp.json()
    context["sd_jwt"] = resp.json()["credential"]


@then("a coop-a pode armazenar essa VC como parte do dossiê do lote")
def coop_a_armazena_vc(context):
    # "Armazenar" aqui é a coop-a ter em mãos um SD-JWT válido e decodável,
    # vinculado ao lote certo — o armazenamento físico em si (dossiê) é
    # responsabilidade de um cliente fora do escopo deste MVP; o que este
    # teste garante é que o material que iria pro dossiê é íntegro e
    # correto.
    sd_jwt = context["sd_jwt"]
    parts = sd_jwt.split("~")
    payload = b64url_json(parts[0].split(".")[1])
    assert payload.get("vct") == "https://agritrust.example/vct/honey-certification"
    assert payload.get("batchNumber") == context["batch_number"]
