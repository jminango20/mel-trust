"""
Step defs (pytest-bdd) para tests/features/ancoragem_outbox.feature.

Roda cada cenário para coop-a E coop-b: a fixture autouse `node` é
parametrizada (id [coop-a] / [coop-b]) e todos os helpers leem URL, Fuseki
e chave Besu dela — um único arquivo de steps, sem duplicação.

DECISÕES DE DESIGN (o código de produção segue):
1. A marca de ancoragem fica num RECURSO SEPARADO, `:AnchoringTask_<slug>`,
   NUNCA no recurso do lote. Motivo: o hash do lote é o keccak do grafo
   devolvido por `_LOTE_CONSTRUCT_TEMPLATE` (lote_endpoint.py), que seleciona
   por padrão fixo (batchNumber + a ?productType + ownedBy + produtor).
   Gravar status no lote arriscaria mexer nesse conjunto; a tarefa separada
   não depende disso. ATENÇÃO: a tarefa NÃO pode usar `agritrust:batchNumber`
   para se ligar ao lote — `find_existing_lote` casa QUALQUER recurso com esse
   predicado e quebraria a idempotência de POST /lote. Usa-se
   `:anchoringBatch` (propriedade própria).
2. Retry = endpoint manual `POST /anchoring/retry` (sem scheduler na PoC).
3. O estado "pendente" é PLANTADO direto no Fuseki aqui; sem flag de falha
   forçada no código de produção.
4. POST /lote grava "pending" antes de ancorar e "anchored" depois.
5. `mismatch` (asset on-chain com hash != recalculado) nunca vira anchored e
   é REEXAMINADO (re-reportado) a cada retry, sem reenviar tx.

CONTRATO de POST /anchoring/retry (200):
  {"processed": int, "results": [{"batchNumber", "outcome":
   "sent" | "already_onchain" | "mismatch" | ..., "txHash": str|None}]}

Independência da asserção "nada reenviado": nonce da conta Besu do nó
(eth_getTransactionCount) — só sobe se uma tx for de fato enviada.

Como o mismatch é plantado SEM adulterar código de produção: POST /lote real
ancora o hash H1; depois o `rdf:type` do lote é trocado direto no Fuseki
(HoneyApis -> HoneyNativeStinglessBee). `a ?productType` faz parte do
CONSTRUCT, então o hash recalculado passa a ser H2 != H1, com o asset
on-chain intacto.
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

scenarios("../features/ancoragem_outbox.feature")

RPC_URL = "http://localhost:8545"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"
BEE_NS = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"

_PREFIX = (
    f"PREFIX : <{BEE_NS}>\n"
    "PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"
    "PREFIX foaf: <http://xmlns.com/foaf/0.1/>\n"
)


def _node_config(name: str, url: str, default_fuseki: str) -> dict:
    env = dotenv_values(REPO_ROOT / name / ".env")
    return {
        "name": name,
        "url": url,
        "fuseki": env.get("FUSEKI_URL", default_fuseki),
        "password": env.get("FUSEKI_ADMIN_PASSWORD"),
        "key_path": REPO_ROOT / name / "keys" / f"{name}-besu.key",
    }


_NODES = {
    "coop-a": _node_config("coop-a", "http://localhost:8001", "http://127.0.0.1:3030/coop-a"),
    "coop-b": _node_config("coop-b", "http://localhost:8002", "http://127.0.0.1:3031/coop-b"),
}


@pytest.fixture(params=list(_NODES), autouse=True)
def node(request):
    return _NODES[request.param]


def _update(node: dict, query: str):
    requests.post(
        f"{node['fuseki']}/update", data={"update": query},
        auth=("admin", node["password"]), timeout=20,
    ).raise_for_status()


def _select(node: dict, query: str) -> list[dict]:
    r = requests.post(
        f"{node['fuseki']}/sparql", data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", node["password"]), timeout=20,
    )
    r.raise_for_status()
    return r.json()["results"]["bindings"]


def _slug(batch: str) -> str:
    return batch.replace("-", "_")


def _plant_pending_task(node: dict, batch: str):
    """Planta (substituindo qualquer tarefa existente) a marca 'pending'."""
    t = f"{BEE_NS}AnchoringTask_{_slug(batch)}"
    _update(node, f"""{_PREFIX}
    DELETE WHERE {{ <{t}> ?p ?o }} ;
    INSERT DATA {{
      <{t}> a :AnchoringTask ;
            :anchoringBatch "{batch}" ;
            :anchoringStatus "pending" .
    }}""")


def _read_task(node: dict, batch: str) -> dict:
    rows = _select(node, f"""{_PREFIX}
    SELECT ?status ?tx WHERE {{
      ?t :anchoringBatch "{batch}" ; :anchoringStatus ?status .
      OPTIONAL {{ ?t :anchoringTxHash ?tx }}
    }}""")
    assert rows, f"nenhuma AnchoringTask encontrada para o lote {batch!r}"
    row = rows[0]
    return {"status": row["status"]["value"], "txHash": row.get("tx", {}).get("value")}


def _besu_nonce(node: dict) -> int:
    from eth_account import Account
    addr = Account.from_key(node["key_path"].read_text().strip()).address
    r = requests.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionCount", "params": [addr, "latest"]},
        timeout=10,
    )
    r.raise_for_status()
    return int(r.json()["result"], 16)


def _run_retry(node: dict, context):
    """Executa o retry e guarda {body, nonce_before, nonce_after} em context['runs']."""
    nonce_before = _besu_nonce(node)
    try:
        # Teto: o retry pode enviar várias tx; cada uma tem timeout de 60s no nó.
        resp = requests.post(f"{node['url']}/anchoring/retry", timeout=180)
    except requests.exceptions.Timeout:
        pytest.fail(
            "POST /anchoring/retry não respondeu em 180s — RPC/túnel não respondeu "
            "(túnel SSH do Besu fora ou máquina suspensa?)",
            pytrace=False,
        )
    assert resp.status_code == 200, (
        f"POST /anchoring/retry esperado 200, veio {resp.status_code}: {resp.text}"
    )
    run = {"body": resp.json(), "nonce_before": nonce_before, "nonce_after": _besu_nonce(node)}
    context.setdefault("runs", []).append(run)
    return run


def _result_for(run: dict, batch: str) -> dict:
    match = [r for r in run["body"].get("results", []) if r.get("batchNumber") == batch]
    assert len(match) == 1, f"esperava 1 resultado para {batch!r}, veio {match!r} (resposta: {run['body']!r})"
    return match[0]


def _post_lote_ancorado(node: dict, batch: str):
    resp = requests.post(
        f"{node['url']}/lote",
        json={"batchNumber": batch, "productType": "HoneyApis",
              "producerName": PRODUCER_NAME, "producerDID": PRODUCER_DID},
        timeout=60,
    )
    assert resp.status_code == 200, f"POST /lote esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("anchoringStatus") == "anchored", (
        f"pré-condição: lote precisa estar realmente ancorado on-chain, veio {resp.json()!r}"
    )


@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _limpeza(context, node):
    yield
    for key in ("batch1", "batch2", "batch3"):
        batch = context.get(key)
        if not batch:
            continue
        _update(node, f"""{_PREFIX}
        DELETE WHERE {{ ?lote agritrust:batchNumber "{batch}" . ?lote ?p ?o }} ;
        DELETE WHERE {{ ?t :anchoringBatch "{batch}" . ?t ?p ?o }}""")


# --- Bloco 1: pendente, ainda NÃO on-chain ---------------------------------

@given("um lote cadastrado no coop-a cuja ancoragem está marcada como pendente e que ainda NÃO existe on-chain")
def lote_pendente_nao_onchain(node, context):
    batch = f"MS-2026-OUTBOX1-{uuid.uuid4().hex[:8]}"
    context["batch1"] = batch
    # Planta o lote DIRETO no Fuseki (sem passar por POST /lote, que ancoraria).
    rows = _select(node, f"""{_PREFIX}
    SELECT ?p WHERE {{ ?p agritrust:hasDID "{PRODUCER_DID}" }} LIMIT 1""")
    if rows:
        producer = rows[0]["p"]["value"]
        producer_triples = ""
    else:
        producer = f"{BEE_NS}Produtor_outbox_{uuid.uuid4().hex[:8]}"
        producer_triples = (
            f'<{producer}> a agritrust:DataProducer ; foaf:name "{PRODUCER_NAME}" ; '
            f'agritrust:hasDID "{PRODUCER_DID}" .'
        )
    _update(node, f"""{_PREFIX}
    INSERT DATA {{
      {producer_triples}
      <{BEE_NS}Lote_{_slug(batch)}> a :HoneyApis ;
          agritrust:ownedBy <{producer}> ;
          agritrust:batchNumber "{batch}" .
    }}""")
    _plant_pending_task(node, batch)


@when("o retry de ancoragem roda")
def retry_roda(node, context):
    _run_retry(node, context)


@then('a transação é enviada, o lote passa a "anchored" e o txHash é registrado')
def tx_enviada_anchored(node, context):
    batch = context["batch1"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "sent", f"esperado outcome 'sent', veio {result!r}"
    assert run["nonce_after"] > run["nonce_before"], "nenhuma tx enviada (nonce da conta Besu não mudou)"
    task = _read_task(node, batch)
    assert task["status"] == "anchored", f"esperado status 'anchored', veio {task!r}"
    assert task["txHash"] and task["txHash"] == result["txHash"], (
        f"txHash não registrado na tarefa ou diferente do retornado: {task!r} vs {result!r}"
    )


# --- Bloco 2: já on-chain, recibo perdido ----------------------------------

@given("um lote cuja transação já foi confirmada on-chain, mas que continua marcado como pendente (recibo perdido)")
def lote_onchain_mas_pendente(node, context):
    batch = f"MS-2026-OUTBOX2-{uuid.uuid4().hex[:8]}"
    context["batch2"] = batch
    _post_lote_ancorado(node, batch)
    # Simula "recibo perdido": o asset existe on-chain, mas a marca voltou a pending.
    _plant_pending_task(node, batch)


@then('nenhuma transação é reenviada (o retry consulta getAsset primeiro, vê que o hash já bate e só marca como "anchored")')
def nenhuma_tx_reenviada(node, context):
    batch = context["batch2"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "already_onchain", f"esperado 'already_onchain', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], (
        "tx reenviada: nonce da conta Besu subiu num lote que já estava on-chain"
    )
    task = _read_task(node, batch)
    assert task["status"] == "anchored", f"esperado status 'anchored', veio {task!r}"


# --- Bloco 3: segundo retry, nada a fazer ----------------------------------

@when("o retry roda uma segunda vez")
def retry_segunda_vez(node, context):
    _run_retry(node, context)


@then("não há nada a processar e nada é reenviado")
def nada_a_processar(context):
    run = context["runs"][-1]
    assert run["body"].get("processed") == 0 and run["body"].get("results") == [], (
        f"esperado processed=0/results=[], veio {run['body']!r}"
    )
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num retry sem pendências"


# --- Cenário 2: mismatch ---------------------------------------------------

@given("um lote ancorado on-chain cujo conteúdo no Fuseki foi alterado depois da ancoragem, com a marca pendente")
def lote_ancorado_e_adulterado(node, context):
    batch = f"MS-2026-OUTBOX3-{uuid.uuid4().hex[:8]}"
    context["batch3"] = batch
    _post_lote_ancorado(node, batch)  # on-chain com hash H1
    lote = f"{BEE_NS}Lote_{_slug(batch)}"
    # Muda o rdf:type do lote (parte do CONSTRUCT) -> hash recalculado H2 != H1.
    _update(node, f"""{_PREFIX}
    DELETE DATA {{ <{lote}> a :HoneyApis }} ;
    INSERT DATA {{ <{lote}> a :HoneyNativeStinglessBee }}""")
    _plant_pending_task(node, batch)


@then('o resultado do lote é "mismatch", a tarefa não vira "anchored" e nenhuma transação é enviada')
def resultado_mismatch(node, context):
    batch = context["batch3"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "mismatch", f"esperado 'mismatch', veio {result!r}"
    assert "!=" in result.get("detail", ""), f"mismatch sem detalhe dos hashes: {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num lote com mismatch"
    task = _read_task(node, batch)
    assert task["status"] == "mismatch", f"esperado status 'mismatch' (nunca 'anchored'), veio {task!r}"
    context["mismatch_detail"] = result["detail"]


@when("o retry roda novamente")
def retry_novamente(node, context):
    _run_retry(node, context)


@then('o lote continua reportado como "mismatch", a tarefa segue "mismatch" e nada é reenviado')
def continua_mismatch(node, context):
    batch = context["batch3"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "mismatch", f"mismatch deveria continuar reportado, veio {result!r}"
    assert result["detail"] == context["mismatch_detail"], "detalhe do mismatch mudou entre retries"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num lote com mismatch"
    assert _read_task(node, batch)["status"] == "mismatch"
