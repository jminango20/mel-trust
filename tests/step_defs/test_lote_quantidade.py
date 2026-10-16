"""
Step defs (pytest-bdd) para tests/features/lote_quantidade.feature.

FASE RED (TDD): escrito e rodado ANTES de POST /lote aceitar `quantityLiters`.
Hoje o campo é ignorado (lote_endpoint.py não o lê) e anchor_batch ancora
amount=1 fixo. Contrato proposto: `quantityLiters` (litros, <= 3 casas) ->
`:quantityMilliliters` xsd:integer no Fuseki -> `amount` do createAsset.

Testado contra coop-a. getAsset lido direto do AssetRegistry (mesmo ABI de
test_ancoragem_lote_produtor_reaproveitado.py).
"""
import hashlib
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/lote_quantidade.feature")

COOP_A_URL = "http://localhost:8001"
PRODUCER_NAME = "João da Silva"
PRODUCERS_KEY_DIR = REPO_ROOT / "coop-a" / "keys" / "producers"

RPC_URL = "http://127.0.0.1:8545"
CHANNEL_NAME = "mel-trust"
ASSET_REGISTRY_ADDRESS = "0x0B306BF915C4d645ff596e518fAf3F9669b97016"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"

_ASSET_TUPLE_COMPONENTS = [
    {"internalType": "bytes32", "name": "assetId", "type": "bytes32"},
    {"internalType": "string", "name": "channelName", "type": "string"},
    {"internalType": "address", "name": "owner", "type": "address"},
    {"internalType": "bytes32", "name": "parentAssetId", "type": "bytes32"},
    {"internalType": "uint256", "name": "amount", "type": "uint256"},
    {"internalType": "uint256", "name": "unknown1", "type": "uint256"},
    {"internalType": "string", "name": "idLocal", "type": "string"},
    {"internalType": "bytes32[]", "name": "dataHashes", "type": "bytes32[]"},
    {"internalType": "uint256", "name": "unknown2", "type": "uint256"},
    {"internalType": "uint256", "name": "unknown3", "type": "uint256"},
    {"internalType": "uint256[]", "name": "unknown4", "type": "uint256[]"},
    {"internalType": "uint256", "name": "unknown5", "type": "uint256"},
    {"internalType": "uint256", "name": "unknown6", "type": "uint256"},
    {"internalType": "uint256[]", "name": "unknown7", "type": "uint256[]"},
    {"internalType": "uint256", "name": "createdAt", "type": "uint256"},
    {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
]
_GET_ASSET_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "bytes32", "name": "assetId", "type": "bytes32"},
        ],
        "name": "getAsset",
        "outputs": [
            {"internalType": "struct Asset", "name": "asset", "type": "tuple", "components": _ASSET_TUPLE_COMPONENTS},
            {"internalType": "bool", "name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


@pytest.fixture
def context():
    return {}


def _fuseki_post(path: str, data: dict, **kwargs):
    resp = requests.post(
        f"{COOP_A_FUSEKI_URL}/{path}",
        data=data,
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
        **kwargs,
    )
    resp.raise_for_status()
    return resp


@pytest.fixture(autouse=True)
def _limpa_lote_sintetico(context):
    yield
    if "batch_number" not in context:
        return
    _fuseki_post("update", {"update": f"""{_PREFIX}
    DELETE WHERE {{
      ?lote agritrust:batchNumber "{context['batch_number']}" .
      ?lote ?p ?o .
    }}"""})
    _fuseki_post("update", {"update": f"""{_PREFIX}
    DELETE WHERE {{ ?t :deliveryBatch "{context['batch_number']}" . ?t ?p ?o }}"""})
    if "producer_did_planted" in context:
        _fuseki_post("update", {"update": f"""{_PREFIX}
        DELETE WHERE {{ ?p agritrust:hasDID "{context['producer_did_planted']}" . ?p ?x ?y }}"""})
        # carteira custodial criada pelo coop-a para este DID sintético (se houve entrega)
        (PRODUCERS_KEY_DIR / (hashlib.sha256(context["producer_did_planted"].encode()).hexdigest() + ".key")
         ).unlink(missing_ok=True)


def _lote_existe(batch_number: str) -> bool:
    resp = _fuseki_post(
        "sparql",
        {"query": f'{_PREFIX}ASK {{ ?l agritrust:batchNumber "{batch_number}" }}'},
        headers={"Accept": "application/sparql-results+json"},
    )
    return resp.json()["boolean"]


@given(parsers.parse("um novo lote válido com quantityLiters {litros}"))
def novo_lote_com_quantidade(context, litros):
    suffix = uuid.uuid4().hex[:8]
    context["batch_number"] = f"MS-2026-QTD-{suffix}"
    # DID sintético novo por execução: a carteira custodial criada pelo coop-a é apagada na limpeza.
    context["producer_did_planted"] = f"did:key:zTesteQtd{uuid.uuid4().hex}"
    # Número JSON (float), como um cliente real mandaria — não string.
    context["body"] = {
        "batchNumber": context["batch_number"],
        "productType": "HoneyApis",
        "producerName": PRODUCER_NAME,
        "producerDID": context["producer_did_planted"],
        "quantityLiters": float(litros),
    }


@when("a cooperativa cadastra o lote via POST /lote")
def cadastra_lote(context):
    resp = requests.post(f"{COOP_A_URL}/lote", json=context["body"], timeout=20)
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    context["response"] = resp


@when("a cooperativa tenta cadastrar o lote via POST /lote")
def tenta_cadastrar_lote(context):
    context["response"] = requests.post(f"{COOP_A_URL}/lote", json=context["body"], timeout=20)


@then(parsers.parse("o Fuseki guarda quantityMilliliters {ml:d} como inteiro"))
def fuseki_guarda_ml(context, ml):
    resp = _fuseki_post(
        "sparql",
        {"query": f"""{_PREFIX}
        SELECT ?q (DATATYPE(?q) AS ?dt) WHERE {{
          ?l agritrust:batchNumber "{context['batch_number']}" ; :quantityMilliliters ?q .
        }}"""},
        headers={"Accept": "application/sparql-results+json"},
    )
    bindings = resp.json()["results"]["bindings"]
    assert bindings, "lote sem :quantityMilliliters no Fuseki (campo quantityLiters ignorado?)"
    assert bindings[0]["q"]["value"] == str(ml), f"esperado {ml}, veio {bindings[0]['q']['value']}"
    assert bindings[0]["dt"]["value"].endswith("#integer"), f"datatype {bindings[0]['dt']['value']}, esperado xsd:integer"


@then(parsers.parse("o createAsset on-chain usa amount {amount:d}"))
def create_asset_amount(context, amount):
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    registry = w3.eth.contract(address=Web3.to_checksum_address(ASSET_REGISTRY_ADDRESS), abi=_GET_ASSET_ABI)
    asset, exists = registry.functions.getAsset(CHANNEL_NAME, Web3.keccak(text=context["batch_number"])).call()
    assert exists, f"asset {context['batch_number']} não existe on-chain (anchoringStatus={context['response'].json().get('anchoringStatus')})"
    assert asset[4] == amount, f"amount on-chain = {asset[4]}, esperado {amount}"


BEE_NS = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"


@given("um lote de 1800 mL no Fuseki do coop-a com ancoragem pendente e ainda fora da blockchain")
def lote_plantado_com_quantidade(context):
    suffix = uuid.uuid4().hex[:8]
    batch = f"MS-2026-QTD-RETRY-{suffix}"
    slug = batch.replace("-", "_")
    context["batch_number"] = batch
    context["producer_did_planted"] = f"did:key:zTesteQtdRetry{suffix}"
    # Plantado DIRETO (sem POST /lote, que ancoraria com o amount certo e
    # esconderia o retry). Produtor próprio para a limpeza não tocar o real.
    _fuseki_post("update", {"update": f"""{_PREFIX}
    INSERT DATA {{
      :Produtor_qtd_{suffix} a agritrust:DataProducer ;
          agritrust:hasDID "{context['producer_did_planted']}" .
      :Lote_{slug} a :HoneyApis ;
          agritrust:ownedBy :Produtor_qtd_{suffix} ;
          agritrust:batchNumber "{batch}" ;
          :quantityMilliliters 1800 .
      :AnchoringTask_{slug} a :AnchoringTask ;
          :anchoringBatch "{batch}" ;
          :anchoringStatus "pending" .
    }}"""})


@when("o retry de ancoragem do coop-a roda")
def retry_roda(context):
    resp = requests.post(f"{COOP_A_URL}/anchoring/retry", timeout=180)
    assert resp.status_code == 200, f"retry esperado 200, veio {resp.status_code}: {resp.text}"
    mine = [r for r in resp.json()["results"] if r["batchNumber"] == context["batch_number"]]
    assert len(mine) == 1 and mine[0]["outcome"] == "sent", f"esperado outcome 'sent', veio {mine!r}"


@then(parsers.parse("o asset on-chain tem amount {amount:d}"))
def asset_amount(context, amount):
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    registry = w3.eth.contract(address=Web3.to_checksum_address(ASSET_REGISTRY_ADDRESS), abi=_GET_ASSET_ABI)
    asset, exists = registry.functions.getAsset(CHANNEL_NAME, Web3.keccak(text=context["batch_number"])).call()
    assert exists, "asset não existe on-chain após o retry"
    assert asset[4] == amount, f"amount on-chain = {asset[4]}, esperado {amount}"


@then("retorna 400 com shacl_violation e nada é inserido no Fuseki")
def rejeitado_shacl(context):
    resp = context["response"]
    assert resp.status_code == 400, f"esperado 400, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("error") == "shacl_violation", f"esperado error=shacl_violation, veio {resp.json()}"
    assert not _lote_existe(context["batch_number"]), "lote foi inserido apesar do 400"


@then("retorna 400 invalid_request e nada é inserido no Fuseki")
def rejeitado_formato(context):
    resp = context["response"]
    assert resp.status_code == 400, f"esperado 400, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("error") == "invalid_request", f"esperado error=invalid_request, veio {resp.json()}"
    assert not _lote_existe(context["batch_number"]), "lote foi inserido apesar do 400"
