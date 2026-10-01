"""
Step defs (pytest-bdd) para
tests/features/ancoragem_lote_produtor_reaproveitado.feature.

FASE RED (TDD): escrito ANTES da correção em coop-b/lote_endpoint.py.

Duas causas raiz conhecidas, ambas presentes em coop-b hoje (achado desta
sessão, documentado em docs/referencia-contratos/README.md seção 6 e no
histórico de correção de coop-a/lote_endpoint.py):

1. Não-determinismo: coop-b ainda hasheia
   `Web3.keccak(g.serialize(format="nt").encode("utf-8"))` DIRETO, sem
   ordenar as linhas N-Triples (rdflib não garante ordem entre processos) —
   nunca recebeu a correção `hash_utils.py`/`deterministic_nt_hash` já
   aplicada em coop-a e lab.

2. Incompletude: `is_new_producer` em coop-b/lote_endpoint.py só adiciona
   as triplas do produtor (`rdf:type`/`foaf:name`/`hasDID`) ao grafo `g`
   quando o produtor é NOVO. Quando um segundo lote reaproveita um
   `producerDID` já existente (`find_existing_producer` acha e reusa a
   URI), essas triplas NÃO entram em `g` — o hash ancorado reflete só as 3
   triplas do lote (type/ownedBy/batchNumber), não as 6 triplas completas
   (lote + produtor) que um verificador externo reconstruiria via SPARQL
   CONSTRUCT contra o Fuseki (onde o produtor existe de qualquer forma,
   reaproveitado ou não).

Este cenário testa especificamente a causa (2) — produtor reaproveitado —
mas a correção prevista (reconstruir via CONSTRUCT contra o próprio Fuseki
antes de ancorar, mesmo padrão já validado em coop-a) resolve as duas de
uma vez, porque:
  a. usa `deterministic_nt_hash` (ordenação determinística) — resolve (1).
  b. sempre reconstrói o conjunto REAL de triplas do Fuseki, nunca o
     objeto `g` em memória que pode estar incompleto — resolve (2).

Verifica "via getAsset()" diretamente contra o AssetRegistry (não via
GET /verify do verifier-demo — aquele módulo hoje só sabe reconstruir
lotes do coop-a, ver onchain_verification.py:COOP_A_FUSEKI_SPARQL_URL;
estender pra coop-b é outra tarefa, fora deste escopo).
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

scenarios("../features/ancoragem_lote_produtor_reaproveitado.feature")

COOP_B_URL = "http://localhost:8002"
PRODUCER_NAME = "João da Silva"

RPC_URL = "http://localhost:8545"
CHANNEL_NAME = "mel-trust"
ASSET_REGISTRY_ADDRESS = "0x0B306BF915C4d645ff596e518fAf3F9669b97016"

_coop_b_env = dotenv_values(REPO_ROOT / "coop-b" / ".env")
COOP_B_FUSEKI_URL = _coop_b_env.get("FUSEKI_URL", "http://127.0.0.1:3031/coop-b")
COOP_B_FUSEKI_ADMIN_PASSWORD = _coop_b_env.get("FUSEKI_ADMIN_PASSWORD")
COOP_B_FUSEKI_SPARQL_URL = f"{COOP_B_FUSEKI_URL}/sparql"

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"

# Struct Asset — 16 campos, ordem CONFIRMADA em
# docs/referencia-contratos/README.md seção 6.
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

# Mesma forma de CONSTRUCT usada em verifier-demo/onchain_verification.py
# (_LOTE_CONSTRUCT_TEMPLATE), só trocando o Fuseki alvo pra coop-b.
_LOTE_CONSTRUCT_TEMPLATE = """
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
  ?lote agritrust:batchNumber %(batch_number)s ;
        a ?productType ;
        agritrust:ownedBy ?producer .
  BIND(%(batch_number)s AS ?batchNumber)
  ?producer a agritrust:DataProducer ;
            agritrust:hasDID ?producerDID .
  OPTIONAL { ?producer foaf:name ?producerName }
}
"""


def _sparql_literal(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _deterministic_nt_hash(raw_nt: str) -> bytes:
    """Mesma normalização esperada de coop-b/hash_utils.py (ainda não
    existe — é exatamente o que a correção precisa criar): ordena as
    linhas N-Triples antes de hashear, pra ser determinístico
    independente de processo/ordem de serialização."""
    lines = [line for line in raw_nt.split("\n") if line.strip()]
    canonical = "\n".join(sorted(lines)) + "\n"
    return Web3.keccak(text=canonical)


def _recalcula_hash_esperado(batch_number: str) -> bytes:
    """Reconstrói o CONJUNTO COMPLETO de triplas (lote + produtor) via
    CONSTRUCT contra o Fuseki do coop-b e calcula o hash determinístico —
    é o que QUALQUER verificador externo (incluindo o futuro
    onchain_verification.py estendido pra coop-b) recalcularia."""
    query = _LOTE_CONSTRUCT_TEMPLATE % {"batch_number": _sparql_literal(batch_number)}
    resp = requests.post(
        COOP_B_FUSEKI_SPARQL_URL,
        data={"query": query},
        headers={"Accept": "application/n-triples"},
        timeout=20,
    )
    resp.raise_for_status()
    return _deterministic_nt_hash(resp.text)


@pytest.fixture
def context():
    return {}


def _apaga_lote_no_coop_b(batch_number: str):
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?lote agritrust:batchNumber "{batch_number}" .
      ?lote ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_B_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_B_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


@pytest.fixture(autouse=True)
def _limpeza(context):
    yield
    for key in ("batch_number_1", "batch_number_2"):
        if key in context:
            _apaga_lote_no_coop_b(context[key])


@given("que um produtor já existe no coop-b (lote anterior cadastrado)")
def produtor_ja_existe(context):
    suffix = uuid.uuid4().hex[:8]
    producer_did = f"did:key:zTesteReaproveitado{suffix}"
    batch_1 = f"MN-2026-REUSE1-{suffix}"

    resp = requests.post(
        f"{COOP_B_URL}/lote",
        json={
            "batchNumber": batch_1,
            "productType": "HoneyNativeStinglessBee",
            "producerName": PRODUCER_NAME,
            "producerDID": producer_did,
        },
        timeout=20,
    )
    assert resp.status_code == 200, (
        f"esperado 200 no 1º cadastro (cria o produtor), veio {resp.status_code}: {resp.text}"
    )
    context["producer_did"] = producer_did
    context["batch_number_1"] = batch_1


@when("um segundo lote é cadastrado reaproveitando esse produtor")
def segundo_lote_reaproveita_produtor(context):
    suffix = uuid.uuid4().hex[:8]
    batch_2 = f"MN-2026-REUSE2-{suffix}"

    resp = requests.post(
        f"{COOP_B_URL}/lote",
        json={
            "batchNumber": batch_2,
            "productType": "HoneyNativeStinglessBee",
            "producerName": PRODUCER_NAME,
            "producerDID": context["producer_did"],
        },
        timeout=20,
    )
    assert resp.status_code == 200, (
        f"esperado 200 no 2º cadastro (reaproveita produtor), veio {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("anchoringStatus") == "anchored", (
        f"esperava o 2º lote ancorado on-chain, veio: {body!r}"
    )
    context["batch_number_2"] = batch_2
    context["lote_response"] = body


@then("o hash ancorado reflete o conjunto completo de triplas do lote (incluindo produtor), verificável via getAsset()")
def hash_ancorado_completo(context):
    batch_2 = context["batch_number_2"]

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    assert w3.is_connected(), "não foi possível conectar ao RPC do Besu (túnel SSH pode estar fora)"

    asset_registry = w3.eth.contract(
        address=Web3.to_checksum_address(ASSET_REGISTRY_ADDRESS),
        abi=_GET_ASSET_ABI,
    )
    asset_id = Web3.keccak(text=batch_2)
    asset, exists = asset_registry.functions.getAsset(CHANNEL_NAME, asset_id).call()
    assert exists, f"esperava asset do lote {batch_2!r} existir on-chain, veio exists=False"

    data_hashes = asset[7]  # dataHashes, índice 7 no struct Asset
    assert data_hashes, f"esperava dataHashes não vazio, veio: {data_hashes!r}"
    anchored_hash = bytes(data_hashes[0])

    expected_hash = bytes(_recalcula_hash_esperado(batch_2))

    assert anchored_hash == expected_hash, (
        f"hash ancorado ({anchored_hash.hex()}) não bate com o hash recalculado a "
        f"partir do CONJUNTO COMPLETO de triplas (lote + produtor reaproveitado) "
        f"via CONSTRUCT ({expected_hash.hex()}) — o grafo hasheado no momento da "
        f"ancoragem estava incompleto (faltam as triplas do produtor, porque ele "
        f"foi reaproveitado, não criado de novo)."
    )
