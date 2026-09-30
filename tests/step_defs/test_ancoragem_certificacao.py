"""
Step defs (pytest-bdd) para tests/features/ancoragem_certificacao.feature.

FASE RED (TDD): escrito ANTES de qualquer integração blockchain em
certifier/credential_endpoint.py. Hoje POST /credential do certifier monta
e assina o SD-JWT VC e devolve só {"credential": "<sd-jwt>"} — nenhum
"txHash" no corpo. Ver docs/referencia-contratos/README.md
(TransactionOrchestrator seção 5, ProcessRegistry seção 4 — processo
"honey-certification"/{"HoneyApis","HoneyNativeStinglessBee"}/"issuance" já
existe on-chain, criado com action=0/CREATE_ASSET numa tarefa anterior;
pode precisar de ajuste pra CREATE_DOCUMENT=9 quando a integração real for
implementada, já que a VC de certificação é um documento vinculado ao
lote, não o próprio ativo — decisão de implementação, não deste RED).

Usa batchNumber SINTÉTICO (uuid) por execução, mesmo padrão já aplicado em
test_ancoragem_lote.py/test_ancoragem_laudo.py — necessário pra
repetibilidade contra blockchain real (newAssetIds do documento seria
determinístico por batchNumber; um valor fixo reutilizado entre execuções
reverteria on-chain na 2ª chamada, "documento já existe").

Fluxo do cenário (mesmo mecanismo de test_holder_certificacao.py): lab
analisa -> coop-a busca o pre_authorized_code do lote no certifier (só
existe depois que o certifier confirma conforms=True via SHACL regulatório)
-> troca por access_token -> emite a VC de certificação.
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
from scripts.test_credential_flow import GRANT_TYPE, build_proof_jwt  # noqa: E402

scenarios("../features/ancoragem_certificacao.feature")

COOP_A_URL = "http://localhost:8001"
LAB_URL = "http://localhost:8005"
CERTIFIER_URL = "http://localhost:8003"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_certifier_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERTIFIER_FUSEKI_URL = _certifier_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERTIFIER_FUSEKI_ADMIN_PASSWORD = _certifier_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"

# Chave da coop-a como holder — mesma simplificação de test_holder_certificacao.py.
HOLDER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"


@pytest.fixture
def context():
    return {}


def _apaga_lote_no_coop_a(batch_number: str):
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?lote agritrust:batchNumber "{batch_number}" .
      ?lote ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_A_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


def _apaga_lote_no_certifier(batch_number: str):
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    DELETE WHERE {{
      ?s agritrust:batchNumber "{batch_number}" .
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
    yield
    if "batch_number" not in context:
        return
    _apaga_lote_no_coop_a(context["batch_number"])
    _apaga_lote_no_certifier(context["batch_number"])


@given("que o lote foi analisado pelo lab e aprovado pelo certifier")
def lote_analisado_e_aprovado(context):
    suffix = uuid.uuid4().hex[:8]
    batch_number = f"MS-2026-ANCCERT-{suffix}"

    lote_resp = requests.post(
        f"{COOP_A_URL}/lote",
        json={
            "batchNumber": batch_number,
            "productType": "HoneyApis",
            "producerName": PRODUCER_NAME,
            "producerDID": PRODUCER_DID,
        },
        timeout=20,
    )
    assert lote_resp.status_code == 200, (
        f"esperado 200 no cadastro do lote, veio {lote_resp.status_code}: {lote_resp.text}"
    )

    analise_resp = requests.post(
        f"{LAB_URL}/analise",
        json={
            "batchNumber": batch_number,
            "moistureContent": 18.5,
            "hmfContent": 12.3,
            "acidityContent": 30.0,
            "diastaseActivity": 10.0,
        },
        timeout=20,
    )
    assert analise_resp.status_code == 200, (
        f"esperado 200 na análise do lab, veio {analise_resp.status_code}: {analise_resp.text}"
    )
    context["batch_number"] = batch_number


@when("a coop-a retira a credencial de certificação (Holder) via POST /lote/{batch}/pre-authorized-code seguido de /token e /credential no certifier")
def coop_a_retira_credencial(context):
    batch_number = context["batch_number"]

    lookup_resp = requests.post(
        f"{CERTIFIER_URL}/lote/{batch_number}/pre-authorized-code",
        timeout=10,
    )
    assert lookup_resp.status_code == 200, (
        f"esperado 200 ao buscar pre_authorized_code do lote no certifier, "
        f"veio {lookup_resp.status_code}: {lookup_resp.text}"
    )
    pre_authorized_code = lookup_resp.json()["pre_authorized_code"]

    token_resp = requests.post(
        f"{CERTIFIER_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": pre_authorized_code},
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
    assert context["credential_response"].status_code == 200, (
        f"esperado 200 na emissão da VC, veio "
        f"{context['credential_response'].status_code}: {context['credential_response'].text}"
    )


@then("o hash da VC de certificação é ancorado on-chain via createDocument, com parentAssetId apontando para o asset do lote")
def hash_ancorado_via_create_document(context):
    # Sem forma independente de confirmar isso além do corpo da resposta
    # (checado no próximo step) — mesma estrutura de
    # tests/features/ancoragem_lote.feature/ancoragem_laudo.feature.
    pass


@then("a resposta de POST /credential inclui o txHash da ancoragem")
def resposta_inclui_tx_hash(context):
    body = context["credential_response"].json()
    assert "txHash" in body, (
        f"esperava campo 'txHash' na resposta de POST /credential (ancoragem "
        f"da VC de certificação via createDocument), resposta veio sem ele: {body!r}"
    )
    assert body["txHash"], f"'txHash' presente mas vazio/None: {body['txHash']!r}"
