"""
Ancoragem on-chain de POST /lote — chama
`TransactionOrchestrator.submitTransaction` na rede Semear (Besu privado,
chain ID 648, zero gas) depois que o lote já foi inserido com sucesso no
Fuseki (ver lote_endpoint.py, `_anchor_lote_on_chain` embutido no fluxo de
`cadastrar_lote`).

NUNCA reverte a inserção no Fuseki nem quebra a resposta HTTP: qualquer
erro aqui (rede fora, tunel SSH caído, revert on-chain, timeout, gas) é
capturado e devolvido como `(False, tx_hash_ou_None, mensagem_de_erro)`
para o chamador decidir o que expor em `anchoringStatus`/`anchoringError`
— decisão já tomada pelo usuário, não deste módulo.

ABI e endereço do TransactionOrchestrator: CONFIRMADOS contra o node real
nesta mesma sessão (ver docs/referencia-contratos/README.md, seção 5, e
scripts/blockchain/test_submit_transaction.py — tx real
0x56782744e2b6446ae781315ae9baf05e713242fa4653ec234253d215f44a990b,
receipt.status=1). `submitTransaction` é VOID (outputs=[]) — não tenta
decodificar nenhum retorno; o único dado confiável pós-chamada é o
tx_hash da própria transação assinada (independente do ABI estar certo).

ENDEREÇO HARDCODED, NÃO RESOLVIDO VIA AddressDiscovery: mesma decisão já
tomada em scripts/blockchain/test_submit_transaction.py e
create_process.py — o endereço do TransactionOrchestrator já foi
confirmado uma vez nesta sessão (getVersion()=="1.0.0", tx real de
sucesso), então cada chamada em runtime não precisa pagar o custo de mais
uma chamada RPC (getContractAddress) só para redescobrir um valor que já
não muda (o contrato não é upgradeable neste MVP, até onde sabemos). Se o
endereço mudar (redeploy), esta constante precisa ser atualizada aqui e em
todo outro script que a hardcoded — mesmo trade-off já aceito no resto do
projeto.
"""
import logging
import os

from requests.exceptions import ConnectionError as _ReqConnectionError
from requests.exceptions import Timeout as _ReqTimeout
from web3 import Web3
from web3.exceptions import TimeExhausted

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

RPC_URL = "http://localhost:8545"
EXPECTED_CHAIN_ID = 648

# Endereço já CONFIRMADO em docs/referencia-contratos/README.md, seção 5.
TRANSACTION_ORCHESTRATOR_ADDRESS = "0x959922bE3CAee4b8Cd9a407cc3ac1C251C2007B1"

CHANNEL_NAME = "mel-trust"
PROCESS_ID = "honey-batch-registration"
STAGE_ID = "creation"

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
BESU_KEY_PATH = os.environ.get(
    "BESU_KEY_PATH", os.path.join(_THIS_DIR, "keys", "coop-b-besu.key")
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
    """Carrega a chave Besu do nó e monta o objeto de contrato, com cache
    de módulo. Levanta exceção se a chave não existir/estiver vazia, ou se
    o RPC não responder — o chamador (`anchor_batch`) é responsável por
    capturar isso."""
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


def anchor_batch(batch_number: str, nature_id: str, data_hash: bytes) -> tuple[bool, str | None, str | None]:
    """Ancora o lote `batch_number` on-chain via
    `TransactionOrchestrator.submitTransaction`.

    `data_hash` é o keccak256 (32 bytes) do MESMO grafo rdflib em memória
    recém-inserido no Fuseki (serializado como N-Triples) — não re-consulta
    o Fuseki, para não ancorar um estado potencialmente diferente do que
    acabou de ser gravado.

    `nature_id` é o `productType` do lote (ex.: "HoneyApis",
    "HoneyNativeStinglessBee") — usado como `natureId` do
    TransactionRequest, casando com os processos `honey-batch-registration`
    já registrados no ProcessRegistry para esses dois natureId.

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

        new_asset_id = Web3.keccak(text=batch_number)

        operation_data = (
            1,                    # amount
            "",                   # idLocal
            [data_hash],          # dataHashes
            "0x0000000000000000000000000000000000000000",  # newOwner = address(0)
            [],                   # amounts
            b"\x00" * 32,         # parentAssetId = bytes32(0)
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
                "Lote %r ancorado on-chain com sucesso. tx_hash=%s bloco=%s",
                batch_number, tx_hash_hex, receipt.blockNumber,
            )
            return True, tx_hash_hex, None

        error_msg = f"receipt.status == 0 (revert on-chain). tx_hash={tx_hash_hex}"
        logger.error("Falha ao ancorar lote %r on-chain: %s", batch_number, error_msg)
        return False, None, error_msg

    except Exception as exc:  # noqa: BLE001 - nunca deixa exceção escapar (rede/revert/timeout/gas)
        error_msg = _describe_rpc_error(exc)
        logger.error("Erro ao ancorar lote %r on-chain: %s", batch_number, error_msg)
        return False, None, error_msg


# --- Leitura on-chain (outbox/retry) -------------------------------------
# Mesmo caminho de leitura de verifier-demo/onchain_verification.py:
# AssetRegistry resolvido AO VIVO via AddressDiscovery (nunca hardcoded);
# `getAsset(channel, assetId)` é `view` (sem conta, sem gas, sem tx).
ADDRESS_DISCOVERY_ADDRESS = "0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e"

_ADDRESS_DISCOVERY_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "nameHash", "type": "bytes32"}],
        "name": "getContractAddress",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Struct Asset (16 campos) — ordem/tipos iguais a onchain_verification.py
# (CONFIRMADOS em docs/referencia-contratos/README.md seção 6).
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


def _get_onchain_data_hash_raw(batch_number: str) -> tuple[bool, bytes | None]:
    """Lê `AssetRegistry.getAsset(CHANNEL_NAME, keccak256(batch_number))`.

    Retorna `(exists, dataHashes[0] | None)`. `exists=True` com hash `None`
    significa asset sem dataHashes (tratado como divergência pelo chamador).

    DIFERENTE de `anchor_batch`, esta função LEVANTA exceção em erro de
    rede/RPC: o retry precisa distinguir "asset não existe" (False, None —
    seguro reenviar) de "não consegui consultar" (NÃO pode reenviar às
    cegas, senão duplica a transação que talvez já exista)."""
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

    asset, exists = _cached_asset_registry.functions.getAsset(
        CHANNEL_NAME, Web3.keccak(text=batch_number)
    ).call()
    if not exists:
        return False, None
    data_hashes = asset[7]
    return True, (bytes(data_hashes[0]) if data_hashes else None)


def get_onchain_data_hash(batch_number: str) -> tuple[bool, bytes | None]:
    """Ver `_get_onchain_data_hash_raw`. Timeout/conexão recusada no RPC viram
    ConnectionError com a mensagem "RPC/túnel não respondeu"."""
    try:
        return _get_onchain_data_hash_raw(batch_number)
    except (_ReqConnectionError, _ReqTimeout, TimeExhausted) as exc:
        raise ConnectionError(_describe_rpc_error(exc)) from exc
