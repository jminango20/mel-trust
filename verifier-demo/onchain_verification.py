"""
Verificação de integridade on-chain — M9.5 (segunda fatia, sobre
`verify_endpoint.py`).

O que este módulo faz: para um `batch_number` já resolvido pela consulta
SPARQL federada (`query_federated`, em `verify_endpoint.py`), calcula os 3
assetIds esperados (lote, laudo, certificação — mesmo esquema de
`coop-a/anchoring.py`/`lab/anchoring.py`/`certifier/anchoring.py`), lê cada
um via `AssetRegistry.getAsset("mel-trust", assetId)` (chamada `view`, não
assina nada, não gasta gas) e, para lote/laudo, RECONSTRÓI o dado via
SPARQL `CONSTRUCT` contra o Fuseki de origem (coop-a para o lote, lab para
o laudo), recalcula o hash com a MESMA normalização usada ao ancorar
(`deterministic_nt_hash` — ver `coop-a/hash_utils.py`/`lab/hash_utils.py`) e
compara com `dataHashes[0]` lido do contrato.

Endereço do AssetRegistry: CONFIRMADO ao vivo nesta sessão via
`AddressDiscovery.getContractAddress(keccak256("ASSET_REGISTRY"))`
(`0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e`), resolvendo para
`0x0B306BF915C4d645ff596e518fAf3F9669b97016` — bate com
`docs/referencia-contratos/README.md` seção 6. Resolvido em RUNTIME (não
hardcoded) — diferente de `coop-a/anchoring.py`/`lab/anchoring.py`
(TransactionOrchestrator), que hardcodam por já terem confirmado o
endereço numa sessão anterior: aqui a tarefa pediu explicitamente para
confirmar via AddressDiscovery antes de usar, e este módulo só faz
chamadas `view` (sem custo de gas na rede zero-gas), então o custo extra
de uma chamada RPC adicional por verificação é aceitável.

Struct `Asset` e assinatura de `getAsset`: CONFIRMADOS em
`docs/referencia-contratos/README.md` seção 6 — 16 campos, 6 deles
`unknownN` (tipo confirmado, nome/propósito não). Este módulo só lê
`exists`, `parentAssetId` e `dataHashes[0]` (os únicos campos que importam
aqui, conforme a própria nota final daquela seção do README).

NORMALIZAÇÃO DE HASH DUPLICADA (decisão deliberada, documentada em
`coop-a/hash_utils.py`): cada nó deste projeto é um processo/venv separado
(mesmo padrão de duplicação de `anchoring.py` entre coop-a/coop-b/lab/
certifier) — `deterministic_nt_hash` é replicada aqui EXATAMENTE igual
(serialize nt -> filtra linhas vazias -> `sorted()` -> join com "\n" + "\n"
final -> `Web3.keccak(text=...)`), não importada entre projetos. Qualquer
mudança lá precisa ser replicada aqui, ou a verificação de integridade
passa a reportar "mismatch" para dados que na verdade não mudaram.

Falha ampla (RPC fora do ar, túnel SSH caído, Fuseki de origem fora do ar,
qualquer exceção): os 3 campos viram `"unavailable"`, nunca quebra a
resposta de `GET /verify` (mesmo padrão de "nunca quebra a resposta HTTP"
já usado em `anchoring.py`/`lote_endpoint.py`/`analise_endpoint.py`).
"""
import logging

import requests
from rdflib import Graph
from web3 import Web3

logger = logging.getLogger(__name__)

RPC_URL = "http://localhost:8545"
EXPECTED_CHAIN_ID = 648
CHANNEL_NAME = "mel-trust"

ADDRESS_DISCOVERY_ADDRESS = "0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e"

# Fusekis de ORIGEM dos dados a reconstruir via CONSTRUCT — mesma
# perspectiva de rede de `verifier-demo/config.py` (verifier-demo roda no
# HOST, fora de Docker, então usa as portas publicadas via 127.0.0.1, não
# host.docker.internal).
COOP_A_FUSEKI_SPARQL_URL = "http://127.0.0.1:3030/coop-a/sparql"
LAB_FUSEKI_SPARQL_URL = "http://127.0.0.1:3033/lab/sparql"

_ADDRESS_DISCOVERY_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "nameHash", "type": "bytes32"}],
        "name": "getContractAddress",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Struct Asset — 16 campos, ordem e tipos CONFIRMADOS em
# docs/referencia-contratos/README.md seção 6. Nomes `unknownN` mantidos
# como no README (propósito real não confirmado).
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
            {
                "internalType": "struct Asset",
                "name": "asset",
                "type": "tuple",
                "components": _ASSET_TUPLE_COMPONENTS,
            },
            {"internalType": "bool", "name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# Cache de módulo — w3/contratos não mudam entre chamadas (view, sem conta
# assinando). None até a primeira resolução bem-sucedida.
_cached_w3 = None
_cached_asset_registry = None


def _get_w3_and_asset_registry():
    """Conecta ao RPC, confirma chain_id, resolve o endereço do
    AssetRegistry via AddressDiscovery (ao vivo, nunca hardcoded) e monta o
    objeto de contrato. Cacheado a partir da primeira chamada bem-sucedida
    no processo. Levanta exceção em qualquer falha — quem chama
    (`verify_onchain_integrity`) é responsável por capturar."""
    global _cached_w3, _cached_asset_registry

    if _cached_w3 is not None and _cached_asset_registry is not None:
        return _cached_w3, _cached_asset_registry

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    if not w3.is_connected():
        raise ConnectionError("Não foi possível conectar ao RPC do Besu (túnel SSH pode estar fora).")

    chain_id = w3.eth.chain_id
    if chain_id != EXPECTED_CHAIN_ID:
        raise ValueError(f"chain_id inesperado: {chain_id} (esperado {EXPECTED_CHAIN_ID}).")

    address_discovery = w3.eth.contract(
        address=Web3.to_checksum_address(ADDRESS_DISCOVERY_ADDRESS),
        abi=_ADDRESS_DISCOVERY_ABI,
    )
    asset_registry_address = address_discovery.functions.getContractAddress(
        Web3.keccak(text="ASSET_REGISTRY")
    ).call()

    asset_registry = w3.eth.contract(
        address=Web3.to_checksum_address(asset_registry_address),
        abi=_GET_ASSET_ABI,
    )

    _cached_w3, _cached_asset_registry = w3, asset_registry
    return w3, asset_registry


def _deterministic_nt_hash(graph: Graph) -> bytes:
    """Réplica EXATA de `coop-a/hash_utils.py:deterministic_nt_hash` — ver
    docstring do módulo acima sobre por que é duplicada, não importada."""
    raw = graph.serialize(format="nt")
    lines = [line for line in raw.split("\n") if line.strip()]
    canonical = "\n".join(sorted(lines)) + "\n"
    return Web3.keccak(text=canonical)


# --- CONSTRUCT do LOTE, contra o Fuseki do coop-a ---
#
# Mesmas triplas que `coop-a/lote_endpoint.py` monta para o cenário de
# teste usado em `tests/step_defs/test_verificacao_hash_onchain.py`
# (batchNumber/productType/producerName/producerDID, sem geo/meliponário —
# ver levantamento prévio desta tarefa). `agritrust:ownedBy` liga o lote ao
# recurso do produtor; CONSTRUCT recupera as duas "pontas" (lote + produtor)
# para que o conjunto de triplas reconstruído seja idêntico ao que foi
# ancorado.
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

# --- CONSTRUCT do LAUDO, contra o Fuseki do lab ---
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
           agritrust:batchNumber %(batch_number)s ;
           bee:hasMoistureContent ?moisture ;
           bee:hasHMFContent ?hmf ;
           bee:hasAcidity ?acidity ;
           bee:hasDiastaseActivity ?diastase .
  BIND(%(batch_number)s AS ?batchNumber)
}
"""


def _sparql_literal(value: str) -> str:
    """Mesmo escape simples usado em todo o resto do projeto (não é um
    escapador SPARQL genérico e completo — ver docs/known-issues.md)."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _construct_graph(fuseki_sparql_url: str, query_template: str, batch_number: str) -> Graph:
    """Roda um CONSTRUCT contra `fuseki_sparql_url` e devolve o grafo
    resultante (rdflib.Graph), nunca persistido em lugar nenhum — existe só
    em memória, pelo tempo desta verificação."""
    query = query_template % {"batch_number": _sparql_literal(batch_number)}
    response = requests.post(
        fuseki_sparql_url,
        data={"query": query},
        headers={"Accept": "application/n-triples"},
        timeout=20,
    )
    response.raise_for_status()
    g = Graph()
    g.parse(data=response.text, format="nt")
    return g


def _check_asset(
    asset_registry,
    asset_id: bytes,
    fuseki_sparql_url: str | None,
    construct_template: str | None,
    batch_number: str,
) -> str:
    """Lê o asset `asset_id` via `getAsset` e, se existir e houver
    `fuseki_sparql_url`/`construct_template` (lote/laudo), reconstrói o
    dado e compara o hash recalculado com `dataHashes[0]`.

    Retorna um de: "confirmed", "mismatch", "not_anchored".
    """
    asset, exists = asset_registry.functions.getAsset(CHANNEL_NAME, asset_id).call()
    if not exists:
        return "not_anchored"

    if fuseki_sparql_url is None or construct_template is None:
        # Chamador (certificação) decide o rótulo — não deveria chegar
        # aqui nesse caso, mas devolve algo honesto se chegar.
        return "not_verifiable_via_query"

    data_hashes = asset[7]  # dataHashes, índice 7 no struct Asset (ver _ASSET_TUPLE_COMPONENTS)
    if not data_hashes:
        return "mismatch"
    anchored_hash = data_hashes[0]

    graph = _construct_graph(fuseki_sparql_url, construct_template, batch_number)
    recalculated_hash = _deterministic_nt_hash(graph)

    if bytes(recalculated_hash) == bytes(anchored_hash):
        return "confirmed"
    return "mismatch"


def verify_onchain_integrity(batch_number: str) -> dict:
    """Verifica a integridade on-chain dos 3 eventos (lote, laudo,
    certificação) de `batch_number`.

    Retorna `{"lote": ..., "laudo": ..., "certificacao": ...}`, cada valor
    em `"confirmed"`, `"mismatch"`, `"not_anchored"`,
    `"not_verifiable_via_query"` (só certificação) ou `"unavailable"` (RPC
    ou Fuseki de origem fora do ar — qualquer falha inesperada). NUNCA
    levanta exceção: todo erro é capturado e mapeado para
    `"unavailable"` nos 3 campos, para nunca quebrar a resposta de
    `GET /verify`.
    """
    try:
        _w3, asset_registry = _get_w3_and_asset_registry()

        lote_asset_id = Web3.keccak(text=batch_number)
        laudo_asset_id = Web3.keccak(text=f"{batch_number}-lab-analysis")
        certificacao_asset_id = Web3.keccak(text=f"{batch_number}-certification")

        lote_status = _check_asset(
            asset_registry, lote_asset_id, COOP_A_FUSEKI_SPARQL_URL, _LOTE_CONSTRUCT_TEMPLATE, batch_number
        )
        laudo_status = _check_asset(
            asset_registry, laudo_asset_id, LAB_FUSEKI_SPARQL_URL, _LAUDO_CONSTRUCT_TEMPLATE, batch_number
        )

        # Certificação: hash ancorado é Web3.keccak(text=sdjwt.sd_jwt_issuance)
        # (SD-JWT completo, não grafo RDF) — nunca reconstruível via SPARQL
        # CONSTRUCT (ver docstring do módulo/levantamento prévio da tarefa).
        # Só distingue "existe mas não é verificável por este canal" de
        # "nunca foi ancorado".
        asset, exists = asset_registry.functions.getAsset(CHANNEL_NAME, certificacao_asset_id).call()
        certificacao_status = "not_verifiable_via_query" if exists else "not_anchored"

        return {
            "lote": lote_status,
            "laudo": laudo_status,
            "certificacao": certificacao_status,
        }
    except Exception as exc:  # noqa: BLE001 - nunca quebra a resposta de GET /verify
        logger.error(
            "Falha ao verificar integridade on-chain do lote %r: %s: %s",
            batch_number, type(exc).__name__, exc,
        )
        return {"lote": "unavailable", "laudo": "unavailable", "certificacao": "unavailable"}


def read_certification_data_hash(batch_number: str) -> tuple[bool, bytes | None]:
    """Usado por `present_response_endpoint.py` (M9.7) — quando uma VP real
    é recebida, o hash do Credential-JWT (recalculado pelo chamador, não
    aqui) é comparado contra o `dataHashes[0]` ancorado DE VERDADE, em vez
    do placeholder `"not_verifiable_via_query"` que `verify_onchain_integrity`
    devolve sozinho (sem VP, não há como recalcular esse hash — ver
    docstring da função acima).

    Retorna `(exists, data_hash_ou_None)`. Propaga exceção de conectividade
    (RPC fora do ar) — quem chama decide o fallback, diferente de
    `verify_onchain_integrity` (que sempre engole erro pra não quebrar
    `GET /verify`, que tem valor sem checagem on-chain; aqui a checagem É
    o propósito da chamada)."""
    _w3, asset_registry = _get_w3_and_asset_registry()
    certificacao_asset_id = Web3.keccak(text=f"{batch_number}-certification")
    asset, exists = asset_registry.functions.getAsset(CHANNEL_NAME, certificacao_asset_id).call()
    if not exists:
        return False, None
    data_hashes = asset[7]  # dataHashes, índice 7 no struct Asset
    if not data_hashes:
        return True, None
    return True, bytes(data_hashes[0])
