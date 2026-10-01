"""
Step defs (pytest-bdd) para tests/features/holder_persiste_vc.feature.

FASE RED (TDD): escrito ANTES de qualquer código novo. Hoje a coop-a
(como Holder) recebe a VC de certificação do certifier via o fluxo já
implementado (POST /lote/{batch}/pre-authorized-code → /token →
/credential, mesmo mecanismo de test_holder_certificacao.py/
test_ancoragem_certificacao.py), mas NADA no código da coop-a guarda essa
VC — nenhum endpoint da coop-a recebe/persiste o SD-JWT retirado do
certifier. "Receber com sucesso" hoje é só a resposta HTTP chegar — não
existe nenhum passo seguinte de persistência.

DECISÃO DE DESIGN (RED, sujeita a ajuste na implementação real): a
asserção abaixo espera encontrar o SD-JWT guardado como LITERAL (não RDF
estruturado — a VC já é auto-contida/assinada, decompor em triplas não
agrega nada e complicaria revalidação de assinatura) via um predicado
novo `agritrust:hasCertificationCredential`, vinculado ao recurso do
LOTE (mesma URI que `coop-a/lote_endpoint.py` já usa,
`BEE[f"Lote_{slug(batchNumber)}"]`). Nome do predicado é a interpretação
mais direta do Gherkin ("vinculado ao batchNumber... como literal"),
documentada aqui para a fase GREEN seguir ou renomear com justificativa.
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

scenarios("../features/holder_persiste_vc.feature")

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


@given("que a coop-a retira a VC de certificação do certifier (fluxo já implementado hoje: pre-authorized-code → /token → /credential)")
def coop_a_retira_vc(context):
    suffix = uuid.uuid4().hex[:8]
    batch_number = f"MS-2026-HOLDERVC-{suffix}"

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

    lookup_resp = requests.post(f"{CERTIFIER_URL}/lote/{batch_number}/pre-authorized-code", timeout=10)
    assert lookup_resp.status_code == 200, (
        f"esperado 200 no pre-authorized-code, veio {lookup_resp.status_code}: {lookup_resp.text}"
    )
    pre_authorized_code = lookup_resp.json()["pre_authorized_code"]

    token_resp = requests.post(
        f"{CERTIFIER_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": pre_authorized_code},
        timeout=10,
    )
    assert token_resp.status_code == 200, f"esperado 200 no token, veio {token_resp.status_code}: {token_resp.text}"
    access_token = token_resp.json()["access_token"]

    meta = requests.get(f"{CERTIFIER_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))
    nonce_resp = requests.post(f"{CERTIFIER_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]
    key = JWK.from_pem(HOLDER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    context["batch_number"] = batch_number
    context["credential_proof_bundle"] = {
        "config_id": config_id,
        "proof_jwt": proof_jwt,
        "access_token": access_token,
    }


@when("a coop-a recebe a credencial com sucesso")
def coop_a_recebe_credencial(context):
    bundle = context["credential_proof_bundle"]
    resp = requests.post(
        f"{CERTIFIER_URL}/credential",
        json={
            "credential_configuration_id": bundle["config_id"],
            "proof": {"proof_type": "jwt", "jwt": bundle["proof_jwt"]},
            "batch_number": context["batch_number"],
        },
        headers={"Authorization": f"Bearer {bundle['access_token']}"},
        timeout=30,
    )
    assert resp.status_code == 200, f"esperado 200 na emissão da VC, veio {resp.status_code}: {resp.text}"
    context["sd_jwt"] = resp.json()["credential"]


def _busca_credencial_guardada(batch_number: str) -> list[str]:
    query = f"""{_PREFIX}
    SELECT ?credencial WHERE {{
      ?lote agritrust:batchNumber "{batch_number}" ;
            agritrust:hasCertificationCredential ?credencial .
    }}
    """
    resp = requests.post(
        f"{COOP_A_FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    resp.raise_for_status()
    bindings = resp.json()["results"]["bindings"]
    return [b["credencial"]["value"] for b in bindings]


@then("o SD-JWT completo é guardado no Fuseki da coop-a, vinculado ao batchNumber (como literal, não RDF estruturado)")
def sd_jwt_guardado_no_fuseki(context):
    credenciais = _busca_credencial_guardada(context["batch_number"])
    assert context["sd_jwt"] in credenciais, (
        f"esperava encontrar o SD-JWT recebido ({context['sd_jwt'][:40]}...) guardado "
        f"no Fuseki da coop-a, vinculado ao batchNumber via "
        f"agritrust:hasCertificationCredential — nada encontrado "
        f"(resultados: {credenciais!r})."
    )


@then("uma consulta SPARQL pelo batchNumber recupera a credencial guardada")
def consulta_recupera_credencial(context):
    credenciais = _busca_credencial_guardada(context["batch_number"])
    assert len(credenciais) >= 1, (
        f"esperava pelo menos 1 credencial recuperável via consulta SPARQL por "
        f"batchNumber={context['batch_number']!r}, achou {len(credenciais)}."
    )
