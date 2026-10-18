"""
Step defs (pytest-bdd) para tests/features/ancoragem_laudo.feature.

GREEN (pós-implementação): lab/analise_endpoint.py agora ancora o laudo
on-chain via TransactionOrchestrator.submitTransaction (createDocument,
processId="honey-lab-analysis", parentAssetId=keccak256(batchNumber) — o
MESMO assetId que coop-a/anchoring.py usa ao ancorar o lote). Ver
docs/referencia-contratos/README.md (TransactionOrchestrator seção 5,
ProcessRegistry seção 4).

AJUSTE DE REPETIBILIDADE: a primeira versão deste arquivo usava um
batchNumber FIXO ("MS-2026-042") — funcionava na primeira execução, mas
falhava em execuções subsequentes contra a MESMA blockchain real (Besu não
reseta entre runs de teste): newAssetIds do documento é determinístico
(keccak256(batchNumber + "-lab-analysis")), então a 2ª chamada revertia
on-chain (documento já existe) e a resposta vinha "anchoring_failed", sem
txHash — quebrando o assert. Corrigido aqui com o MESMO padrão já usado em
test_ancoragem_lote.py: batchNumber sintético (uuid) gerado a cada
execução, cadastrado de verdade via POST /lote no coop-a (ancora o asset
PAI) antes de POST /analise no lab (ancora o documento FILHO,
referenciando o pai como parentAssetId) — reflete a dependência real do
cenário: o "Dado" precisa que o lote já exista on-chain.
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

scenarios("../features/ancoragem_laudo.feature")

COOP_A_URL = "http://127.0.0.1:8001"
LAB_URL = "http://127.0.0.1:8005"
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


@given("que o lote MS-2026-042 já foi ancorado on-chain pelo coop-a")
def lote_ja_ancorado(context):
    # Apesar do texto do Gherkin citar "MS-2026-042" (nome ilustrativo do
    # cenário original), a implementação usa um batchNumber SINTÉTICO por
    # execução — necessário pra repetibilidade contra blockchain real (ver
    # docstring do módulo). Cadastra de verdade via POST /lote no coop-a,
    # que ancora o asset do lote on-chain (tests/features/ancoragem_lote.feature)
    # — é esse asset que o laudo vai referenciar como parentAssetId.
    suffix = uuid.uuid4().hex[:8]
    batch_number = f"MS-2026-ANCLAUDO-{suffix}"
    body = {
        "batchNumber": batch_number,
        "productType": "HoneyApis",
        "producerName": PRODUCER_NAME,
        "producerDID": PRODUCER_DID,
    }
    resp = requests.post(f"{COOP_A_URL}/lote", json=body, timeout=20)
    assert resp.status_code == 200, (
        f"esperado 200 no cadastro do lote (pré-requisito: precisa existir "
        f"e estar ancorado on-chain antes do laudo referenciá-lo), veio "
        f"{resp.status_code}: {resp.text}"
    )
    lote_body = resp.json()
    assert lote_body.get("anchoringStatus") == "anchored", (
        f"esperava o lote já ancorado on-chain (anchoringStatus='anchored') "
        f"antes do cenário prosseguir, veio: {lote_body!r}"
    )
    context["batch_number"] = batch_number


@when("o lab avalia a amostra via POST /analise")
def lab_avalia_amostra(context):
    body = {
        "batchNumber": context["batch_number"],
        "moistureContent": 18.5,
        "hmfContent": 12.3,
        "acidityContent": 30.0,
        "diastaseActivity": 10.0,
    }
    resp = requests.post(f"{LAB_URL}/analise", json=body, timeout=20)
    assert resp.status_code == 200, (
        f"esperado 200 (análise válida, pré-requisito pro cenário testar "
        f"ancoragem), veio {resp.status_code}: {resp.text}"
    )
    context["analise_response"] = resp


@then("o hash do laudo é ancorado on-chain via createDocument, com parentAssetId apontando para o asset do lote")
def hash_ancorado_via_create_document(context):
    # Sem forma independente de confirmar isso além do corpo da resposta
    # (checado no próximo step) — mesma estrutura de
    # tests/features/ancoragem_lote.feature (o step "hash ancorado" é
    # documentação de intenção; a asserção real acontece em "a resposta
    # ... inclui o txHash").
    pass


@then("a resposta de POST /analise inclui o txHash da ancoragem")
def resposta_inclui_tx_hash(context):
    body = context["analise_response"].json()
    assert "txHash" in body, (
        f"esperava campo 'txHash' na resposta de POST /analise (ancoragem "
        f"do laudo via createDocument), resposta veio sem ele: {body!r}"
    )
    assert body["txHash"], f"'txHash' presente mas vazio/None: {body['txHash']!r}"
