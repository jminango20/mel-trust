"""
test_submit_transaction.py

Testa `submitTransaction` no contrato TransactionOrchestrator, na rede Semear
(Besu privado, chain ID 648, zero gas, acessivel via tunel SSH em
http://localhost:8545). Objetivo UNICO desta rodada: descobrir se a
assinatura descrita pelo usuario (linguagem natural, NUNCA vista em .sol
real) funciona contra o node de verdade, ANTES de tocar em
coop-a/lote_endpoint.py ou coop-b/lote_endpoint.py.

Uso:
    python scripts/blockchain/test_submit_transaction.py

Pre-requisitos:
    - Tunel SSH ativo expondo o RPC do Besu em http://localhost:8545.
    - Dependencias instaladas (ver scripts/blockchain/requirements.txt).
    - Arquivo `coop-a/keys/coop-a-besu.key` presente (mesmo formato ja
      confirmado nos outros scripts desta pasta). Conta ja confirmada membro
      do canal "mel-trust" em create_batch_registration_processes.py — esta
      rodada NAO reconfirma isChannelMember de novo (mesma decisao de
      create_process.py ao reaproveitar a confirmacao do certifier).

ORIGEM DA STRUCT TransactionRequest E DA FUNCAO submitTransaction — DISCLAIMER
--------------------------------------------------------------------------------
NADA nesta secao foi confirmado contra codigo-fonte Solidity real. Nao existe
nenhum `TransactionOrchestrator.sol` neste repositorio (ver CLAUDE.md e
docs/referencia-contratos/README.md secao 5, ainda marcada NAO CONFIRMADO
antes desta rodada).

A struct TransactionRequest e a assinatura de submitTransaction foram dadas
pelo usuario via chat, em linguagem natural, EXPLICITAMENTE como suposicao a
testar, nao como fato:

    struct OperationData {
        uint256 amount;
        string idLocal;
        bytes32[] dataHashes;
    }

    struct TransactionRequest {
        bytes32[] newAssetIds;
        bytes32[] targetAssetIds;
        string processId;
        string natureId;
        string stageId;
        string channelName;
        OperationData operationData;
    }

    function submitTransaction(TransactionRequest calldata request)
        external returns (bytes32 txId);
    // OU sem retorno -- assinatura exata desconhecida, ver abaixo.

Pontos especificamente marcados como incertos pelo usuario:
  - O tipo de retorno (`bytes32 txId`) pode nao existir (funcao pode ser
    `void`). Este script tenta primeiro com outputs=[bytes32]; se a
    montagem/estimativa da transacao falhar de um jeito que sugira mismatch
    de outputs, tenta de novo com outputs=[] antes de desistir -- mas ambas
    as tentativas usam os MESMOS inputs (nunca um nome de funcao alternativo
    sem confirmacao humana).
  - `amount` exige > 0 em CreateAssetInput (regra de negocio citada pelo
    usuario, nao verificada aqui) -- por isso o script usa amount=1, nao 0,
    para nao confundir "revert por amount=0" com "revert por struct errada".
  - `idLocal` como string vazia "" -- por decisao do usuario (localizacao
    estruturada nao modelada ainda neste MVP).

O QUE E REALMENTE CONFIRMADO SEM DEPENDER DE NENHUMA SUPOSICAO DE ABI
--------------------------------------------------------------------------
O tx_hash retornado por `w3.eth.send_raw_transaction()` (== o hash da
propria transacao Ethereum assinada, == `receipt.transactionHash`) NAO
depende de nenhuma suposicao sobre o ABI do contrato -- e um hash calculado
sobre os bytes da transacao assinada, valido independente de o calldata
bater ou nao com a funcao real do contrato. Por isso este script sempre
imprime esse hash, mesmo que a chamada reverta (nesse caso receipt.status=0,
mas o hash da tx em si e real, so nao representa uma mudanca de estado bem
sucedida).

Qualquer RETORNO de funcao (txId) OU EVENTO emitido pelo contrato, por outro
lado, dependem inteiramente do ABI estar certo -- se estiver, o script
documenta o valor decodificado; se a decodificacao falhar ou nao houver
retorno, o script relata isso claramente, sem inventar.

FLUXO DESTE SCRIPT (resumo)
-----------------------------
1. Confirma tunel: is_connected() e chain_id==648. Se cair no meio, para e
   reporta.
2. Carrega a chave da coop-a (mesmo arquivo/formato dos outros scripts).
3. Resolve o endereco do TransactionOrchestrator -- usa o valor JA
   confirmado em docs/referencia-contratos/README.md secao 5
   (0x959922bE3CAee4b8Cd9a407cc3ac1C251C2007B1, getVersion()=="1.0.0"), sem
   reconsultar AddressDiscovery (mesmo padrao de create_process.py com
   ProcessRegistry -- endereco ja confirmado, nao precisa redescobrir).
4. Monta um TransactionRequest de teste:
     - batchNumber sintetico "TEST-ANCORAGEM-<uuid8>"
     - newAssetIds = [keccak256(batchNumber)]
     - targetAssetIds = []
     - processId = "honey-batch-registration" (ja existe on-chain pra
       natureId HoneyApis, criado em create_batch_registration_processes.py)
     - natureId = "HoneyApis"
     - stageId = "creation"
     - channelName = "mel-trust"
     - operationData.amount = 1
     - operationData.idLocal = ""
     - operationData.dataHashes = [keccak256("teste")]
5. Tenta `submitTransaction(request)`. Primeiro faz uma chamada `.call()`
   (nao muda estado) para tentar ler o valor de retorno sem gastar uma tx de
   verdade; se isso funcionar, mostra o valor decodificado. Independente do
   resultado do `.call()`, monta e ENVIA a transacao de verdade
   (estimate_gas -> build_transaction -> sign -> send_raw_transaction ->
   wait_for_transaction_receipt), mesmo padrao dos outros scripts desta
   pasta.
6. Se reverter: reporta o erro exato tal como veio do node, e PARA -- nao
   tenta nome alternativo (ex.: "createTransaction", "registerTransaction")
   sem confirmacao humana.
7. Se funcionar (receipt.status==1): confirma o tx_hash real, imprime
   qualquer log/evento bruto do receipt (sem decodificar, a menos que o
   valor de retorno do .call() acima tenha sugerido um layout), e reporta
   sucesso.

Erros (revert por selector/struct incorreto, falta de role/permissao,
timeout de RPC, etc.) sao deixados aparecer no output real, nao sao
mascarados nem engolidos silenciosamente.
"""

import sys
import uuid
from pathlib import Path

try:
    from web3 import Web3
    from eth_account import Account
except ImportError:
    print(
        "ERRO: pacote 'web3' (ou 'eth_account') nao encontrado neste "
        "interpretador Python.\n"
        "Instale com: pip install -r scripts/blockchain/requirements.txt\n"
        f"Interpretador atual: {sys.executable}",
        file=sys.stderr,
    )
    sys.exit(1)


RPC_URL = "http://127.0.0.1:8545"
EXPECTED_CHAIN_ID = 648

# Endereco JA CONFIRMADO em docs/referencia-contratos/README.md secao 5
# (getVersion() == "1.0.0"). Este script nao resolve de novo via
# AddressDiscovery -- mesmo padrao de create_process.py.
TRANSACTION_ORCHESTRATOR_ADDRESS = "0x959922bE3CAee4b8Cd9a407cc3ac1C251C2007B1"

# Chave Besu da coop-a -- ja confirmada membro do canal "mel-trust" em
# create_batch_registration_processes.py (mesma sessao). Nao reconfirmado de
# novo aqui.
COOP_A_KEY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "coop-a" / "keys" / "coop-a-besu.key"
)

CHANNEL_NAME = "mel-trust"
PROCESS_ID = "honey-batch-registration"
NATURE_ID = "HoneyApis"
STAGE_ID = "creation"

# OperationData -- SUPOSICAO do usuario, ver disclaimer no topo do arquivo.
OPERATION_DATA_COMPONENTS = [
    {"internalType": "uint256", "name": "amount", "type": "uint256"},
    {"internalType": "string", "name": "idLocal", "type": "string"},
    {"internalType": "bytes32[]", "name": "dataHashes", "type": "bytes32[]"},
]

# TransactionRequest -- SUPOSICAO do usuario, ver disclaimer no topo do
# arquivo.
TRANSACTION_REQUEST_COMPONENTS = [
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
        "components": OPERATION_DATA_COMPONENTS,
    },
]

# Duas variantes de ABI para submitTransaction: com retorno bytes32 (txId) e
# sem retorno nenhum. Tentadas nessa ordem -- primeiro a que o usuario
# descreveu como mais provavel (com retorno).
SUBMIT_TRANSACTION_ABI_WITH_RETURN = [
    {
        "inputs": [
            {
                "internalType": "struct TransactionRequest",
                "name": "request",
                "type": "tuple",
                "components": TRANSACTION_REQUEST_COMPONENTS,
            }
        ],
        "name": "submitTransaction",
        "outputs": [{"internalType": "bytes32", "name": "txId", "type": "bytes32"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

SUBMIT_TRANSACTION_ABI_NO_RETURN = [
    {
        "inputs": [
            {
                "internalType": "struct TransactionRequest",
                "name": "request",
                "type": "tuple",
                "components": TRANSACTION_REQUEST_COMPONENTS,
            }
        ],
        "name": "submitTransaction",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def load_coop_a_private_key() -> str:
    """Carrega a chave privada Besu da coop-a. Mesmo formato/leitura dos
    outros scripts desta pasta. Nunca imprime a chave -- so o endereco
    derivado dela."""
    if not COOP_A_KEY_PATH.is_file():
        print(
            f"ERRO: arquivo de chave da coop-a nao encontrado em "
            f"{COOP_A_KEY_PATH}.",
            file=sys.stderr,
        )
        sys.exit(1)

    private_key = COOP_A_KEY_PATH.read_text(encoding="utf-8").strip()
    if not private_key:
        print(
            f"ERRO: arquivo de chave da coop-a em {COOP_A_KEY_PATH} esta "
            "vazio.",
            file=sys.stderr,
        )
        sys.exit(1)
    return private_key


def main() -> None:
    print("=" * 70)
    print("TESTE DE submitTransaction NO TransactionOrchestrator")
    print("=" * 70)
    print(f"RPC URL: {RPC_URL}")
    print(f"TransactionOrchestrator: {TRANSACTION_ORCHESTRATOR_ADDRESS}")
    print()

    private_key = load_coop_a_private_key()
    account = Account.from_key(private_key)
    print(f"Conta derivada de coop-a/keys/coop-a-besu.key: {account.address}")
    print("(a chave privada em si nunca e impressa)")
    print()

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))

    connected = w3.is_connected()
    print(f"is_connected(): {connected}")
    if not connected:
        print("PARANDO: tunel SSH nao esta ativo (is_connected() == False).")
        sys.exit(1)
    try:
        chain_id = w3.eth.chain_id
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao consultar chain_id (tunel pode ter caido): {type(exc).__name__}: {exc}")
        sys.exit(1)
    print(f"chain_id lido: {chain_id} (esperado: {EXPECTED_CHAIN_ID})")
    if chain_id != EXPECTED_CHAIN_ID:
        print(
            f"PARANDO: chain_id ({chain_id}) diferente do esperado "
            f"({EXPECTED_CHAIN_ID})."
        )
        sys.exit(1)
    print()

    # --- Monta o TransactionRequest de teste ---
    batch_number = f"TEST-ANCORAGEM-{uuid.uuid4().hex[:8]}"
    new_asset_id = Web3.keccak(text=batch_number)
    data_hash = Web3.keccak(text="teste")

    operation_data = (
        1,       # amount -- CreateAssetInput exige > 0 (regra citada pelo usuario)
        "",      # idLocal -- localizacao estruturada nao modelada ainda
        [data_hash],  # dataHashes
    )
    transaction_request = (
        [new_asset_id],   # newAssetIds
        [],                # targetAssetIds
        PROCESS_ID,
        NATURE_ID,
        STAGE_ID,
        CHANNEL_NAME,
        operation_data,
    )

    print("=" * 70)
    print("TransactionRequest de teste a ser enviado")
    print("=" * 70)
    print(f"  batchNumber (sintetico, so pra gerar newAssetIds): {batch_number!r}")
    print(f"  newAssetIds:      [keccak256({batch_number!r})] = [{new_asset_id.hex()}]")
    print("  targetAssetIds:   []")
    print(f"  processId:        {PROCESS_ID!r}")
    print(f"  natureId:         {NATURE_ID!r}")
    print(f"  stageId:          {STAGE_ID!r}")
    print(f"  channelName:      {CHANNEL_NAME!r}")
    print("  operationData:")
    print("    amount:         1")
    print("    idLocal:        '' (vazio)")
    print(f"    dataHashes:     [keccak256('teste')] = [{data_hash.hex()}]")
    print()

    def attempt(abi, label):
        print("=" * 70)
        print(f"TENTATIVA: submitTransaction, ABI '{label}'")
        print("=" * 70)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(TRANSACTION_ORCHESTRATOR_ADDRESS), abi=abi
        )
        fn = contract.functions.submitTransaction

        # Preview via .call() -- nao muda estado, so tenta ler o retorno
        # (se a ABI declarar um). Erros aqui NAO interrompem o fluxo: a tx
        # de verdade e tentada de qualquer jeito logo abaixo.
        try:
            preview = fn(transaction_request).call({"from": account.address})
            print(f"  Preview via .call() (nao muda estado): retorno = {preview!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Preview via .call() falhou (nao fatal, so informativo): {type(exc).__name__}: {exc}")
        print()

        try:
            nonce = w3.eth.get_transaction_count(account.address)
            gas_price = w3.eth.gas_price
            print(f"  nonce: {nonce}")
            print(f"  gas_price lido de w3.eth.gas_price: {gas_price}")

            estimated_gas = fn(transaction_request).estimate_gas({"from": account.address})
            print(f"  gas estimado (estimate_gas): {estimated_gas}")

            tx = fn(transaction_request).build_transaction(
                {
                    "from": account.address,
                    "nonce": nonce,
                    "chainId": EXPECTED_CHAIN_ID,
                    "gas": estimated_gas,
                    "gasPrice": gas_price,
                }
            )
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            error_text = f"{type(exc).__name__}: {exc}"
            print(f"  ERRO ao estimar gas ou montar a transacao: {error_text}")
            print()
            return False, error_text, None, None

        try:
            signed_tx = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print(f"  Transacao enviada. hash: {tx_hash.hex()}")
            print(
                "  (este hash e SEMPRE real e independente de qualquer "
                "suposicao de ABI -- e o hash da propria tx assinada, "
                "== receipt.transactionHash)"
            )

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            status_text = "SUCESSO (status=1)" if receipt.status == 1 else "FALHA (status=0)"
            print(f"  Receipt recebido. status: {receipt.status} -> {status_text}")
            print(f"  Receipt bruto (logs, sem decodificar): {receipt.logs}")
            print()

            if receipt.status == 1:
                return True, receipt, tx_hash, None
            else:
                return False, f"receipt.status == 0 (revert on-chain), tx_hash={tx_hash.hex()}", None, tx_hash
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            error_text = f"{type(exc).__name__}: {exc}"
            print(f"  ERRO ao assinar/enviar a transacao, ou revert on-chain: {error_text}")
            print()
            return False, error_text, None, None

    success, result, tx_hash, revert_tx_hash = attempt(SUBMIT_TRANSACTION_ABI_WITH_RETURN, "com retorno bytes32 txId")

    if not success:
        print("=" * 70)
        print(
            "ABI 'com retorno' falhou/reverteu. Tentando de novo com ABI "
            "'sem retorno' (outputs=[]) -- MESMOS inputs, mesmo nome de "
            "funcao, so muda a declaracao de outputs (nao afeta o encoding "
            "da chamada em si, so a decodificacao de retorno em .call())."
        )
        print("=" * 70)
        error_with_return = result
        success2, result2, tx_hash2, revert_tx_hash2 = attempt(SUBMIT_TRANSACTION_ABI_NO_RETURN, "sem retorno")
        if not success2:
            print("=" * 70)
            print("FALHA: as duas tentativas de submitTransaction reverteram/falharam.")
            print("=" * 70)
            print(f"  com retorno: {error_with_return}")
            print(f"  sem retorno: {result2}")
            print()
            print(
                "PARANDO. Nao tentando nome de funcao alternativo "
                "(ex.: createTransaction/registerTransaction) sem "
                "confirmacao humana -- mesma disciplina dos outros scripts "
                "desta pasta."
            )
            sys.exit(1)
        success, result, tx_hash = success2, result2, tx_hash2
        abi_used_label = "sem retorno"
    else:
        abi_used_label = "com retorno bytes32 txId"

    receipt = result
    print("=" * 70)
    print(f"SUCESSO com submitTransaction (ABI '{abi_used_label}').")
    print("=" * 70)
    print(f"  tx_hash (real, hash da propria tx Ethereum): {tx_hash.hex()}")
    print(f"  receipt.status: {receipt.status}")
    print(f"  receipt.blockNumber: {receipt.blockNumber}")
    print(f"  receipt.logs (bruto, {len(receipt.logs)} entradas): {receipt.logs}")
    print()
    print(
        "Este tx_hash NAO depende de nenhuma suposicao de ABI estar certa -- "
        "e valido e verificavel via getTransactionReceipt no node, "
        "independente do struct TransactionRequest bater exatamente com o "
        "real."
    )


if __name__ == "__main__":
    main()
