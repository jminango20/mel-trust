"""
Step defs (pytest-bdd) para tests/features/ancoragem_outbox_certifier.feature —
outbox de ancoragem da VC DE CERTIFICAÇÃO no certifier (POST /credential).

FASE RED (TDD): escrito ANTES de qualquer código de outbox em certifier/. Hoje o
certifier não grava marca de ancoragem nem tem POST /anchoring/retry.

O QUE É ANCORADO HOJE (certifier/credential_endpoint.py:588, confirmado antes de
escrever): `Web3.keccak(text=sdjwt.sd_jwt_issuance)` — keccak256 do SD-JWT COMPLETO
serializado (`<Credential-JWT>~<disclosure1>~...~`, com o `~` final), o mesmo
artefato entregue ao holder. Cada disclosure leva um salt aleatório, então esse
hash NÃO é reconstruível a partir de nenhum dado de nenhum Fuseki. Por isso o
retry reenvia o valor GUARDADO, sem recalcular nada.

DECISÕES DE DESIGN (o código de produção do certifier deve seguir):
1. A AnchoringTask fica num RECURSO SEPARADO no Fuseki do certifier,
   `:AnchoringTask_<slug(batchNumber)>`, e guarda, no momento da EMISSÃO:
     :anchoringBatch    "<batchNumber>"
     :anchoringStatus   "pending" | "anchored" | "mismatch"
     :anchoringDataHash "0x<64 hex>"  (keccak256 do SD-JWT completo emitido)
     :anchoringNature   "<productType>" (ex.: HoneyApis) — o natureId da tx
     :anchoringTxHash   "0x..."        (quando anchored)
   Guardar o natureId evita que o retry dependa de consulta federada (coop-a/
   coop-b/lab) — diferente do outbox do lab. A tarefa NÃO fica no recurso de
   nada hasheado (não há recurso: o hash é do SD-JWT).
2. assetId do documento = keccak256(batchNumber + "-certification"); pai =
   keccak256(batchNumber).
3. Retry manual: `POST /anchoring/retry`; mesmo contrato dos outros nós:
   {"processed": n, "results": [{"batchNumber", "outcome": "sent" |
   "already_onchain" | "mismatch" | "waiting_parent" | "in_flight" | "failed"...,
   "txHash"}]}.
4. Mesma lógica de in_flight do lote/lab: se o envio falha, reconsulta getAsset;
   existe => resolve; não existe + "Known transaction" => in_flight; outro erro =>
   failed.
5. Se o pai (keccak256(batchNumber)) não existe on-chain: waiting_parent, sem enviar.
6. ORDEM em POST /credential: monta e assina o SD-JWT -> grava a tarefa `pending`
   (com hash + natureId) -> ancora -> `anchored`.
LIMITAÇÃO REGISTRADA: se a tarefa for PERDIDA (apagada, ou o processo cair entre
assinar o SD-JWT e gravá-la), o hash NÃO é recuperável — não há dado a partir do
qual recalculá-lo (salts aleatórios). A VC já foi entregue ao holder, mas ficará
sem ancoragem até uma nova emissão.

Como cada estado é plantado (sem flag de falha no código de produção):
- "ainda não existe on-chain": tarefa plantada direto no Fuseki do certifier com
  um hash sintético conhecido; só o pai (lote) é ancorado de verdade (POST /lote);
- "recibo perdido" e "mismatch": POST /credential REAL (fluxo completo), e SÓ o
  status (ou o hash guardado, no mismatch) é alterado no Fuseki — o hash
  verdadeiro vem da emissão real e é conferido contra keccak256(credential) da
  resposta, calculado pelo próprio teste, não pelo helper do retry;
- "in_flight": só a resposta do nó ao envio é simulada
  (tests/support/run_retry_injected.py), mesma exceção dos outros nós.
"Nada reenviado" é verificado pelo nonce da conta Besu do certifier.
"""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from jwcrypto.jwk import JWK
from pytest_bdd import given, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
from load_node_module import load_node_module  # noqa: E402
from scripts.test_credential_flow import GRANT_TYPE, build_proof_jwt  # noqa: E402

scenarios("../features/ancoragem_outbox_certifier.feature")

COOP_A_URL = "http://127.0.0.1:8001"
LAB_URL = "http://127.0.0.1:8005"
CERTIFIER_URL = "http://127.0.0.1:8003"
RPC_URL = "http://127.0.0.1:8545"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"
BEE_NS = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"
BESU_KEY_PATH = REPO_ROOT / "certifier" / "keys" / "certifier-besu.key"
HOLDER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"

_cert_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERT_FUSEKI = _cert_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERT_PASSWORD = _cert_env.get("FUSEKI_ADMIN_PASSWORD")
_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")
_lab_env = dotenv_values(REPO_ROOT / "lab" / ".env")
LAB_FUSEKI = _lab_env.get("FUSEKI_URL", "http://127.0.0.1:3033/lab")
LAB_PASSWORD = _lab_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = (
    f"PREFIX : <{BEE_NS}>\n"
    "PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"
)


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


def _hex(b: bytes) -> str:
    return "0x" + bytes(b).hex()


# --- tarefa (no Fuseki do CERTIFIER) -----------------------------------------

def _plant_task(batch: str, data_hash: bytes, status: str = "pending", nature: str = "HoneyApis"):
    """Planta (substituindo qualquer tarefa existente) a marca com hash + natureId guardados."""
    t = f"{BEE_NS}AnchoringTask_{_slug(batch)}"
    _update(CERT_FUSEKI, CERT_PASSWORD, f"""{_PREFIX}
    DELETE WHERE {{ <{t}> ?p ?o }} ;
    INSERT DATA {{
      <{t}> a :AnchoringTask ;
            :anchoringBatch "{batch}" ;
            :anchoringStatus "{status}" ;
            :anchoringDataHash "{_hex(data_hash)}" ;
            :anchoringNature "{nature}" .
    }}""")


def _read_task(batch: str) -> dict:
    rows = _select(CERT_FUSEKI, CERT_PASSWORD, f"""{_PREFIX}
    SELECT ?status ?tx ?hash ?nature WHERE {{
      ?t :anchoringBatch "{batch}" ; :anchoringStatus ?status .
      OPTIONAL {{ ?t :anchoringTxHash ?tx }}
      OPTIONAL {{ ?t :anchoringDataHash ?hash }}
      OPTIONAL {{ ?t :anchoringNature ?nature }}
    }}""")
    assert rows, f"nenhuma AnchoringTask encontrada no Fuseki do certifier para o lote {batch!r}"
    r = rows[0]
    return {
        "status": r["status"]["value"],
        "txHash": r.get("tx", {}).get("value"),
        "dataHash": r.get("hash", {}).get("value"),
        "nature": r.get("nature", {}).get("value"),
    }


def _set_task_status(batch: str, status: str):
    _update(CERT_FUSEKI, CERT_PASSWORD, f"""{_PREFIX}
    DELETE {{ ?t :anchoringStatus ?s . ?t :anchoringTxHash ?x }}
    INSERT {{ ?t :anchoringStatus "{status}" }}
    WHERE  {{ ?t :anchoringBatch "{batch}" ; :anchoringStatus ?s . OPTIONAL {{ ?t :anchoringTxHash ?x }} }}""")


def _set_task_hash(batch: str, data_hash: bytes):
    _update(CERT_FUSEKI, CERT_PASSWORD, f"""{_PREFIX}
    DELETE {{ ?t :anchoringDataHash ?h }}
    INSERT {{ ?t :anchoringDataHash "{_hex(data_hash)}" }}
    WHERE  {{ ?t :anchoringBatch "{batch}" ; :anchoringDataHash ?h }}""")


# --- pai (lote no coop-a) e laudo (lab), VC real -----------------------------------

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


def _pedir_credencial(batch: str) -> requests.Response:
    """pre-authorized-code -> /token -> /nonce -> POST /credential, devolvendo a resposta
    CRUA de POST /credential (o chamador decide o que esperar: 200 na emissão, 409 na reemissão)."""
    lookup = requests.post(f"{CERTIFIER_URL}/lote/{batch}/pre-authorized-code", timeout=20)
    assert lookup.status_code == 200, f"pre-authorized-code esperado 200, veio {lookup.status_code}: {lookup.text}"
    token = requests.post(
        f"{CERTIFIER_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": lookup.json()["pre_authorized_code"]},
        timeout=20,
    )
    assert token.status_code == 200, f"/token esperado 200, veio {token.status_code}: {token.text}"

    meta = requests.get(f"{CERTIFIER_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))
    c_nonce = requests.post(f"{CERTIFIER_URL}/nonce", timeout=10).json()["c_nonce"]
    proof = build_proof_jwt(JWK.from_pem(HOLDER_KEY_PATH.read_bytes()), meta["credential_issuer"], c_nonce)

    return requests.post(
        f"{CERTIFIER_URL}/credential",
        json={"credential_configuration_id": config_id,
              "proof": {"proof_type": "jwt", "jwt": proof}, "batch_number": batch},
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        timeout=60,
    )


def _emitir_vc_real(batch: str) -> dict:
    """Fluxo COMPLETO e real (mesmo de test_ancoragem_certificacao.py): lote (coop-a) ->
    laudo (lab) -> pre-authorized-code -> /token -> /nonce -> POST /credential."""
    _post_lote_ancorado(batch)
    analise = requests.post(
        f"{LAB_URL}/analise",
        json={"batchNumber": batch, "moistureContent": 18.5, "hmfContent": 12.3,
              "acidityContent": 30.0, "diastaseActivity": 10.0},
        timeout=60,
    )
    assert analise.status_code == 200, f"POST /analise esperado 200, veio {analise.status_code}: {analise.text}"

    cred = _pedir_credencial(batch)
    assert cred.status_code == 200, f"POST /credential esperado 200, veio {cred.status_code}: {cred.text}"
    body = cred.json()
    assert body.get("anchoringStatus") == "anchored", (
        f"pré-condição: a VC precisa estar realmente ancorada on-chain, veio {body.get('anchoringStatus')!r}: "
        f"{body.get('anchoringError')!r}"
    )
    return body


# --- chain -------------------------------------------------------------------

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


def _module(node: str, filename: str, alias: str):
    """Carrega `<nó>/<filename>` (isolado, ver tests/support/load_node_module.py)."""
    return load_node_module(node, filename, alias)


def _onchain_cert_hash(batch: str) -> tuple[bool, bytes | None]:
    """Leitura INDEPENDENTE do código do certifier: usa o leitor genérico por assetId do
    lab/anchoring.py com assetId = keccak256(batch + "-certification")."""
    lab = _module("lab", "anchoring.py", "anchoring_ro")
    return lab._get_onchain_data_hash_raw(Web3.keccak(text=f"{batch}-certification"))


# --- execução do retry --------------------------------------------------------

def _run_retry(context):
    nonce_before = _besu_nonce()
    try:
        resp = requests.post(f"{CERTIFIER_URL}/anchoring/retry", timeout=180)
    except requests.exceptions.Timeout:
        pytest.fail("POST /anchoring/retry (certifier) não respondeu em 180s — RPC/túnel não respondeu?", pytrace=False)
    assert resp.status_code == 200, f"POST /anchoring/retry esperado 200, veio {resp.status_code}: {resp.text}"
    context.setdefault("runs", []).append(
        {"body": resp.json(), "nonce_before": nonce_before, "nonce_after": _besu_nonce()}
    )


def _result_for(run: dict, batch: str) -> dict:
    match = [r for r in run["body"].get("results", []) if r.get("batchNumber") == batch]
    assert len(match) == 1, f"esperava 1 resultado para {batch!r}, veio {match!r} (resposta: {run['body']!r})"
    return match[0]


def _run_retry_injected(context):
    """Retry real do certifier (`credential_endpoint.retry_anchoring`) com o envio respondendo
    'Known transaction' (ver tests/support/run_retry_injected.py)."""
    py = REPO_ROOT / "certifier" / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.run(
        [str(py), str(REPO_ROOT / "tests" / "support" / "run_retry_injected.py"), "credential_endpoint"],
        cwd=REPO_ROOT / "certifier", capture_output=True, text=True, timeout=180,
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
        _update(CERT_FUSEKI, CERT_PASSWORD, f"""{_PREFIX}
        DELETE WHERE {{ ?s agritrust:batchNumber "{batch}" . ?s ?p ?o }} ;
        DELETE WHERE {{ ?t :anchoringBatch "{batch}" . ?t ?p ?o }}""")


def _synthetic_hash(batch: str, tag: str) -> bytes:
    return bytes(Web3.keccak(text=f"sd-jwt-sintetico-{tag}-{batch}"))


# --- Cenário 1: pending com hash guardado, asset ainda não existe --------------------

@given("uma tarefa pending da VC de certificação com hash guardado, cujo asset ainda não existe on-chain")
def tarefa_pending_hash_guardado(context):
    batch = f"MS-2026-CERTOUT1-{uuid.uuid4().hex[:8]}"
    context["batch1"] = batch
    _post_lote_ancorado(batch)  # o pai precisa existir on-chain; a VC NÃO é ancorada
    context["stored_hash1"] = _synthetic_hash(batch, "guardado")
    _plant_task(batch, context["stored_hash1"])


@when("o retry de ancoragem do certifier roda")
def retry_roda(context):
    _run_retry(context)


@then('a transação é enviada com o hash guardado, a tarefa passa a "anchored" e o txHash é registrado')
def tx_enviada_com_hash_guardado(context):
    batch = context["batch1"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "sent", f"esperado outcome 'sent', veio {result!r}"
    assert run["nonce_after"] > run["nonce_before"], "nenhuma tx enviada (nonce da conta Besu do certifier não mudou)"
    exists, onchain = _onchain_cert_hash(batch)
    assert exists and onchain == context["stored_hash1"], (
        f"o asset on-chain deveria ter o hash GUARDADO {_hex(context['stored_hash1'])}, veio {exists=} {onchain!r}"
    )
    task = _read_task(batch)
    assert task["status"] == "anchored", f"esperado status 'anchored', veio {task!r}"
    assert task["txHash"] and task["txHash"] == result["txHash"], (
        f"txHash não registrado na tarefa ou diferente do retornado: {task!r} vs {result!r}"
    )


# --- Cenário 2: recibo perdido + segundo retry --------------------------------------------

@given("uma VC de certificação emitida de verdade por POST /credential e ancorada on-chain, com a tarefa devolvida para pending")
def vc_real_com_tarefa_pending(context):
    batch = f"MS-2026-CERTOUT2-{uuid.uuid4().hex[:8]}"
    context["batch2"] = batch
    body = _emitir_vc_real(batch)  # VC REAL, ancorada de verdade por POST /credential
    real_hash = _hex(Web3.keccak(text=body["credential"]))  # calculado AQUI, pelo teste
    task = _read_task(batch)    # POST /credential real precisa ter gravado a tarefa
    assert task["dataHash"] == real_hash, (
        f"a tarefa deveria guardar keccak256 do SD-JWT emitido ({real_hash}), guardou {task['dataHash']!r}"
    )
    assert task["nature"] == "HoneyApis", f"a tarefa deveria guardar o natureId, guardou {task['nature']!r}"
    _set_task_status(batch, "pending")  # "recibo perdido": só o status volta a pending


@then('nenhuma transação é reenviada (o retry consulta getAsset primeiro, vê que o hash guardado já bate e só marca como "anchored")')
def nenhuma_tx_reenviada(context):
    batch = context["batch2"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "already_onchain", f"esperado 'already_onchain', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx reenviada para uma VC que já estava on-chain"
    assert _read_task(batch)["status"] == "anchored"


@when("o retry do certifier roda uma segunda vez")
def retry_segunda_vez(context):
    _run_retry(context)


@then("não há nada a processar no certifier e nada é reenviado")
def nada_a_processar(context):
    run = context["runs"][-1]
    assert run["body"].get("processed") == 0 and run["body"].get("results") == [], (
        f"esperado processed=0/results=[], veio {run['body']!r}"
    )
    assert run["nonce_after"] == run["nonce_before"], "tx enviada num retry sem pendências"


# --- Cenário 3: mismatch ------------------------------------------------------------

@given("uma VC de certificação ancorada on-chain cuja tarefa guarda um hash DIFERENTE do ancorado, marcada como pending")
def vc_com_hash_guardado_diferente(context):
    batch = f"MS-2026-CERTOUT3-{uuid.uuid4().hex[:8]}"
    context["batch3"] = batch
    body = _emitir_vc_real(batch)  # on-chain com o hash REAL H1
    assert _read_task(batch)["dataHash"] == _hex(Web3.keccak(text=body["credential"]))
    _set_task_hash(batch, _synthetic_hash(batch, "diferente"))  # guardado H2 != H1
    _set_task_status(batch, "pending")


@then('o resultado da VC é "mismatch", a tarefa não vira "anchored" e nenhuma transação é enviada')
def resultado_mismatch(context):
    batch = context["batch3"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "mismatch", f"esperado 'mismatch', veio {result!r}"
    assert "!=" in result.get("detail", ""), f"mismatch sem detalhe dos hashes: {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada numa VC com mismatch"
    assert _read_task(batch)["status"] == "mismatch", "a tarefa em mismatch nunca pode virar 'anchored'"
    context["mismatch_detail"] = result["detail"]


@when("o retry do certifier roda novamente")
def retry_novamente(context):
    _run_retry(context)


@then('a VC continua reportada como "mismatch", a tarefa segue "mismatch" e nada é reenviado')
def continua_mismatch(context):
    batch = context["batch3"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "mismatch", f"mismatch deveria continuar reportado, veio {result!r}"
    assert result["detail"] == context["mismatch_detail"], "detalhe do mismatch mudou entre retries"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada numa VC com mismatch"
    assert _read_task(batch)["status"] == "mismatch"


# --- Cenário 4: pai ainda não ancorado -----------------------------------------------

@given("uma tarefa pending da VC de certificação cujo lote (pai) ainda não foi ancorado on-chain")
def tarefa_com_pai_nao_ancorado(context):
    batch = f"MS-2026-CERTOUT4-{uuid.uuid4().hex[:8]}"
    context["batch4"] = batch
    # nenhum lote em lugar nenhum: o natureId vem GUARDADO na tarefa, o retry não consulta ninguém
    _plant_task(batch, _synthetic_hash(batch, "guardado"))


@then('o resultado é "waiting_parent", nenhuma transação é enviada e a tarefa continua pending')
def resultado_waiting_parent(context):
    batch = context["batch4"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "waiting_parent", f"esperado 'waiting_parent', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx enviada com o pai ainda não ancorado"
    assert _read_task(batch)["status"] == "pending", "a tarefa deve continuar 'pending' esperando o pai"


# --- Cenário 5: in_flight ---------------------------------------------------------------
# Mesma exceção documentada dos outros nós: a janela "tx em voo" da chain real (bloco de 2s) é
# menor que a latência do retry até o envio, então só a resposta do nó ao envio é simulada; o
# resto (Fuseki, getAsset, estimate_gas, anchor_certification, o retry do certifier) é real.

@given("uma tarefa pending da VC de certificação cuja transação está em voo (o envio responde Known transaction)")
def tarefa_pending_tx_em_voo(context):
    batch = f"MS-2026-CERTOUT5-{uuid.uuid4().hex[:8]}"
    context["batch5"] = batch
    _post_lote_ancorado(batch)  # pai on-chain: o estimate_gas passa e o envio é alcançado
    context["stored_hash5"] = _synthetic_hash(batch, "guardado")
    _plant_task(batch, context["stored_hash5"])


@when("o retry de ancoragem do certifier roda com o envio respondendo Known transaction")
def retry_com_known_transaction(context):
    _run_retry_injected(context)


@then("a resposta é outcome in_flight, nenhum ERROR é logado e a tarefa da VC continua pending")
def resposta_in_flight(context):
    batch = context["batch5"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "in_flight", f"esperado 'in_flight' (Known transaction), veio {result!r}"
    errors = [log for log in run["logs"] if log["level"] == "ERROR"]
    assert not errors, f"ERROR logado para um caso em voo: {errors!r}"
    assert _read_task(batch)["status"] == "pending"


@when("a transação em voo da VC é minerada e o retry do certifier roda novamente")
def vc_minerada_e_retry(context):
    batch = context["batch5"]
    ok, tx_hash, err = _module("certifier", "anchoring.py", "anchoring").anchor_certification(
        batch, "HoneyApis", context["stored_hash5"]
    )
    assert ok, f"não consegui ancorar de verdade para simular a mineração: {err}"
    _run_retry(context)


@then('a tarefa da VC vira "anchored" sem reenviar nada')
def tarefa_anchored_sem_reenvio(context):
    batch = context["batch5"]
    run = context["runs"][-1]
    result = _result_for(run, batch)
    assert result["outcome"] == "already_onchain", f"esperado 'already_onchain', veio {result!r}"
    assert run["nonce_after"] == run["nonce_before"], "tx reenviada para uma VC já minerada"
    assert _read_task(batch)["status"] == "anchored"


# --- Cenário 6: reemissão bloqueada -----------------------------------------------------
# Um documento de certificação por lote (assetId = keccak256(batch + "-certification")).
# Reemitir geraria outro SD-JWT (salts novos => hash novo) que não pode virar um segundo
# asset nem atualizar o primeiro sem um processo UPDATE_ASSET; então POST /credential para
# um lote que já tem certificação emitida responde 409, sem emitir VC e sem tocar na tarefa.

def _count_tasks(batch: str) -> int:
    rows = _select(CERT_FUSEKI, CERT_PASSWORD, f"""{_PREFIX}
    SELECT (COUNT(?t) AS ?n) WHERE {{ ?t :anchoringBatch "{batch}" }}""")
    return int(rows[0]["n"]["value"])


@given("um lote que já tem certificação emitida (AnchoringTask existente)")
def lote_com_certificacao_emitida(context):
    batch = f"MS-2026-CERTOUT6-{uuid.uuid4().hex[:8]}"
    context["batch6"] = batch
    context["first_vc"] = _emitir_vc_real(batch)["credential"]  # emissão REAL e ancorada
    context["tasks_before"] = _count_tasks(batch)


@when("POST /credential é chamado de novo para o mesmo lote")
def credential_de_novo(context):
    context["nonce_before_2nd"] = _besu_nonce()
    context["second_response"] = _pedir_credencial(context["batch6"])
    context["nonce_after_2nd"] = _besu_nonce()


@then("responde 409, não emite nova VC e não cria nova AnchoringTask")
def responde_409_sem_nova_vc(context):
    resp = context["second_response"]
    assert resp.status_code == 409, (
        f"esperado 409 na reemissão (lote já certificado), veio {resp.status_code}: {resp.text[:300]}"
    )
    assert "credential" not in resp.json(), "a reemissão bloqueada não pode devolver uma VC"
    assert context["nonce_after_2nd"] == context["nonce_before_2nd"], "tx enviada numa reemissão bloqueada"
    batch = context["batch6"]
    assert context["tasks_before"] == 1, f"o lote já certificado deveria ter 1 AnchoringTask, tem {context['tasks_before']}"
    assert _count_tasks(batch) == context["tasks_before"], "a reemissão bloqueada criou/alterou AnchoringTask"

