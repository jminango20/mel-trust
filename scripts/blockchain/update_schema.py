"""
update_schema.py

Registra a versao 2 do schema "honey-validation-shapes" via
SchemaRegistry.updateSchema(SchemaUpdateInput), assinada pela conta do certifier.

Assinatura exata (docs/referencia-contratos/ISchemaRegistry.sol):
    struct SchemaUpdateInput { string schemaId; bytes32 newDataHash; string channelName; string description; }
    function updateSchema(SchemaUpdateInput calldata schema) external;   // sem retorno

Fluxo (para em qualquer pre-condicao falsa, sem enviar nada):
  1. isChannelMember("mel-trust", certifier) == True
  2. getActiveSchema: owner == certifier, versao 1, status 0 (ACTIVE)
  3. simulacao com .call() a partir do certifier; se reverter, PARA e imprime o erro exato
  4. envia a tx (timeout de recibo 60 s); imprime txHash e status
  5. verifica: getActiveSchema -> versao 2, ACTIVE, dataHash == canonical_schema_hash;
     getSchemaByVersion(..., 1) -> DEPRECATED (status 1)

Uso: python scripts/blockchain/update_schema.py
"""
import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import create_schema as cs  # noqa: E402  (reusa constantes, ABI de leitura e canonical_schema_hash)

RPC_URL = "http://127.0.0.1:8545"
RECEIPT_TIMEOUT_S = 60
SCHEMA_ID = cs.SCHEMA_ID
CHANNEL = cs.CHANNEL_NAME
DESCRIPTION_V2 = (
    "SHACL shapes para validação de lotes de mel (IN 11/2000, Portaria 795/2023, 1.439/2025). "
    "v2: CertificateShape adicionada; mensagem sh:Info de HoneyNativeMoistureShape corrigida."
)

UPDATE_ABI = [{
    "inputs": [{
        "internalType": "struct ISchemaRegistry.SchemaUpdateInput", "name": "schema", "type": "tuple",
        "components": [
            {"internalType": "string", "name": "schemaId", "type": "string"},
            {"internalType": "bytes32", "name": "newDataHash", "type": "bytes32"},
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "string", "name": "description", "type": "string"},
        ],
    }],
    "name": "updateSchema", "outputs": [], "stateMutability": "nonpayable", "type": "function",
}]
GET_BY_VERSION_ABI = [{
    "inputs": [
        {"internalType": "string", "name": "channelName", "type": "string"},
        {"internalType": "string", "name": "schemaId", "type": "string"},
        {"internalType": "uint256", "name": "version", "type": "uint256"},
    ],
    "name": "getSchemaByVersion",
    "outputs": [
        {"internalType": "struct Schema", "name": "schema", "type": "tuple", "components": cs.SCHEMA_STRUCT_COMPONENTS},
        {"internalType": "bool", "name": "exists", "type": "bool"},
    ],
    "stateMutability": "view", "type": "function",
}]


def hx(b):
    return "0x" + bytes(b).hex()


def stop(msg):
    print(f"PARANDO: {msg}")
    sys.exit(1)


def main():
    account = Account.from_key(cs.load_certifier_private_key())
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
    print(f"chain_id: {w3.eth.chain_id}  certifier: {account.address}")
    addr = Web3.to_checksum_address(cs.SCHEMA_REGISTRY_ADDRESS)
    reg = w3.eth.contract(address=addr, abi=cs.GET_ACTIVE_SCHEMA_ABI + UPDATE_ABI + GET_BY_VERSION_ABI)

    new_hash = cs.canonical_schema_hash(cs.VALIDATION_SHAPES_PATH)
    print(f"newDataHash (canonico): {hx(new_hash)}")

    # 1. membership
    acm = w3.eth.contract(address=Web3.to_checksum_address(cs.ACCESS_CHANNEL_MANAGER_ADDRESS), abi=cs.IS_CHANNEL_MEMBER_ABI)
    member = acm.functions.isChannelMember(CHANNEL, account.address).call()
    print(f"[1] isChannelMember = {member}")
    if not member:
        stop("certifier nao e membro do canal")

    # 2. estado antes
    s, exists = reg.functions.getActiveSchema(CHANNEL, SCHEMA_ID).call()
    print(f"[2] antes: exists={exists} owner={s[4]} version={s[2]} status={s[6]} dataHash={hx(s[3])}")
    if not (exists and s[4] == account.address and s[2] == 1 and s[6] == 0):
        stop("estado inicial nao e (owner=certifier, versao 1, status 0)")
    if bytes(s[3]) == bytes(new_hash):
        stop("dataHash on-chain ja igual ao canonico; nada a atualizar")

    # 3. simulacao
    arg = (SCHEMA_ID, new_hash, CHANNEL, DESCRIPTION_V2)
    fn = reg.functions.updateSchema(arg)
    try:
        fn.call({"from": account.address})
    except Exception as exc:  # noqa: BLE001 - erro exato, sem mascarar
        print(f"[3] SIMULACAO REVERTEU: {type(exc).__name__}: {exc}")
        stop("simulacao falhou; nada enviado")
    print("[3] simulacao .call() OK (sem revert)")

    # 4. envio
    tx = fn.build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": cs.EXPECTED_CHAIN_ID,
        "gas": fn.estimate_gas({"from": account.address}),
        "gasPrice": w3.eth.gas_price,
    })
    tx_hash = w3.eth.send_raw_transaction(account.sign_transaction(tx).raw_transaction)
    print(f"[4] txHash: {hx(tx_hash)}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT_S)
    print(f"[4] receipt.status = {receipt.status}  block = {receipt.blockNumber}")
    if receipt.status != 1:
        stop("transacao reverteu on-chain")

    # 5. verificacao
    a, ex_a = reg.functions.getActiveSchema(CHANNEL, SCHEMA_ID).call()
    print(f"[5] ativo: exists={ex_a} version={a[2]} status={a[6]} dataHash={hx(a[3])} description={a[9]!r}")
    v1, ex_1 = reg.functions.getSchemaByVersion(CHANNEL, SCHEMA_ID, 1).call()
    print(f"[5] v1: exists={ex_1} version={v1[2]} status={v1[6]} dataHash={hx(v1[3])}")
    ok = ex_a and a[2] == 2 and a[6] == 0 and bytes(a[3]) == bytes(new_hash) and ex_1 and v1[6] == 1
    print("VERIFICACAO:", "OK (v2 ACTIVE com hash canonico; v1 DEPRECATED)" if ok else "DIVERGENTE")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
