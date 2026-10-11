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
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
from load_node_module import load_node_module  # noqa: E402

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
    for key in ("batch1", "batch2", "batch3", "batch4", "batch5"):
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


# --- Cenário 3: transação em voo (Known transaction) -----------------------
# EXCEÇÃO DOCUMENTADA (injeção de erro): tentei reproduzir com a chain real e
# não é determinístico. O Besu mina a cada 2s, mas o retry leva ~2,5s só para
# chegar ao envio (Fuseki + CONSTRUCT + getAsset; medido: tx enviada +0,5s após o
# início do bloco, retry HTTP começando em +1,0s e respondendo em +3,4s), então
# `getAsset` já enxerga a tx minerada ("already_onchain") e a janela "em voo"
# some. Por isso o ÚNICO ponto simulado é a resposta do nó ao envio:
# tests/support/run_retry_injected.py roda `retry_anchoring()` real do nó, em
# processo próprio (venv do nó), com `Eth.send_raw_transaction` respondendo
# "Known transaction" (texto idêntico ao observado na chain real). Fuseki,
# CONSTRUCT, hash, getAsset on-chain, estimate_gas e anchor_batch (inclusive o
# que ele loga) são reais; o contrato da resposta é o mesmo do endpoint, mas o
# transporte HTTP é pulado nesta passada. A passada seguinte ("minerada") é
# real, via HTTP.

_CONSTRUCT_TEMPLATE = """
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
CONSTRUCT {
  ?producer a agritrust:DataProducer .
  ?producer foaf:name ?producerName .
  ?producer agritrust:hasDID ?producerDID .
  ?lote a ?productType .
  ?lote agritrust:ownedBy ?producer .
  ?lote agritrust:batchNumber ?batchNumber .
}
WHERE {
  ?lote agritrust:batchNumber "%(b)s" ;
        a ?productType ;
        agritrust:ownedBy ?producer .
  BIND("%(b)s" AS ?batchNumber)
  ?producer a agritrust:DataProducer ;
            agritrust:hasDID ?producerDID .
  OPTIONAL { ?producer foaf:name ?producerName }
}
"""  # igual a `_LOTE_CONSTRUCT_TEMPLATE` de <nó>/lote_endpoint.py

_anchoring_modules: dict = {}


def _node_anchoring(node: dict):
    """Carrega `<nó>/anchoring.py` (isolado, ver tests/support/load_node_module.py)."""
    name = node["name"]
    if name not in _anchoring_modules:
        _anchoring_modules[name] = load_node_module(name, "anchoring.py", "anchoring")
    return _anchoring_modules[name]


def _rpc(method: str, params: list):
    r = requests.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=10)
    r.raise_for_status()
    return r.json()["result"]


def _besu_address(node: dict) -> str:
    from eth_account import Account
    return Account.from_key(node["key_path"].read_text().strip()).address


def _plant_lote(node: dict, batch: str, product_type: str):
    rows = _select(node, f"""{_PREFIX}
    SELECT ?p WHERE {{ ?p agritrust:hasDID "{PRODUCER_DID}" }} LIMIT 1""")
    if rows:
        producer, producer_triples = rows[0]["p"]["value"], ""
    else:
        producer = f"{BEE_NS}Produtor_outbox_{uuid.uuid4().hex[:8]}"
        producer_triples = (
            f'<{producer}> a agritrust:DataProducer ; foaf:name "{PRODUCER_NAME}" ; '
            f'agritrust:hasDID "{PRODUCER_DID}" .'
        )
    _update(node, f"""{_PREFIX}
    INSERT DATA {{
      {producer_triples}
      <{BEE_NS}Lote_{_slug(batch)}> a :{product_type} ;
          agritrust:ownedBy <{producer}> ;
          agritrust:batchNumber "{batch}" .
    }}""")
    _plant_pending_task(node, batch)


def _lote_data_hash(node: dict, batch: str) -> bytes:
    r = requests.post(
        f"{node['fuseki']}/sparql", data={"query": _CONSTRUCT_TEMPLATE % {"b": batch}},
        headers={"Accept": "application/n-triples"}, auth=("admin", node["password"]), timeout=20,
    )
    r.raise_for_status()
    lines = sorted(line for line in r.text.split("\n") if line.strip())
    return Web3.keccak(text="\n".join(lines) + "\n")


import json
import subprocess

_INJECT_SCRIPT = REPO_ROOT / "tests" / "support" / "run_retry_injected.py"


def _run_retry_injected(node: dict, context):
    """Roda o retry real do nó com o envio respondendo 'Known transaction'."""
    py = REPO_ROOT / node["name"] / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.run(
        [str(py), str(_INJECT_SCRIPT)], cwd=REPO_ROOT / node["name"],
        capture_output=True, text=True, timeout=180,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT_JSON:")]
    assert lines, f"helper de injeção não devolveu resultado (rc={proc.returncode}):\n{proc.stderr[-1500:]}"
    payload = json.loads(lines[-1][len("RESULT_JSON:"):])
    context.setdefault("runs", []).append(
        {"body": payload["retry"], "logs": payload["logs"], "nonce_before": _besu_nonce(node), "nonce_after": _besu_nonce(node)}
    )


@given("uma tarefa pending cuja transação está em voo (o envio responde Known transaction)")
def tarefa_pending_tx_em_voo(node, context):
    batch = f"MS-2026-OUTBOX4-{uuid.uuid4().hex[:8]}"
    context["batch4"] = batch
    _plant_lote(node, batch, "HoneyApis")  # lote + tarefa pending; NADA enviado on-chain ainda


@when("o retry de ancoragem roda com o envio respondendo Known transaction")
def retry_com_known_transaction(node, context):
    _run_retry_injected(node, context)


@then("a resposta é outcome in_flight, nenhum ERROR é logado e a tarefa continua pending")
def resposta_in_flight(node, context):
    batch = context["batch4"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "in_flight", (
        f"esperado outcome 'in_flight' (Known transaction = tx em voo), veio {result!r}"
    )
    # só os ERROR deste lote: o retry processa o Fuseki inteiro e loga MISMATCH
    # (de propósito, nível ERROR) de qualquer tarefa alheia em mismatch
    errors = [log for log in run["logs"] if log["level"] == "ERROR" and batch in log["msg"]]
    assert not errors, f"ERROR logado para um caso em voo: {errors!r}"
    assert _read_task(node, batch)["status"] == "pending", "a tarefa em voo deve continuar 'pending'"


@when("a transação em voo é minerada e o retry roda novamente")
def minerada_e_retry(node, context):
    batch = context["batch4"]
    mod = _node_anchoring(node)
    # a "tx em voo" finalmente é minerada: envia a ancoragem de verdade e espera o recibo
    ok, tx_hash, err = mod.anchor_batch(batch, "HoneyApis", _lote_data_hash(node, batch))
    assert ok, f"não consegui ancorar de verdade o lote para simular a mineração: {err}"
    _run_retry(node, context)


@then('a tarefa vira "anchored" sem reenviar nada')
def tarefa_anchored_sem_reenvio(node, context):
    batch = context["batch4"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "already_onchain", f"esperado 'already_onchain', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx reenviada para um lote já minerado"
    assert _read_task(node, batch)["status"] == "anchored"


# --- Cenário 4: revert real nunca é in_flight -------------------------------

@given("uma tarefa pending de um lote cujo tipo não tem processo registrado on-chain (a ancoragem reverte)")
def tarefa_pending_revert_real(node, context):
    batch = f"MS-2026-OUTBOX5-{uuid.uuid4().hex[:8]}"
    context["batch5"] = batch
    _plant_lote(node, batch, "HoneyTipoSemProcesso")


@then('o resultado é "failed" com o erro do contrato, nunca "in_flight", e a tarefa continua pending')
def revert_real_failed(node, context):
    batch = context["batch5"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "failed", f"revert real deve ser 'failed', veio {result!r}"
    assert "ContractCustomError" in result.get("error", ""), f"esperava o erro do contrato, veio {result!r}"
    assert "Known transaction" not in result.get("error", "") and "RPC/túnel" not in result.get("error", "")
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num revert"
    assert _read_task(node, batch)["status"] == "pending"
