"""
Step defs (pytest-bdd) para tests/features/holder_persiste_vc.feature.

GREEN (pós-implementação): coop-a ganhou
`POST /lote/{batch_number}/retirar-certificacao` (coop-a/lote_endpoint.py)
— roda o fluxo completo de Holder contra o certifier
(pre-authorized-code → /token → /nonce → /credential, assinado pela
própria chave do issuer da coop-a, simplificação de MVP documentada no
endpoint) e, se a VC vier com sucesso, guarda o SD-JWT completo como
LITERAL no Fuseki da coop-a via `agritrust:hasCertificationCredential`,
vinculado ao recurso do lote (mesma URI determinística de POST /lote).
"""
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/holder_persiste_vc.feature")

COOP_A_URL = "http://127.0.0.1:8001"
LAB_URL = "http://127.0.0.1:8005"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_certifier_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERTIFIER_FUSEKI_URL = _certifier_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERTIFIER_FUSEKI_ADMIN_PASSWORD = _certifier_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"


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
    """Prepara o pré-requisito (lote + laudo reais) pro endpoint novo poder
    rodar o fluxo de Holder — a RETIRADA em si acontece no step 'Quando'."""
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
    context["batch_number"] = batch_number


@when("a coop-a recebe a credencial com sucesso")
def coop_a_recebe_credencial(context):
    resp = requests.post(
        f"{COOP_A_URL}/lote/{context['batch_number']}/retirar-certificacao",
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"esperado 200 em POST /lote/.../retirar-certificacao, veio {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("status") == "stored", f"esperava status='stored', veio: {body!r}"
    context["sd_jwt"] = body["credential"]


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
