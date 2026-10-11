"""
Ancoragem on-chain de POST /analise — chama
`TransactionOrchestrator.submitTransaction` na rede Semear (Besu privado,
chain ID 648, zero gas) depois que o laudo já foi inserido com sucesso no
Fuseki do lab (ver analise_endpoint.py, `anchor_lab_analysis` chamado no
fim do fluxo de `registrar_analise`).

Mesmo mecanismo de `coop-a/anchoring.py`/`coop-b/anchoring.py` (mesma ABI de
`submitTransaction`, mesmo cache de módulo, mesmo contrato de retorno
`(sucesso, tx_hash_ou_None, mensagem_de_erro_ou_None)`, nunca levanta
exceção) — a diferença central é O QUE está sendo ancorado:

- `coop-a/anchoring.py` ancora o LOTE em si: `newAssetIds=[keccak256(batch_number)]`,
  processo `honey-batch-registration`/`creation`, `action=CREATE_ASSET` (0),
  `parentAssetId=bytes32(0)` (o lote não tem pai).
- Este módulo ancora o LAUDO (documento vinculado ao lote, não o próprio
  ativo): `newAssetIds=[keccak256(batch_number + "-lab-analysis")]` — um
  assetId NOVO e distinto do assetId do lote —, processo
  `honey-lab-analysis`/`reporting`, `action=CREATE_DOCUMENT` (9, ver
  docs/referencia-contratos/README.md seção 4). `operationData.parentAssetId`
  é `keccak256(batch_number)` — O MESMO valor usado como `newAssetIds[0]`
  em `coop-a/anchoring.py`/`coop-b/anchoring.py` ao ancorar o lote — é isso
  que vincula o documento ao ativo pai no grafo de ativos do
  TransactionOrchestrator/AssetRegistry. `Web3.keccak(text=batch_number)` é
  o MESMO cálculo (mesmo encoding) usado lá, conferido linha a linha contra
  `coop-a/anchoring.py` antes de codar este módulo.

`submitTransaction` não recebe `action` como parâmetro explícito — quem
determina isso é o processo (`processId`/`natureId`/`stageId`) já
registrado no ProcessRegistry (`honey-lab-analysis` foi criado com
`action=CREATE_DOCUMENT` no momento do `createProcess`, tarefa anterior
desta sessão) — o TransactionOrchestrator resolve o processo internamente
e aplica a ação correspondente.

NUNCA reverte a inserção no Fuseki nem quebra a resposta HTTP: qualquer
erro aqui (rede fora, tunel SSH caído, revert on-chain, timeout, gas,
productType não resolvido) é capturado e devolvido como
`(False, tx_hash_ou_None, mensagem_de_erro)` para o chamador decidir o que
expor em `anchoringStatus`/`anchoringError` — decisão já tomada pelo
usuário, não deste módulo.

ABI e endereço do TransactionOrchestrator: CONFIRMADOS contra o node real
(ver docs/referencia-contratos/README.md, seção 5) — mesma constante
hardcoded (não resolvida via AddressDiscovery em runtime) e mesma
justificativa de `coop-a/anchoring.py`: o endereço já foi confirmado uma
vez nesta sessão e o contrato não é upgradeable neste MVP.
"""
import logging
import os

from requests.exceptions import ConnectionError as _ReqConnectionError
from requests.exceptions import Timeout as _ReqTimeout
from web3 import Web3
from web3.exceptions import TimeExhausted

from config import (
    ADDRESS_DISCOVERY_ADDRESS,
    CHANNEL_NAME,
    EXPECTED_CHAIN_ID,
    RPC_URL,
    TRANSACTION_ORCHESTRATOR_ADDRESS,
)

logger = logging.getLogger(__name__)

# Timeout por transação: espera pelo recibo (e, via request_kwargs do
# HTTPProvider, cada chamada RPC individual: 10s) falha RÁPIDO em vez de
# ficar pendurada quando o túnel SSH cai ou a máquina suspende.
RPC_TX_TIMEOUT_S = 60


def _describe_rpc_error(exc: Exception) -> str:
    """Mensagem de erro de ancoragem. Timeout/conexão recusada no RPC viram
    uma frase explícita ("RPC/túnel não respondeu"), em vez de só o nome
    cru da exceção da lib."""
    if isinstance(exc, (TimeExhausted, _ReqConnectionError, _ReqTimeout)):
        return (
            f"RPC/túnel não respondeu (timeout de {RPC_TX_TIMEOUT_S}s por transação; "
            f"túnel SSH fora ou máquina suspensa?): {type(exc).__name__}: {exc}"
        )
    return f"{type(exc).__name__}: {exc}"

PROCESS_ID = "honey-lab-analysis"
STAGE_ID = "reporting"

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BESU_KEY_PATH = os.environ.get(
    "BESU_KEY_PATH", os.path.join(_THIS_DIR, "keys", "lab-besu.key")
)

_OPERATION_DATA_COMPONENTS = [
    {"internalType": "uint256", "name": "amount", "type": "uint256"},
    {"internalType": "string", "name": "idLocal", "type": "string"},
    {"internalType": "bytes32[]", "name": "dataHashes", "type": "bytes32[]"},
    {"internalType": "address", "name": "newOwner", "type": "address"},
    {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"},
    {"internalType": "bytes32", "name": "parentAssetId", "type": "bytes32"},
]

_TRANSACTION_REQUEST_COMPONENTS = [
    {"internalType": "bytes32[]", "name": "newAssetIds", "type": "bytes32[]"},
    {"internalType": "bytes32[]", "name": "targetAssetIds", "type": "bytes32[]"},
    {"internalType": "string", "name": "processId", "type": "string"},
    {"internalType": "string", "name": "natureId", "type": "string"},
    {"internalType": "string", "name": "stageId", "type": "string"},
    {"internalType": "string", "name": "channelName", "type": "string"},
    {
        "internalType": "struct OperationData",
        "name": "operationData",
        "type": "tuple",
        "components": _OPERATION_DATA_COMPONENTS,
    },
]

# ABI void — outputs=[], ver disclaimer no topo do módulo.
_SUBMIT_TRANSACTION_ABI = [
    {
        "inputs": [
            {
                "internalType": "struct TransactionRequest",
                "name": "request",
                "type": "tuple",
                "components": _TRANSACTION_REQUEST_COMPONENTS,
            }
        ],
        "name": "submitTransaction",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Cache de módulo — carregados/instanciados uma vez só (a chave privada e o
# w3.eth.contract não mudam entre chamadas). None até a primeira chamada
# bem-sucedida de `_get_account_and_contract`.
_cached_account = None
_cached_w3 = None
_cached_contract = None


def _get_account_and_contract():
    """Carrega a chave Besu do próprio lab e monta o objeto de contrato,
    com cache de módulo. Levanta exceção se a chave não existir/estiver
    vazia, ou se o RPC não responder — o chamador (`anchor_lab_analysis`) é
    responsável por capturar isso."""
    global _cached_account, _cached_w3, _cached_contract

    if _cached_account is not None and _cached_contract is not None:
        return _cached_account, _cached_w3, _cached_contract

    if not os.path.isfile(BESU_KEY_PATH):
        raise FileNotFoundError(f"Chave Besu não encontrada em {BESU_KEY_PATH}")

    private_key = open(BESU_KEY_PATH, "r", encoding="utf-8").read().strip()
    if not private_key:
        raise ValueError(f"Chave Besu em {BESU_KEY_PATH} está vazia.")

    from eth_account import Account

    account = Account.from_key(private_key)
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(TRANSACTION_ORCHESTRATOR_ADDRESS),
        abi=_SUBMIT_TRANSACTION_ABI,
    )

    _cached_account, _cached_w3, _cached_contract = account, w3, contract
    return account, w3, contract


def anchor_lab_analysis(
    batch_number: str, nature_id: str, data_hash: bytes
) -> tuple[bool, str | None, str | None]:
    """Ancora o LAUDO do lote `batch_number` on-chain via
    `TransactionOrchestrator.submitTransaction`, como um documento
    (`CREATE_DOCUMENT`) vinculado ao ativo do lote — não o próprio ativo.

    `data_hash` é o keccak256 (32 bytes) do MESMO grafo rdflib em memória
    recém-inserido no Fuseki do lab (serializado como N-Triples) — não
    re-consulta o Fuseki, para não ancorar um estado potencialmente
    diferente do que acabou de ser gravado (mesma decisão de design de
    `coop-a/anchoring.py`).

    `nature_id` é o `productType` do lote (ex.: "HoneyApis",
    "HoneyNativeStinglessBee"), resolvido via SPARQL federado contra
    coop-a/coop-b ANTES de chamar esta função (ver
    `analise_endpoint.py`/`_resolve_product_type`) — casa com os processos
    `honey-lab-analysis` já registrados no ProcessRegistry para esses dois
    natureId.

    `newAssetIds[0]` é `keccak256(batch_number + "-lab-analysis")` — um
    assetId NOVO, distinto do assetId do lote
    (`keccak256(batch_number)`, usado em `coop-a/anchoring.py`). O vínculo
    entre os dois é feito via `operationData.parentAssetId =
    keccak256(batch_number)` — o MESMO valor que `coop-a/anchoring.py` usa
    como `newAssetIds[0]` ao ancorar o lote.

    Retorna `(sucesso, tx_hash_hex_ou_None, mensagem_de_erro_ou_None)`.
    NUNCA levanta exceção — todo erro (rede, revert, timeout, chave
    ausente, tunel caído) é capturado e devolvido como mensagem. Quem
    decide o que fazer com isso (campo `anchoringStatus` da resposta HTTP,
    log) é o chamador."""
    try:
        account, w3, contract = _get_account_and_contract()

        if not w3.is_connected():
            return False, None, "Não foi possível conectar ao RPC do Besu (tunel SSH pode estar fora)."

        chain_id = w3.eth.chain_id
        if chain_id != EXPECTED_CHAIN_ID:
            return False, None, f"chain_id inesperado: {chain_id} (esperado {EXPECTED_CHAIN_ID})."

        new_asset_id = Web3.keccak(text=f"{batch_number}-lab-analysis")
        # MESMO cálculo usado em coop-a/anchoring.py:anchor_batch para o
        # newAssetIds do LOTE — é o que vincula este documento ao ativo pai.
        parent_asset_id = Web3.keccak(text=batch_number)

        operation_data = (
            1,                    # amount
            "",                   # idLocal
            [data_hash],          # dataHashes
            "0x0000000000000000000000000000000000000000",  # newOwner = address(0)
            [],                   # amounts
            parent_asset_id,      # parentAssetId = keccak256(batch_number), vincula ao lote
        )
        transaction_request = (
            [new_asset_id],       # newAssetIds
            [],                   # targetAssetIds
            PROCESS_ID,
            nature_id,
            STAGE_ID,
            CHANNEL_NAME,
            operation_data,
        )

        fn = contract.functions.submitTransaction(transaction_request)

        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = w3.eth.gas_price
        estimated_gas = fn.estimate_gas({"from": account.address})

        tx = fn.build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": EXPECTED_CHAIN_ID,
                "gas": estimated_gas,
                "gasPrice": gas_price,
            }
        )
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RPC_TX_TIMEOUT_S)
        # web3.py 8.x: HexBytes.hex() retorna SEM o prefixo "0x" — normaliza
        # aqui pra convenção usual de tx hash Ethereum (o hash em si não
        # muda, só a representação em string).
        tx_hash_hex = tx_hash.hex()
        if not tx_hash_hex.startswith("0x"):
            tx_hash_hex = "0x" + tx_hash_hex

        if receipt.status == 1:
            logger.info(
                "Laudo do lote %r ancorado on-chain com sucesso (createDocument). tx_hash=%s bloco=%s",
                batch_number, tx_hash_hex, receipt.blockNumber,
            )
            return True, tx_hash_hex, None

        error_msg = f"receipt.status == 0 (revert on-chain). tx_hash={tx_hash_hex}"
        logger.error("Falha ao ancorar laudo do lote %r on-chain: %s", batch_number, error_msg)
        return False, None, error_msg

    except Exception as exc:  # noqa: BLE001 - nunca deixa exceção escapar (rede/revert/timeout/gas)
        error_msg = _describe_rpc_error(exc)
        if is_known_transaction_error(error_msg):
            # tx idêntica já na fila (em voo): não é erro; o retry decide (ver
            # `_retry_one` em analise_endpoint.py). INFO, não ERROR.
            logger.info("Laudo do lote %r: tx já está em voo no nó (%s)", batch_number, KNOWN_TRANSACTION_MARKER)
        else:
            logger.error("Erro ao ancorar laudo do lote %r on-chain: %s", batch_number, error_msg)
        return False, None, error_msg


# --- Detecção de "Known transaction" (tx em voo) ---------------------------
KNOWN_TRANSACTION_MARKER = "Known transaction"


def is_known_transaction_error(error: str | None) -> bool:
    """O Besu responde 'Known transaction' (-32000) quando a MESMA tx assinada
    já está na fila dele, ainda não minerada: ela está "em voo", não falhou."""
    return bool(error) and KNOWN_TRANSACTION_MARKER in error


# --- Leitura on-chain (outbox/retry) ---------------------------------------
# Mesmo caminho de leitura de coop-a/anchoring.py e verifier-demo/
# onchain_verification.py: AssetRegistry resolvido AO VIVO via AddressDiscovery;
# `getAsset(channel, assetId)` é `view` (sem conta, sem gas, sem tx).

_ADDRESS_DISCOVERY_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "nameHash", "type": "bytes32"}],
        "name": "getContractAddress",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Struct Asset (16 campos) — ordem/tipos CONFIRMADOS em
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

_cached_asset_registry = None


def _get_onchain_data_hash_raw(asset_id: bytes) -> tuple[bool, bytes | None]:
    """`getAsset(CHANNEL_NAME, asset_id)` -> `(exists, dataHashes[0] | None)`.
    LEVANTA exceção em erro de rede/RPC: o retry precisa distinguir "asset não
    existe" (seguro enviar) de "não consegui consultar" (NÃO pode enviar às cegas)."""
    global _cached_asset_registry

    if _cached_asset_registry is None:
        w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))
        if not w3.is_connected():
            raise ConnectionError("Não foi possível conectar ao RPC do Besu (tunel SSH pode estar fora).")
        if w3.eth.chain_id != EXPECTED_CHAIN_ID:
            raise ValueError(f"chain_id inesperado: {w3.eth.chain_id} (esperado {EXPECTED_CHAIN_ID}).")
        discovery = w3.eth.contract(
            address=Web3.to_checksum_address(ADDRESS_DISCOVERY_ADDRESS), abi=_ADDRESS_DISCOVERY_ABI
        )
        registry_address = discovery.functions.getContractAddress(Web3.keccak(text="ASSET_REGISTRY")).call()
        _cached_asset_registry = w3.eth.contract(
            address=Web3.to_checksum_address(registry_address), abi=_GET_ASSET_ABI
        )

    asset, exists = _cached_asset_registry.functions.getAsset(CHANNEL_NAME, asset_id).call()
    if not exists:
        return False, None
    data_hashes = asset[7]
    return True, (bytes(data_hashes[0]) if data_hashes else None)


def _read_asset(asset_id: bytes) -> tuple[bool, bytes | None]:
    """Timeout/conexão recusada no RPC viram ConnectionError com a mensagem
    "RPC/túnel não respondeu" (mesma convenção de `anchor_lab_analysis`)."""
    try:
        return _get_onchain_data_hash_raw(asset_id)
    except (_ReqConnectionError, _ReqTimeout, TimeExhausted) as exc:
        raise ConnectionError(_describe_rpc_error(exc)) from exc


def get_onchain_data_hash(batch_number: str) -> tuple[bool, bytes | None]:
    """O LAUDO já está on-chain? assetId = keccak256(batch_number + "-lab-analysis").
    Retorna `(exists, dataHashes[0] | None)`."""
    return _read_asset(Web3.keccak(text=f"{batch_number}-lab-analysis"))


def parent_exists_onchain(batch_number: str) -> bool:
    """O PAI (asset do lote, ancorado pelo coop-a/coop-b) já está on-chain?
    assetId = keccak256(batch_number) — o mesmo `parentAssetId` do laudo. Sem o
    pai, `createDocument` reverte; o retry espera (`waiting_parent`)."""
    return _read_asset(Web3.keccak(text=batch_number))[0]
