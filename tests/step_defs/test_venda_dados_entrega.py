"""
Step defs (pytest-bdd) para tests/features/venda_dados_entrega.feature.

FASE RED (TDD): escrito e rodado ANTES de o coop-a registrar entregas no VendaDados.
Hoje POST /lote não devolve `deliveryStatus` e POST /delivery/retry não existe.

DECISÕES (contrato proposto, só coop-a):
- batchRef = keccak256(batchNumber): o MESMO bytes32 que a Besu usa como assetId
  (anchoring.py), então o lote tem uma chave única nas duas redes.
- Carteira custodial por producerDID: chave secp256k1 em
  coop-a/keys/producers/<sha256(producerDID) em hex>.key (64 hex, mesmo formato de
  coop-a-besu.key), criada pelo coop-a no primeiro uso e REUTILIZADA depois. A pasta é
  gitignored. Só redes de TESTE por enquanto. O teste planta a chave quando precisa
  saber o endereço de antemão e apaga as que criou.
- A marca de entrega fica num recurso SEPARADO no Fuseki do coop-a, como a
  AnchoringTask: `:DeliveryTask_<slug(batchNumber)>` com `:deliveryBatch` e
  `:deliveryStatus` (pending | registered | mismatch). Não entra no hash do lote.
  O lote guarda a quantidade em `:quantityMilliliters` (fonte única, já existente).
- `deliveryStatus` na resposta de POST /lote: "registered" | "skipped" (sem
  quantidade) | "pending" (falhou; a tarefa fica pendente para o retry).
- POST /delivery/retry: {"processed": n, "results": [{"batchNumber", "outcome",
  "txHash"}]}. outcome: "sent" (enviou registrarEntrega) | "already_registered"
  (entregas(batchRef) já tem o mesmo produtor e mL: só marca registered, sem tx) |
  "mismatch" (dados diferentes: nunca vira registered; reexaminado a cada retry).
  Tarefa registered não entra mais no resultado.
- Configuração do coop-a (env): VENDA_DADOS_RPC_URL (default http://127.0.0.1:8546),
  VENDA_DADOS_ADDRESS, VENDA_DADOS_CHAIN_ID (default 31337). A cooperativa assina com
  coop-a/keys/coop-a-besu.key (a mesma conta do VendaDados implantado).
- Endereço do contrato para o TESTE: env VENDA_DADOS_ADDRESS, senão
  contracts/venda-dados/deployments/local.json (escrito por scripts/deploy-local.js).

PREFLIGHT: nada aqui toca rede na importação/coleta. Cada teste confere antes o node
8546 (chainId e código no endereço). O conftest ainda exige o túnel da Besu em todo
teste (autouse); os cenários 3 e 4 não usam a Besu (lote plantado, sem POST /lote).
"""
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from eth_account import Account
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/venda_dados_entrega.feature")

COOP_A_URL = "http://localhost:8001"
PRODUCER_NAME = "Produtor Teste Entrega"
BEE = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"
_PREFIX = f"PREFIX : <{BEE}>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
FUSEKI_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

PRODUCERS_KEY_DIR = REPO_ROOT / "coop-a" / "keys" / "producers"
COOP_KEY_PATH = REPO_ROOT / "coop-a" / "keys" / "coop-a-besu.key"
DEPLOY_JSON = REPO_ROOT / "contracts" / "venda-dados" / "deployments" / "local.json"
ZERO = "0x0000000000000000000000000000000000000000"

_ABI = [
    {"type": "function", "name": "entregas", "stateMutability": "view",
     "inputs": [{"name": "", "type": "bytes32"}],
     "outputs": [{"name": "produtor", "type": "address"}, {"name": "mililitros", "type": "uint256"}]},
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "registrarEntrega", "stateMutability": "nonpayable",
     "inputs": [{"name": "produtor", "type": "address"}, {"name": "batchRef", "type": "bytes32"},
                {"name": "mililitros", "type": "uint256"}], "outputs": []},
]


def _venda_config():
    rpc = os.environ.get("VENDA_DADOS_RPC_URL", "http://127.0.0.1:8546")
    chain_id = int(os.environ.get("VENDA_DADOS_CHAIN_ID", "31337"))
    address = os.environ.get("VENDA_DADOS_ADDRESS")
    if not address and DEPLOY_JSON.exists():
        address = json.loads(DEPLOY_JSON.read_text())["vendaDados"]
    return rpc, chain_id, address


def _web3():
    rpc, _, _ = _venda_config()
    return Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))


def _venda():
    _, _, address = _venda_config()
    return _web3().eth.contract(address=Web3.to_checksum_address(address), abi=_ABI)


def _porta_aberta():
    rpc, _, _ = _venda_config()
    try:
        _web3().eth.chain_id
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(autouse=True)
def _venda_node_alive(request):
    """Preflight do node do VendaDados (não toca a rede na coleta). Cenário
    @node_parado é o inverso: exige o node PARADO e dispensa este check."""
    if request.node.get_closest_marker("node_parado"):
        return
    rpc, chain_id, address = _venda_config()
    if not address:
        pytest.fail("endereço do VendaDados desconhecido: defina VENDA_DADOS_ADDRESS ou rode "
                    "scripts/deploy-local.js (escreve contracts/venda-dados/deployments/local.json)")
    try:
        w3 = _web3()
        got = w3.eth.chain_id
        code = w3.eth.get_code(Web3.to_checksum_address(address))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"node do VendaDados não respondeu em {rpc}: {exc}. Suba: "
                    "cd contracts/venda-dados && npx hardhat node --port 8546, depois node scripts/deploy-local.js")
    if got != chain_id:
        pytest.fail(f"chainId {got} em {rpc}, esperado {chain_id}")
    if len(code) == 0:
        pytest.fail(f"sem contrato em {address} na rede {rpc} (node reiniciado? rode o deploy de novo)")


@pytest.fixture
def context():
    return {"lotes": [], "keys": [], "producers": []}


def _fuseki(path, data, **kw):
    r = requests.post(f"{FUSEKI_URL}/{path}", data=data, auth=("admin", FUSEKI_PASSWORD), timeout=20, **kw)
    r.raise_for_status()
    return r


@pytest.fixture(autouse=True)
def _limpa(context):
    yield
    for batch in context["lotes"]:
        _fuseki("update", {"update": f"""{_PREFIX}
        DELETE WHERE {{ ?l agritrust:batchNumber "{batch}" . ?l ?p ?o }} ;
        DELETE WHERE {{ ?t :deliveryBatch "{batch}" . ?t ?p ?o }}"""})
    for did in context["producers"]:
        _fuseki("update", {"update": f"""{_PREFIX}
        DELETE WHERE {{ ?p agritrust:hasDID "{did}" . ?p ?x ?y }}"""})
    for path in context["keys"]:
        path.unlink(missing_ok=True)


def _key_path(did):
    return PRODUCERS_KEY_DIR / (hashlib.sha256(did.encode()).hexdigest() + ".key")


def _custodial_address(did):
    path = _key_path(did)
    assert path.exists(), f"carteira custodial não foi criada em {path} (coop-a não gerou a chave?)"
    return Account.from_key("0x" + path.read_text().strip()).address


def _plant_key(context, did):
    """Planta uma chave aleatória para o DID (para saber o endereço de antemão)."""
    acct = Account.create()
    PRODUCERS_KEY_DIR.mkdir(parents=True, exist_ok=True)
    _key_path(did).write_text(acct.key.hex().removeprefix("0x"))
    return acct.address


def _ref(batch):
    return Web3.keccak(text=batch)


def _new_batch(context, prefix):
    suffix = uuid.uuid4().hex[:8]
    batch = f"MS-2026-ENTREGA-{prefix}-{suffix}"
    did = f"did:key:zTesteEntrega{prefix}{suffix}"
    context["batch_number"], context["producer_did"] = batch, did
    context["lotes"].append(batch)
    context["producers"].append(did)
    context["keys"].append(_key_path(did))  # apaga a chave que o teste ou o coop-a criar
    return batch, did, suffix


def _coop_nonce():
    coop = Account.from_key("0x" + COOP_KEY_PATH.read_text().strip()).address
    return _web3().eth.get_transaction_count(coop)


def _task_status(batch):
    r = _fuseki("sparql", {"query": f'{_PREFIX}SELECT ?s WHERE {{ ?t :deliveryBatch "{batch}" ; :deliveryStatus ?s }}'},
                headers={"Accept": "application/sparql-results+json"})
    b = r.json()["results"]["bindings"]
    return b[0]["s"]["value"] if b else None


def _prepara_post_lote(context, quantity):
    batch, did, _ = _new_batch(context, "P")
    body = {"batchNumber": batch, "productType": "HoneyApis", "producerName": PRODUCER_NAME, "producerDID": did}
    if quantity is not None:
        body["quantityLiters"] = quantity
    context["body"] = body


@given(parsers.parse("um novo lote válido com quantityLiters {litros} e um produtor novo"))
def lote_com_quantidade(context, litros):
    _prepara_post_lote(context, float(litros))


@given("um novo lote válido sem quantityLiters e um produtor novo")
def lote_sem_quantidade(context):
    _prepara_post_lote(context, None)


@when("a cooperativa cadastra o lote via POST /lote")
def cadastra(context):
    r = requests.post(f"{COOP_A_URL}/lote", json=context["body"], timeout=60)
    assert r.status_code == 200, f"esperado 200, veio {r.status_code}: {r.text}"
    context["response"] = r.json()


@then(parsers.parse('a resposta traz deliveryStatus "{delivery}" e anchoringStatus "{anchoring}"'))
def resposta_status(context, delivery, anchoring):
    body = context["response"]
    assert body.get("deliveryStatus") == delivery, f"deliveryStatus esperado {delivery!r}, resposta: {body}"
    assert body.get("anchoringStatus") == anchoring, f"anchoringStatus esperado {anchoring!r}, resposta: {body}"


@then(parsers.parse("entregas do batchRef devolve a carteira custodial do produtor e {ml:d}"))
def entregas_custodial(context, ml):
    produtor, mililitros = _venda().functions.entregas(_ref(context["batch_number"])).call()
    esperado = _custodial_address(context["producer_did"])
    assert (produtor, mililitros) == (esperado, ml), f"entregas = {(produtor, mililitros)}, esperado {(esperado, ml)}"


@then("entregas do batchRef devolve o endereço zero e 0")
def entregas_zero(context):
    produtor, mililitros = _venda().functions.entregas(_ref(context["batch_number"])).call()
    assert (produtor, mililitros) == (ZERO, 0), f"entregas = {(produtor, mililitros)}, esperado ({ZERO}, 0)"


def _saldo(context):
    return _venda().functions.balanceOf(_custodial_address(context["producer_did"])).call()


@then(parsers.parse("o saldo de cotas da carteira custodial é {cotas:d}"))
def saldo_cotas(context, cotas):
    saldo = _saldo(context)
    assert saldo == cotas, f"saldo de cotas = {saldo}, esperado {cotas}"


@then(parsers.parse("o saldo de cotas continua {cotas:d}"))
def saldo_continua(context, cotas):
    saldo = _saldo(context)
    assert saldo == cotas, f"saldo de cotas = {saldo}, esperado {cotas}"


@then(parsers.parse("o lote não aparece no resultado e o saldo de cotas continua {cotas:d}"))
def nao_reaparece(context, cotas):
    mine = _mine(context)
    assert not mine, f"lote reapareceu no retry: {mine}"
    assert _coop_nonce() == context["nonce_before"], "o retry enviou transação (nonce da cooperativa mudou)"
    saldo = _saldo(context)
    assert saldo == cotas, f"saldo de cotas = {saldo}, esperado {cotas}"


@given(parsers.parse("um lote de {ml:d} mL no Fuseki do coop-a com entrega pendente"))
def lote_plantado(context, ml):
    batch, did, suffix = _new_batch(context, "R")
    slug = batch.replace("-", "_")
    context["wallet"] = _plant_key(context, did)
    _fuseki("update", {"update": f"""{_PREFIX}
    INSERT DATA {{
      :Produtor_ent_{suffix} a agritrust:DataProducer ; agritrust:hasDID "{did}" .
      :Lote_{slug} a :HoneyApis ; agritrust:ownedBy :Produtor_ent_{suffix} ;
          agritrust:batchNumber "{batch}" ; :quantityMilliliters {ml} .
      :DeliveryTask_{slug} a :DeliveryTask ; :deliveryBatch "{batch}" ; :deliveryStatus "pending" .
    }}"""})


@given(parsers.parse("a entrega já está on-chain com o mesmo produtor e {ml:d} mL"))
def entrega_ja_onchain(context, ml):
    w3 = _web3()
    coop = Account.from_key("0x" + COOP_KEY_PATH.read_text().strip())
    tx = _venda().functions.registrarEntrega(context["wallet"], _ref(context["batch_number"]), ml).build_transaction(
        {"from": coop.address, "nonce": w3.eth.get_transaction_count(coop.address), "chainId": w3.eth.chain_id})
    signed = coop.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=30)


def _retry(context):
    context["nonce_before"] = _coop_nonce()
    r = requests.post(f"{COOP_A_URL}/delivery/retry", timeout=120)
    assert r.status_code == 200, f"POST /delivery/retry esperado 200, veio {r.status_code}: {r.text}"
    context["last_results"] = r.json()["results"]


@when("o retry de entrega do coop-a roda")
def retry(context):
    _retry(context)


@when("o retry de entrega do coop-a roda de novo")
def retry_de_novo(context):
    _retry(context)


def _mine(context):
    return [r for r in context["last_results"] if r["batchNumber"] == context["batch_number"]]


@then('o resultado do lote é "sent" e a tarefa de entrega vira "registered"')
def sent_registered(context):
    mine = _mine(context)
    assert len(mine) == 1 and mine[0]["outcome"] == "sent" and mine[0].get("txHash"), f"esperado 'sent' com txHash, veio {mine}"
    assert _task_status(context["batch_number"]) == "registered", f"tarefa = {_task_status(context['batch_number'])!r}"


@then('o resultado do lote é "already_registered" sem transação e a tarefa vira "registered"')
def already_registered(context):
    mine = _mine(context)
    assert len(mine) == 1 and mine[0]["outcome"] == "already_registered", f"esperado 'already_registered', veio {mine}"
    assert not mine[0].get("txHash"), f"não devia haver txHash: {mine[0]}"
    assert _coop_nonce() == context["nonce_before"], "o retry enviou transação (nonce da cooperativa mudou)"
    assert _task_status(context["batch_number"]) == "registered", f"tarefa = {_task_status(context['batch_number'])!r}"


@then('o resultado do lote é "mismatch" e a tarefa continua "mismatch"')
def mismatch(context):
    mine = _mine(context)
    assert len(mine) == 1 and mine[0]["outcome"] == "mismatch", f"esperado 'mismatch', veio {mine}"
    assert _task_status(context["batch_number"]) == "mismatch", f"tarefa = {_task_status(context['batch_number'])!r}"
    assert _coop_nonce() == context["nonce_before"], "o retry enviou transação (nonce da cooperativa mudou)"


@then(parsers.parse("entregas do batchRef continua com {ml:d} mL"))
def entregas_continua(context, ml):
    produtor, mililitros = _venda().functions.entregas(_ref(context["batch_number"])).call()
    assert (produtor, mililitros) == (context["wallet"], ml), f"entregas = {(produtor, mililitros)}"


@given("que o node do VendaDados está parado")
def node_parado(context):
    assert not _porta_aberta(), "este cenário exige o node da 8546 PARADO; pare-o e rode de novo com -m node_parado"


@then(parsers.parse('a tarefa de entrega do lote está "{status}"'))
def tarefa_esta(context, status):
    assert _task_status(context["batch_number"]) == status, f"tarefa = {_task_status(context['batch_number'])!r}, esperado {status!r}"
