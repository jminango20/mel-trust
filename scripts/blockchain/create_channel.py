"""
create_channel.py

Cria o canal "mel-trust" no contrato AccessChannelManager, na rede Semear
(Besu privado, chain ID 648, zero gas, acessivel via tunel SSH em
http://localhost:8545).

Uso:
    python scripts/blockchain/create_channel.py

Pre-requisitos:
    - Tunel SSH ativo expondo o RPC do Besu em http://localhost:8545.
    - Dependencias instaladas (ver scripts/blockchain/requirements.txt),
      incluindo `python-dotenv` (usado para ler a chave privada do .env).
    - Arquivo `scripts/blockchain/.env` com a variavel DEPLOYER_PRIVATE_KEY
      preenchida com uma chave privada real que tenha CHANNEL_AUTHORITY_ROLE
      no AccessChannelManager. So existe `.env.example` (sem valor) neste
      repo — o `.env` real precisa ser criado e preenchido por fora deste
      script, e ele NUNCA e commitado (.gitignore raiz ja cobre `.env` em
      qualquer pasta).

ORIGEM DAS ASSINATURAS USADAS NESTE SCRIPT
-------------------------------------------
As duas assinaturas abaixo foram fornecidas pelo usuario diretamente por
texto nesta sessao de chat, como "assinaturas EXATAS". Nao existe nenhum
arquivo `AccessChannelManager.sol` neste repositorio (mel-trust) — o usuario
mencionou que essas assinaturas vieram de um "repositorio de referencia",
mas nenhum `.sol` foi de fato colado nesta conversa, e nenhum `.sol` desse
contrato foi lido pelo agente. Portanto o ABI minimo abaixo NAO foi
confirmado contra codigo-fonte real; e apenas a traducao direta, campo a
campo, do que o usuario descreveu:

    function createChannel(string calldata channelName)
        external onlyRole(CHANNEL_AUTHORITY_ROLE)
    // 1 argumento (string), SEM retorno. Emite ChannelCreated(channelHash,
    // creator, timestamp) em sucesso (o evento nao e decodificado aqui —
    // so citado para contexto; o script confirma sucesso via receipt.status
    // e via nova chamada a getChannelInfo).

    function getChannelInfo(string calldata channelName) external view
        returns (bool exists, bool isActive, address creator,
                 uint256 memberCount, uint256 createdAt)
    // retorna tupla de 5 valores, nessa ordem exata.

Se a assinatura real no contrato deployado for diferente (outra ordem de
campos, outro tipo, `onlyRole` com uma role diferente da assumida, etc.), a
chamada abaixo vai falhar/reverter com o erro real do node — esse erro deve
aparecer no output tal como veio, sem ser mascarado nem escondido.

ENDERECO USADO
---------------
AccessChannelManager: 0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0

Mesmo endereco ja usado em check_addresses.py, onde foi confirmado (rodada
anterior, nao repetido aqui) que tem bytecode real deployado e responde a
hasRole() (interface OpenZeppelin AccessControl). Este script NAO resolve o
endereco de novo via AddressDiscovery — usa o valor ja confirmado, conforme
instrucao do usuario nesta tarefa.

POR QUE gasPrice = w3.eth.gas_price EM VEZ DE 0 HARDCODADO
-------------------------------------------------------------
O contexto do projeto (CLAUDE.md / instrucoes desta sessao) descreve a rede
Semear como "zero gas". Ainda assim, este script LE o gas price atual da
rede via `w3.eth.gas_price` em vez de hardcodar `0` na transacao. Duas
razoes:
  1. E o valor real que o node esta reportando agora, nao uma suposicao
     nossa sobre a configuracao da rede — se a rede mudar de politica (ou
     se "zero gas" nao significar literalmente gasPrice=0 no client Besu
     usado), o script continua correto sem precisar ser editado.
  2. Mesmo que o valor lido seja 0 (o que e esperado nesta rede), fica
     documentado no output QUAL foi o valor realmente usado na transacao,
     em vez de mascarar isso com uma constante fixa no codigo.

FLUXO DESTE SCRIPT (resumo)
-----------------------------
1. Carrega DEPLOYER_PRIVATE_KEY do .env local (nunca hardcoded, nunca
   impresso — so o endereco publico derivado dela aparece no output).
2. Chama getChannelInfo("mel-trust") ANTES de qualquer transacao.
   - Se o canal ja existe (exists == True): imprime os 5 campos e um aviso
     claro de que nada sera enviado, e o script TERMINA ali, sem montar
     nem enviar transacao alguma.
3. Se nao existe (exists == False): monta, assina e envia
   createChannel("mel-trust") (nonce via get_transaction_count, chain_id
   648, gas via estimate_gas, gasPrice via w3.eth.gas_price), espera o
   receipt e imprime hash + status traduzido (sucesso/falha).
4. Se a transacao foi enviada, chama getChannelInfo("mel-trust") de novo e
   imprime os 5 campos, para provar que o canal passou a existir e esta
   ativo.

Erros (revert por falta de CHANNEL_AUTHORITY_ROLE, revert em
getChannelInfo, timeout de RPC, etc.) sao deixados aparecer no output real,
nao sao mascarados nem engolidos silenciosamente.

IMPORTANTE: este arquivo so foi criado, NAO foi executado pelo agente que o
escreveu. O .env real ainda nao tem uma chave privada valida — a execucao
fica a cargo do usuario, depois de revisar e preencher essa chave.
"""

import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    print(
        "ERRO: pacote 'python-dotenv' nao encontrado neste interpretador "
        "Python.\nInstale com: pip install -r scripts/blockchain/requirements.txt\n"
        f"Interpretador atual: {sys.executable}",
        file=sys.stderr,
    )
    sys.exit(1)

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

import os


RPC_URL = "http://localhost:8545"
EXPECTED_CHAIN_ID = 648

ACCESS_CHANNEL_MANAGER_ADDRESS = "0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0"
CHANNEL_NAME = "mel-trust"

# ABI minimo, montado a partir das assinaturas fornecidas pelo usuario via
# chat nesta sessao (ver disclaimer completo no topo do arquivo). NAO vem de
# nenhum .sol lido neste repo.
ACCESS_CHANNEL_MANAGER_ABI = [
    {
        "inputs": [{"internalType": "string", "name": "channelName", "type": "string"}],
        "name": "createChannel",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "string", "name": "channelName", "type": "string"}],
        "name": "getChannelInfo",
        "outputs": [
            {"internalType": "bool", "name": "exists", "type": "bool"},
            {"internalType": "bool", "name": "isActive", "type": "bool"},
            {"internalType": "address", "name": "creator", "type": "address"},
            {"internalType": "uint256", "name": "memberCount", "type": "uint256"},
            {"internalType": "uint256", "name": "createdAt", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


def load_private_key() -> str:
    """Carrega DEPLOYER_PRIVATE_KEY do .env em scripts/blockchain/.env.

    Nunca imprime a chave. Falha com mensagem clara se a variavel nao
    estiver definida (ex.: .env nao existe ainda, ou existe mas so tem
    a linha vazia copiada do .env.example).
    """
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path)

    private_key = os.environ.get("DEPLOYER_PRIVATE_KEY", "").strip()
    if not private_key:
        print(
            "ERRO: DEPLOYER_PRIVATE_KEY nao esta definida (ou esta vazia) em "
            f"{env_path}.\n"
            "Preencha scripts/blockchain/.env com uma chave privada real "
            "antes de rodar este script (ver .env.example para o formato).",
            file=sys.stderr,
        )
        sys.exit(1)
    return private_key


def print_channel_info(label: str, info: tuple) -> None:
    exists, is_active, creator, member_count, created_at = info
    print(f"--- getChannelInfo('{CHANNEL_NAME}') [{label}] ---")
    print(f"  exists:      {exists}")
    print(f"  isActive:    {is_active}")
    print(f"  creator:     {creator}")
    print(f"  memberCount: {member_count}")
    print(f"  createdAt:   {created_at}")
    print()


def main() -> None:
    print("=" * 70)
    print(f"CRIACAO DE CANAL '{CHANNEL_NAME}' NO AccessChannelManager")
    print("=" * 70)
    print(f"RPC URL: {RPC_URL}")
    print(f"AccessChannelManager: {ACCESS_CHANNEL_MANAGER_ADDRESS}")
    print()

    private_key = load_private_key()
    account = Account.from_key(private_key)
    print(f"Conta derivada de DEPLOYER_PRIVATE_KEY: {account.address}")
    print("(a chave privada em si nunca e impressa)")
    print()

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))

    connected = w3.is_connected()
    print(f"is_connected(): {connected}")
    chain_id = w3.eth.chain_id
    print(f"chain_id lido: {chain_id} (esperado: {EXPECTED_CHAIN_ID})")
    if chain_id != EXPECTED_CHAIN_ID:
        print(
            f"AVISO: chain_id ({chain_id}) diferente do esperado "
            f"({EXPECTED_CHAIN_ID}). Continuando mesmo assim, mas isso deve "
            "ser investigado antes de confiar no resultado."
        )
    print()

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(ACCESS_CHANNEL_MANAGER_ADDRESS),
        abi=ACCESS_CHANNEL_MANAGER_ABI,
    )

    print("=" * 70)
    print("PASSO 1: getChannelInfo() ANTES de qualquer transacao")
    print("=" * 70)
    try:
        info_before = contract.functions.getChannelInfo(CHANNEL_NAME).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getChannelInfo(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_channel_info("antes", info_before)

    exists_before = info_before[0]
    if exists_before:
        print(
            f"AVISO: o canal '{CHANNEL_NAME}' JA EXISTE. Nada sera enviado — "
            "encerrando sem montar nenhuma transacao."
        )
        return

    print(
        f"Canal '{CHANNEL_NAME}' ainda nao existe. Prosseguindo para montar "
        "e enviar createChannel()."
    )
    print()

    print("=" * 70)
    print("PASSO 2: montar, assinar e enviar createChannel()")
    print("=" * 70)
    try:
        nonce = w3.eth.get_transaction_count(account.address)
        # Rede zero-gas (Semear/Besu privado) — lemos o gas_price real do
        # node em vez de hardcodar 0 (ver disclaimer no topo do arquivo).
        gas_price = w3.eth.gas_price
        print(f"nonce: {nonce}")
        print(f"gas_price lido de w3.eth.gas_price: {gas_price}")

        estimated_gas = contract.functions.createChannel(CHANNEL_NAME).estimate_gas(
            {"from": account.address}
        )
        print(f"gas estimado (estimate_gas): {estimated_gas}")

        tx = contract.functions.createChannel(CHANNEL_NAME).build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": EXPECTED_CHAIN_ID,
                "gas": estimated_gas,
                "gasPrice": gas_price,
            }
        )
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(
            "ERRO ao estimar gas ou montar a transacao de createChannel(). "
            "Se a conta nao tiver CHANNEL_AUTHORITY_ROLE, o revert deve "
            f"aparecer aqui: {type(exc).__name__}: {exc}"
        )
        sys.exit(1)

    try:
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"Transacao enviada. hash: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        status_text = "SUCESSO (status=1)" if receipt.status == 1 else "FALHA (status=0)"
        print(f"Receipt recebido. status: {receipt.status} -> {status_text}")
        print()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(
            "ERRO ao assinar/enviar a transacao, ou a transacao foi minerada "
            "com falha (revert on-chain). Isso inclui o caso de a conta nao "
            f"ter CHANNEL_AUTHORITY_ROLE: {type(exc).__name__}: {exc}"
        )
        sys.exit(1)

    print("=" * 70)
    print("PASSO 3: getChannelInfo() DEPOIS da confirmacao")
    print("=" * 70)
    try:
        info_after = contract.functions.getChannelInfo(CHANNEL_NAME).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getChannelInfo(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_channel_info("depois", info_after)

    if info_after[0] and info_after[1]:
        print(f"OK: canal '{CHANNEL_NAME}' agora existe e esta ativo (isActive=True).")
    else:
        print(
            "AVISO: apos a transacao, getChannelInfo() nao confirma "
            "exists=True e isActive=True como esperado. Ver campos acima."
        )


if __name__ == "__main__":
    main()
