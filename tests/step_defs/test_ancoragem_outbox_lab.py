"""
Step defs (pytest-bdd) para tests/features/ancoragem_outbox_lab.feature —
outbox de ancoragem do LAUDO no lab (POST /analise).

FASE RED (TDD): escrito ANTES de qualquer código de outbox em lab/. Hoje o lab
não tem POST /anchoring/retry nem grava marca de ancoragem no Fuseki.

DECISÕES DE DESIGN (o código de produção do lab deve seguir):
1. A marca de ancoragem fica num RECURSO SEPARADO no Fuseki do lab,
   `:AnchoringTask_<slug(batchNumber)>`, NUNCA no recurso do laudo
   (`:Analise_<slug>`). O hash do laudo é o keccak do grafo devolvido por
   `_LAUDO_CONSTRUCT_TEMPLATE` (verifier-demo/onchain_verification.py), que
   seleciona o laudo por padrão fixo; a tarefa fica fora dele, então gravar
   status não muda o hash nem quebra GET /verify. A tarefa liga ao laudo por
   `:anchoringBatch` (não por `agritrust:batchNumber`, que casaria com o
   padrão do laudo).
2. assetId do documento = keccak256(batchNumber + "-lab-analysis");
   parentAssetId = keccak256(batchNumber) (o asset do lote).
3. Retry = endpoint manual `POST /anchoring/retry` (sem scheduler na PoC),
   mesmo contrato do lote: {"processed": n, "results": [{"batchNumber",
   "outcome": "sent" | "already_onchain" | "mismatch" | "waiting_parent" |
   "in_flight" | "failed" | "lote_not_found"..., "txHash"}]}.
4. Mesma lógica de in_flight do lote: se o envio falha, reconsulta getAsset;
   existe => resolve; não existe + "Known transaction" => in_flight (tarefa
   segue pending, sem ERROR); outro erro => failed.
5. Específico do laudo: se o PAI (asset do lote, keccak256(batchNumber)) ainda
   não existe on-chain, o retry NÃO envia (a tx reverteria) e responde
   `waiting_parent`; a tarefa continua pending.
6. "Nada reenviado" é verificado pelo nonce da conta Besu do lab.

Como cada estado é plantado (sem flag de falha no código de produção):
- laudo/lote plantados DIRETO no Fuseki (sem passar por POST, que ancoraria);
- "já on-chain" = POST /lote (coop-a) + POST /analise (lab) reais, depois a
  marca volta a pending;
- "mismatch" = laudo real ancorado e depois um valor trocado no Fuseki;
- "in_flight": mesma exceção do lote — só a resposta do nó ao envio é
  simulada (tests/support/run_retry_injected.py), ver o comentário lá.
"""
import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pytest_bdd import given, scenarios, then, when
from rdflib import Graph
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/ancoragem_outbox_lab.feature")

COOP_A_URL = "http://localhost:8001"
LAB_URL = "http://localhost:8005"
RPC_URL = "http://localhost:8545"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"
BEE_NS = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"
LAB_NS = "https://w3id.org/agrisemantics/AGRITRUST/lab-extension#"
BESU_KEY_PATH = REPO_ROOT / "lab" / "keys" / "lab-besu.key"

_lab_env = dotenv_values(REPO_ROOT / "lab" / ".env")
LAB_FUSEKI = _lab_env.get("FUSEKI_URL", "http://127.0.0.1:3033/lab")
LAB_PASSWORD = _lab_env.get("FUSEKI_ADMIN_PASSWORD")
_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = (
    f"PREFIX : <{BEE_NS}>\n"
    f"PREFIX lab: <{LAB_NS}>\n"
    "PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"
    "PREFIX foaf: <http://xmlns.com/foaf/0.1/>\n"
    "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
)

_LAUDO_VALUES = {"moisture": "18.5", "hmf": "12.3", "acidity": "30.0", "diastase": "10.0"}


def _update(base: str, password: str, query: str):
    requests.post(f"{base}/update", data={"update": query}, auth=("admin", password), timeout=20).raise_for_status()


def _select(base: str, password: str, query: str) -> list[dict]:
    r = requests.post(
        f"{base}/sparql", data={"query": query},
        headers={"Accept": "application/sparql-results+json"}, auth=("admin", password), timeout=20,
    )
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def _slug(batch: str) -> str:
    return batch.replace("-", "_")


# --- tarefa (no Fuseki do LAB) -----------------------------------------------

def _plant_pending_task(batch: str):
    t = f"{BEE_NS}AnchoringTask_{_slug(batch)}"
    _update(LAB_FUSEKI, LAB_PASSWORD, f"""{_PREFIX}
    DELETE WHERE {{ <{t}> ?p ?o }} ;
    INSERT DATA {{
      <{t}> a :AnchoringTask ;
            :anchoringBatch "{batch}" ;
            :anchoringStatus "pending" .
    }}""")


def _read_task(batch: str) -> dict:
    rows = _select(LAB_FUSEKI, LAB_PASSWORD, f"""{_PREFIX}
    SELECT ?status ?tx WHERE {{
      ?t :anchoringBatch "{batch}" ; :anchoringStatus ?status .
      OPTIONAL {{ ?t :anchoringTxHash ?tx }}
    }}""")
    assert rows, f"nenhuma AnchoringTask encontrada no Fuseki do lab para o lote {batch!r}"
    return {"status": rows[0]["status"]["value"], "txHash": rows[0].get("tx", {}).get("value")}


# --- laudo (no Fuseki do LAB) e lote (no Fuseki do COOP-A) ---------------------

def _plant_laudo(batch: str):
    """Insere o laudo (as mesmas 6 triplas que POST /analise monta) direto no Fuseki do lab."""
    v = _LAUDO_VALUES
    _update(LAB_FUSEKI, LAB_PASSWORD, f"""{_PREFIX}
    INSERT DATA {{
      <{BEE_NS}Analise_{_slug(batch)}> a lab:AnaliseLaboratorial ;
          agritrust:batchNumber "{batch}" ;
          :hasMoistureContent "{v['moisture']}"^^xsd:decimal ;
          :hasHMFContent "{v['hmf']}"^^xsd:decimal ;
          :hasAcidity "{v['acidity']}"^^xsd:decimal ;
          :hasDiastaseActivity "{v['diastase']}"^^xsd:decimal .
    }}""")


def _plant_lote_sem_ancorar(batch: str, product_type: str = "HoneyApis"):
    """Lote no Fuseki do coop-a SEM ancorar on-chain (nem passar por POST /lote):
    o lab resolve o productType por SPARQL federado, mas o pai não existe na chain."""
    rows = _select(COOP_A_FUSEKI, COOP_A_PASSWORD, f"""{_PREFIX}
    SELECT ?p WHERE {{ ?p agritrust:hasDID "{PRODUCER_DID}" }} LIMIT 1""")
    if rows:
        producer, producer_triples = rows[0]["p"]["value"], ""
    else:
        producer = f"{BEE_NS}Produtor_outbox_{uuid.uuid4().hex[:8]}"
        producer_triples = (
            f'<{producer}> a agritrust:DataProducer ; foaf:name "{PRODUCER_NAME}" ; '
            f'agritrust:hasDID "{PRODUCER_DID}" .'
        )
    _update(COOP_A_FUSEKI, COOP_A_PASSWORD, f"""{_PREFIX}
    INSERT DATA {{
      {producer_triples}
      <{BEE_NS}Lote_{_slug(batch)}> a :{product_type} ;
          agritrust:ownedBy <{producer}> ;
          agritrust:batchNumber "{batch}" .
    }}""")


def _post_lote_ancorado(batch: str):
    resp = requests.post(
        f"{COOP_A_URL}/lote",
        json={"batchNumber": batch, "productType": "HoneyApis",
              "producerName": PRODUCER_NAME, "producerDID": PRODUCER_DID},
        timeout=60,
    )
    assert resp.status_code == 200, f"POST /lote esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("anchoringStatus") == "anchored", (
        f"pré-condição: o lote (pai) precisa estar ancorado on-chain, veio {resp.json()!r}"
    )


def _post_analise_ancorada(batch: str):
    v = _LAUDO_VALUES
    resp = requests.post(
        f"{LAB_URL}/analise",
        json={"batchNumber": batch, "moistureContent": float(v["moisture"]), "hmfContent": float(v["hmf"]),
              "acidityContent": float(v["acidity"]), "diastaseActivity": float(v["diastase"])},
        timeout=60,
    )
    assert resp.status_code == 200, f"POST /analise esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("anchoringStatus") == "anchored", (
        f"pré-condição: o laudo precisa estar realmente ancorado on-chain, veio {resp.json()!r}"
    )


# --- chain --------------------------------------------------------------------

def _besu_nonce() -> int:
    from eth_account import Account
    addr = Account.from_key(BESU_KEY_PATH.read_text().strip()).address
    r = requests.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionCount", "params": [addr, "latest"]},
        timeout=10,
    )
    r.raise_for_status()
    return int(r.json()["result"], 16)


def _lab_module(filename: str, alias: str):
    """Carrega `lab/<filename>` por caminho (nomes como `anchoring`/`hash_utils` colidem com os dos outros nós)."""
    spec = importlib.util.spec_from_file_location(f"_lab_{alias}", REPO_ROOT / "lab" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LAUDO_CONSTRUCT_TEMPLATE = """
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX bee: <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX lab: <https://w3id.org/agrisemantics/AGRITRUST/lab-extension#>
CONSTRUCT {
  ?analise a lab:AnaliseLaboratorial .
  ?analise agritrust:batchNumber ?batchNumber .
  ?analise bee:hasMoistureContent ?moisture .
  ?analise bee:hasHMFContent ?hmf .
  ?analise bee:hasAcidity ?acidity .
  ?analise bee:hasDiastaseActivity ?diastase .
}
WHERE {
  ?analise a lab:AnaliseLaboratorial ;
           agritrust:batchNumber "%(b)s" ;
           bee:hasMoistureContent ?moisture ;
           bee:hasHMFContent ?hmf ;
           bee:hasAcidity ?acidity ;
           bee:hasDiastaseActivity ?diastase .
  BIND("%(b)s" AS ?batchNumber)
}
"""  # igual a `_LAUDO_CONSTRUCT_TEMPLATE` de verifier-demo/onchain_verification.py


def _laudo_data_hash(batch: str) -> bytes:
    r = requests.post(
        f"{LAB_FUSEKI}/sparql", data={"query": _LAUDO_CONSTRUCT_TEMPLATE % {"b": batch}},
        headers={"Accept": "application/n-triples"}, auth=("admin", LAB_PASSWORD), timeout=20,
    )
    r.raise_for_status()
    g = Graph()
    g.parse(data=r.text, format="nt")
    return bytes(_lab_module("hash_utils.py", "hash").deterministic_nt_hash(g))


# --- execução do retry --------------------------------------------------------

def _run_retry(context):
    nonce_before = _besu_nonce()
    try:
        resp = requests.post(f"{LAB_URL}/anchoring/retry", timeout=180)
    except requests.exceptions.Timeout:
        pytest.fail("POST /anchoring/retry (lab) não respondeu em 180s — RPC/túnel não respondeu?", pytrace=False)
    assert resp.status_code == 200, f"POST /anchoring/retry esperado 200, veio {resp.status_code}: {resp.text}"
    context.setdefault("runs", []).append(
        {"body": resp.json(), "nonce_before": nonce_before, "nonce_after": _besu_nonce()}
    )


def _result_for(run: dict, batch: str) -> dict:
    match = [r for r in run["body"].get("results", []) if r.get("batchNumber") == batch]
    assert len(match) == 1, f"esperava 1 resultado para {batch!r}, veio {match!r} (resposta: {run['body']!r})"
    return match[0]


def _run_retry_injected(context):
    """Roda o retry real do lab (`analise_endpoint.retry_anchoring`) com o envio
    respondendo 'Known transaction' (ver tests/support/run_retry_injected.py)."""
    py = REPO_ROOT / "lab" / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.run(
        [str(py), str(REPO_ROOT / "tests" / "support" / "run_retry_injected.py"), "analise_endpoint"],
        cwd=REPO_ROOT / "lab", capture_output=True, text=True, timeout=180,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT_JSON:")]
    assert lines, f"helper de injeção não devolveu resultado (rc={proc.returncode}):\n{proc.stderr[-1500:]}"
    payload = json.loads(lines[-1][len("RESULT_JSON:"):])
    context.setdefault("runs", []).append(
        {"body": payload["retry"], "logs": payload["logs"], "nonce_before": _besu_nonce(), "nonce_after": _besu_nonce()}
    )


# --- fixtures -----------------------------------------------------------------

@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _limpeza(context):
    yield
    for key, batch in context.items():
        if not key.startswith("batch") or not batch:
            continue
        _update(COOP_A_FUSEKI, COOP_A_PASSWORD, f"""{_PREFIX}
        DELETE WHERE {{ ?lote agritrust:batchNumber "{batch}" . ?lote ?p ?o }}""")
        _update(LAB_FUSEKI, LAB_PASSWORD, f"""{_PREFIX}
        DELETE WHERE {{ ?a agritrust:batchNumber "{batch}" . ?a ?p ?o }} ;
        DELETE WHERE {{ ?t :anchoringBatch "{batch}" . ?t ?p ?o }}""")


# --- Cenário 1: pendente / recibo perdido / segundo retry ----------------------

@given("um laudo cadastrado no lab cuja ancoragem está marcada como pendente e que ainda NÃO existe on-chain")
def laudo_pendente_nao_onchain(context):
    batch = f"MS-2026-LABOUT1-{uuid.uuid4().hex[:8]}"
    context["batch1"] = batch
    _post_lote_ancorado(batch)  # o pai precisa existir on-chain
    _plant_laudo(batch)         # laudo direto no Fuseki do lab: NADA ancorado ainda
    _plant_pending_task(batch)


@when("o retry de ancoragem do lab roda")
def retry_roda(context):
    _run_retry(context)


@then('a transação é enviada, o laudo passa a "anchored" e o txHash é registrado')
def tx_enviada_anchored(context):
    batch = context["batch1"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "sent", f"esperado outcome 'sent', veio {result!r}"
    assert run["nonce_after"] > run["nonce_before"], "nenhuma tx enviada (nonce da conta Besu do lab não mudou)"
    task = _read_task(batch)
    assert task["status"] == "anchored", f"esperado status 'anchored', veio {task!r}"
    assert task["txHash"] and task["txHash"] == result["txHash"], (
        f"txHash não registrado na tarefa ou diferente do retornado: {task!r} vs {result!r}"
    )


@given("um laudo cuja transação já foi confirmada on-chain, mas que continua marcado como pendente (recibo perdido)")
def laudo_onchain_mas_pendente(context):
    batch = f"MS-2026-LABOUT2-{uuid.uuid4().hex[:8]}"
    context["batch2"] = batch
    _post_lote_ancorado(batch)
    _post_analise_ancorada(batch)  # laudo REAL ancorado on-chain
    _plant_pending_task(batch)     # "recibo perdido": a marca volta a pending


@then('nenhuma transação é reenviada (o retry consulta getAsset primeiro, vê que o hash já bate e só marca como "anchored")')
def nenhuma_tx_reenviada(context):
    batch = context["batch2"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "already_onchain", f"esperado 'already_onchain', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx reenviada para um laudo que já estava on-chain"
    assert _read_task(batch)["status"] == "anchored"


@when("o retry do lab roda uma segunda vez")
def retry_segunda_vez(context):
    _run_retry(context)


@then("não há nada a processar no lab e nada é reenviado")
def nada_a_processar(context):
    # GLOBAL: vale para o Fuseki do lab inteiro. Os testes antigos de batchNumber
    # fixo deixam tarefas pending/mismatch não-órfãs no lab; é o conftest.py que
    # as limpa (allowlist, antes e depois de cada teste). Se este assert falhar
    # com uma tarefa de batchNumber FORA da allowlist, o teste novo que a criou
    # precisa adicionar o prefixo lá.
    run = context["runs"][-1]
    assert run["body"].get("processed") == 0 and run["body"].get("results") == [], (
        f"esperado processed=0/results=[], veio {run['body']!r}"
    )
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num retry sem pendências"


# --- Cenário 2: mismatch --------------------------------------------------------

@given("um laudo ancorado on-chain cujo conteúdo no Fuseki do lab foi alterado depois da ancoragem, com a marca pendente")
def laudo_ancorado_e_adulterado(context):
    batch = f"MS-2026-LABOUT3-{uuid.uuid4().hex[:8]}"
    context["batch3"] = batch
    _post_lote_ancorado(batch)
    _post_analise_ancorada(batch)  # on-chain com hash H1
    # troca um valor do laudo no Fuseki (hasMoistureContent é parte do CONSTRUCT) -> H2 != H1
    analise = f"{BEE_NS}Analise_{_slug(batch)}"
    _update(LAB_FUSEKI, LAB_PASSWORD, f"""{_PREFIX}
    DELETE WHERE {{ <{analise}> :hasMoistureContent ?m }} ;
    INSERT DATA {{ <{analise}> :hasMoistureContent "19.9"^^xsd:decimal }}""")
    _plant_pending_task(batch)


@then('o resultado do laudo é "mismatch", a tarefa não vira "anchored" e nenhuma transação é enviada')
def resultado_mismatch(context):
    batch = context["batch3"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "mismatch", f"esperado 'mismatch', veio {result!r}"
    assert "!=" in result.get("detail", ""), f"mismatch sem detalhe dos hashes: {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num laudo com mismatch"
    assert _read_task(batch)["status"] == "mismatch", "a tarefa em mismatch nunca pode virar 'anchored'"
    context["mismatch_detail"] = result["detail"]


@when("o retry do lab roda novamente")
def retry_novamente(context):
    _run_retry(context)


@then('o laudo continua reportado como "mismatch", a tarefa segue "mismatch" e nada é reenviado')
def continua_mismatch(context):
    batch = context["batch3"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "mismatch", f"mismatch deveria continuar reportado, veio {result!r}"
    assert result["detail"] == context["mismatch_detail"], "detalhe do mismatch mudou entre retries"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num laudo com mismatch"
    assert _read_task(batch)["status"] == "mismatch"


# --- Cenário 3: pai ainda não ancorado -------------------------------------------

@given("uma tarefa pending de um laudo cujo lote (pai) ainda não foi ancorado on-chain")
def laudo_com_pai_nao_ancorado(context):
    batch = f"MS-2026-LABOUT4-{uuid.uuid4().hex[:8]}"
    context["batch4"] = batch
    _plant_lote_sem_ancorar(batch)  # o lote existe no coop-a (productType resolvível), mas NÃO on-chain
    _plant_laudo(batch)
    _plant_pending_task(batch)


@then('o resultado é "waiting_parent", nenhuma transação é enviada e a tarefa continua pending')
def resultado_waiting_parent(context):
    batch = context["batch4"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "waiting_parent", f"esperado 'waiting_parent', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada com o pai ainda não ancorado"
    assert _read_task(batch)["status"] == "pending", "a tarefa deve continuar 'pending' esperando o pai"


# --- Cenário 4: in_flight ----------------------------------------------------------
# Mesma exceção documentada do lote (ancoragem_outbox.feature): a janela "tx em voo"
# da chain real (bloco de 2s) é menor que a latência do retry até o envio, então só
# a resposta do nó ao envio é simulada; o resto (Fuseki, CONSTRUCT, getAsset,
# estimate_gas, anchor_lab_analysis, o retry do lab) é real.

@given("uma tarefa pending de um laudo cuja transação está em voo (o envio responde Known transaction)")
def laudo_pending_tx_em_voo(context):
    batch = f"MS-2026-LABOUT5-{uuid.uuid4().hex[:8]}"
    context["batch5"] = batch
    _post_lote_ancorado(batch)  # pai on-chain: o estimate_gas passa e o envio é alcançado
    _plant_laudo(batch)
    _plant_pending_task(batch)


@when("o retry de ancoragem do lab roda com o envio respondendo Known transaction")
def retry_com_known_transaction(context):
    _run_retry_injected(context)


@then("a resposta é outcome in_flight, nenhum ERROR é logado e a tarefa do laudo continua pending")
def resposta_in_flight(context):
    batch = context["batch5"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "in_flight", f"esperado 'in_flight' (Known transaction), veio {result!r}"
    errors = [log for log in run["logs"] if log["level"] == "ERROR"]
    assert not errors, f"ERROR logado para um caso em voo: {errors!r}"
    assert _read_task(batch)["status"] == "pending"


@when("a transação em voo do laudo é minerada e o retry do lab roda novamente")
def laudo_minerado_e_retry(context):
    batch = context["batch5"]
    ok, tx_hash, err = _lab_module("anchoring.py", "anchoring").anchor_lab_analysis(
        batch, "HoneyApis", _laudo_data_hash(batch)
    )
    assert ok, f"não consegui ancorar de verdade o laudo para simular a mineração: {err}"
    _run_retry(context)


@then('a tarefa do laudo vira "anchored" sem reenviar nada')
def tarefa_anchored_sem_reenvio(context):
    batch = context["batch5"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "already_onchain", f"esperado 'already_onchain', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx reenviada para um laudo já minerado"
    assert _read_task(batch)["status"] == "anchored"


# --- Cenário 5: natureId indisponível (productType não resolvível) ------------------
# O natureId vem da consulta federada ao Fuseki do coop-a/coop-b. Simulado SEM
# derrubar nenhum serviço: o lote está ancorado on-chain (pai existe), mas é
# apagado do Fuseki do coop-a, então a consulta federada não o encontra.

@given("uma tarefa pending de um laudo cujo lote está ancorado on-chain mas não é encontrado em nenhuma cooperativa")
def laudo_sem_natureid(context):
    batch = f"MS-2026-LABOUT6-{uuid.uuid4().hex[:8]}"
    context["batch6"] = batch
    _post_lote_ancorado(batch)  # pai on-chain
    _update(COOP_A_FUSEKI, COOP_A_PASSWORD, f"""{_PREFIX}
    DELETE WHERE {{ ?lote agritrust:batchNumber "{batch}" . ?lote ?p ?o }}""")  # productType some da federação
    _plant_laudo(batch)
    _plant_pending_task(batch)


@then("o resultado é \"failed\" com o motivo natureId indisponível, nenhuma transação é enviada e a tarefa continua pending")
def failed_sem_natureid(context):
    batch = context["batch6"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "failed", f"esperado 'failed', veio {result!r}"
    assert "natureId indisponível" in result.get("error", ""), f"o motivo deveria dizer natureId indisponível: {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada sem natureId"
    assert _read_task(batch)["status"] == "pending"
