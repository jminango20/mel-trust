"""
Step defs (pytest-bdd) para tests/features/ancoragem_lote.feature.

FASE RED (TDD): escrito ANTES de qualquer integração blockchain em
coop-a/lote_endpoint.py. Hoje POST /lote monta o grafo, roda SHACL, insere
no Fuseki e devolve {"status", "batchNumber", "lote", "producer",
"pre_authorized_code"} — nenhuma chamada a nenhum contrato, nenhum
"txHash" no corpo da resposta. Ver docs/referencia-contratos/README.md
(TransactionOrchestrator, seção 5 — submitTransaction ainda NÃO
CONFIRMADO) e scripts/blockchain/create_process.py (processo
"honey-batch-registration" já existe on-chain pros dois natureId, criado
numa tarefa anterior desta sessão — pré-requisito pra ancoragem, ainda sem
integração no endpoint HTTP).
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

scenarios("../features/ancoragem_lote.feature")

COOP_A_URL = "http://127.0.0.1:8001"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"


@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _limpa_lote_sintetico(context):
    yield
    if "batch_number" not in context:
        return
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?lote agritrust:batchNumber "{context['batch_number']}" .
      ?lote ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_A_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


@given("que a coop-a cadastra um novo lote válido via POST /lote")
def coop_a_cadastra_lote(context):
    suffix = uuid.uuid4().hex[:8]
    batch_number = f"MS-2026-ANCORA-{suffix}"
    body = {
        "batchNumber": batch_number,
        "productType": "HoneyApis",
        "producerName": PRODUCER_NAME,
        "producerDID": PRODUCER_DID,
    }
    context["batch_number"] = batch_number
    context["body"] = body


@when("o lote é inserido com sucesso no Fuseki")
def lote_inserido_com_sucesso(context):
    resp = requests.post(f"{COOP_A_URL}/lote", json=context["body"], timeout=20)
    assert resp.status_code == 200, (
        f"esperado 200 (inserção bem-sucedida, pré-requisito pro cenário "
        f"testar ancoragem), veio {resp.status_code}: {resp.text}"
    )
    context["lote_response"] = resp


@then('o hash do dado do lote é ancorado on-chain via TransactionOrchestrator (createAsset, processId="honey-batch-registration")')
def hash_ancorado_on_chain(context):
    # Sem forma independente (fora do próprio corpo da resposta) de
    # confirmar ancoragem on-chain neste teste ainda — o critério
    # observável disponível hoje é o corpo da resposta HTTP conter o
    # txHash (checado no próximo step). Este step documenta a intenção do
    # Gherkin; a asserção real acontece em "a resposta ... inclui o
    # txHash".
    pass


@then("a resposta de POST /lote inclui o txHash da ancoragem")
def resposta_inclui_tx_hash(context):
    body = context["lote_response"].json()
    assert "txHash" in body, (
        f"esperava campo 'txHash' na resposta de POST /lote (ancoragem "
        f"on-chain), resposta veio sem ele: {body!r}"
    )
    assert body["txHash"], f"'txHash' presente mas vazio/None: {body['txHash']!r}"
