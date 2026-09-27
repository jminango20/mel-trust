"""
add_members.py

Adiciona os 3 nos do MVP (coop-a, coop-b, certifier) como membros do canal
"mel-trust" no contrato AccessChannelManager, na rede Semear (Besu privado,
chain ID 648, zero gas, acessivel via tunel SSH em http://localhost:8545).

Uso:
    python scripts/blockchain/add_members.py

Pre-requisitos:
    - Tunel SSH ativo expondo o RPC do Besu em http://localhost:8545.
    - Dependencias instaladas (ver scripts/blockchain/requirements.txt),
      incluindo `python-dotenv`.
    - Arquivo `scripts/blockchain/.env` com DEPLOYER_PRIVATE_KEY preenchida
      com uma chave privada real que tenha CHANNEL_ADMIN_ROLE no
      AccessChannelManager. So existe `.env.example` (sem valor) neste repo
      — o `.env` real precisa ser criado e preenchido por fora deste script,
      e ele NUNCA e commitado (.gitignore raiz ja cobre `.env` em qualquer
      pasta).
    - Arquivos de chave dos 3 nos ja gerados por generate_node_accounts.py:
        coop-a/keys/coop-a-besu.key
        coop-b/keys/coop-b-besu.key
        certifier/keys/certifier-besu.key

ORIGEM DAS ASSINATURAS USADAS NESTE SCRIPT
-------------------------------------------
addChannelMembers foi fornecida pelo usuario diretamente por texto nesta
sessao de chat, como "assinatura EXATA":

    function addChannelMembers(string calldata channelName, address[] calldata members)
        external onlyRole(CHANNEL_ADMIN_ROLE)
    // SEM retorno. Emite ChannelMembersAdded(channelHash, actuallyAdded,
    // memberCount) em sucesso (o evento nao e decodificado aqui — so citado
    // para contexto; o script confirma sucesso via receipt.status e via nova
    // chamada a isChannelMember para cada endereco).

isChannelMember foi CONFIRMADA pelo usuario nesta sessao (correcao pos-escrita
deste script: a primeira versao tratava como suposicao por analogia, mas o
usuario confirmou explicitamente a assinatura do AccessChannelManager.sol):

    function isChannelMember(string calldata channelName, address member)
        external view returns (bool)

Nao e mais tratada como suposicao no codigo abaixo. Mesmo assim, se a chamada
reverter/nao decodificar em runtime (por qualquer motivo — deploy diferente do
esperado, etc.), o erro real do node continua aparecendo no output tal como
veio, sem ser mascarado.

ENDERECO USADO
---------------
AccessChannelManager: 0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0

Mesmo endereco ja confirmado em rodadas anteriores (bytecode real deployado,
hasRole() funcionando). Este script NAO resolve o endereco de novo via
AddressDiscovery — usa o valor ja confirmado, conforme instrucao do usuario
nesta tarefa.

DE ONDE VEM OS 3 ENDERECOS DE MEMBRO
--------------------------------------
Nao sao hardcoded neste script. Cada endereco e derivado, em tempo de
execucao, da chave privada Ethereum ja gerada para o no correspondente:
    coop-a/keys/coop-a-besu.key
    coop-b/keys/coop-b-besu.key
    certifier/keys/certifier-besu.key
via `Account.from_key(...).address`. Assim, se as chaves forem regeneradas, o
script continua correto sem precisar ser editado. As chaves privadas em si
NUNCA sao impressas.

POR QUE gasPrice = w3.eth.gas_price EM VEZ DE 0 HARDCODADO
-------------------------------------------------------------
Mesma justificativa de create_channel.py: a rede Semear e descrita como
"zero gas", mas o script le o valor real reportado pelo node via
`w3.eth.gas_price` em vez de hardcodar `0`, documentando no output qual foi o
valor realmente usado.

FLUXO DESTE SCRIPT (resumo)
-----------------------------
1. Deriva os 3 enderecos de membro a partir dos arquivos de chave dos nos
   (nunca hardcoded em texto).
2. Chama isChannelMember("mel-trust", endereco) para cada um dos 3, ANTES de
   qualquer transacao. Reporta, para cada um, se ja e membro ou nao.
3. Monta a lista dos enderecos que AINDA NAO sao membros.
   - Se a lista estiver vazia (os 3 ja sao membros): nao monta nem envia
     nada, so reporta isso claramente, e o script termina.
   - Se sobrar 1 ou mais: monta, assina e envia UMA UNICA transacao
     addChannelMembers("mel-trust", enderecos_faltantes) (nonce via
     get_transaction_count, chain_id 648, gas via estimate_gas, gasPrice via
     w3.eth.gas_price), assinada pela conta lida de DEPLOYER_PRIVATE_KEY.
     Espera o receipt e imprime hash + status traduzido (sucesso/falha).
4. Ao final (rodou transacao ou nao), chama isChannelMember("mel-trust",
   endereco) de novo para os 3 e imprime o resultado, para confirmar (ou nao)
   que todos sao membros agora.

Erros (revert por falta de CHANNEL_ADMIN_ROLE, revert/falha de decodificacao
em isChannelMember, timeout de RPC, etc.) sao deixados aparecer no output
real, nao sao mascarados nem engolidos silenciosamente.

IMPORTANTE: este arquivo so foi criado, NAO foi executado pelo agente que o
escreveu. A execucao fica a cargo do usuario, depois de revisar os enderecos
e a logica.
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

# Raiz do repo = dois niveis acima deste arquivo (scripts/blockchain/../..).
REPO_ROOT = Path(__file__).resolve().parents[2]

# Nos cujo endereco (derivado da chave Ethereum ja gerada) deve ser membro do
# canal. Nao hardcodamos os enderecos em texto — so o caminho do arquivo de
# chave de cada no.
NODES = ["coop-a", "coop-b", "certifier"]


def node_key_path(node: str) -> Path:
    return REPO_ROOT / node / "keys" / f"{node}-besu.key"


def load_node_address(node: str) -> str:
    """Deriva o endereco publico do no a partir do arquivo de chave privada
    ja gerado por generate_node_accounts.py. A chave privada em si nunca e
    impressa nem retornada — so o endereco.
    """
    path = node_key_path(node)
    if not path.exists():
        print(
            f"ERRO: arquivo de chave do no '{node}' nao encontrado em {path}.\n"
            "Rode scripts/blockchain/generate_node_accounts.py primeiro.",
            file=sys.stderr,
        )
        sys.exit(1)
    private_key_hex = path.read_text(encoding="utf-8").strip()
    account = Account.from_key(private_key_hex)
    return account.address


# ABI minimo usado neste script.
#
# addChannelMembers: assinatura fornecida pelo usuario via chat nesta sessao
# (ver disclaimer completo no topo do arquivo) — CONFIRMADA.
#
# isChannelMember: assinatura CONFIRMADA pelo usuario (ver disclaimer
# completo no topo do arquivo).
ACCESS_CHANNEL_MANAGER_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "address[]", "name": "members", "type": "address[]"},
        ],
        "name": "addChannelMembers",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "address", "name": "member", "type": "address"},
        ],
        "name": "isChannelMember",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def load_private_key() -> str:
    """Carrega DEPLOYER_PRIVATE_KEY do .env em scripts/blockchain/.env.

    Nunca imprime a chave. Falha com mensagem clara se a variavel nao
    estiver definida (ex.: .env nao existe ainda, ou existe mas so tem a
    linha vazia copiada do .env.example).
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


def check_membership(contract, node_addresses: dict) -> dict:
    """Chama isChannelMember('mel-trust', endereco) para cada no e retorna
    um dict {node: bool}. Assinatura confirmada (ver disclaimer no topo do
    arquivo) — se mesmo assim reverter ou falhar ao decodificar em runtime,
    o erro real aparece aqui, sem ser mascarado.
    """
    results = {}
    for node, address in node_addresses.items():
        try:
            is_member = contract.functions.isChannelMember(CHANNEL_NAME, address).call()
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            print(
                f"ERRO na chamada isChannelMember('{CHANNEL_NAME}', {address}) "
                f"para o no '{node}': {type(exc).__name__}: {exc}"
            )
            sys.exit(1)
        results[node] = is_member
    return results


def main() -> None:
    print("=" * 70)
    print(f"ADICAO DE MEMBROS AO CANAL '{CHANNEL_NAME}' NO AccessChannelManager")
    print("=" * 70)
    print(f"RPC URL: {RPC_URL}")
    print(f"AccessChannelManager: {ACCESS_CHANNEL_MANAGER_ADDRESS}")
    print()

    print("=" * 70)
    print("PASSO 0: derivar enderecos dos 3 nos a partir das chaves ja geradas")
    print("=" * 70)
    node_addresses = {}
    for node in NODES:
        address = load_node_address(node)
        node_addresses[node] = address
        print(f"  {node}: {address}  (lido de {node_key_path(node)})")
    print()

    private_key = load_private_key()
    account = Account.from_key(private_key)
    print(f"Conta ADMIN derivada de DEPLOYER_PRIVATE_KEY: {account.address}")
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
    print(
        "PASSO 1: isChannelMember() para os 3 nos, ANTES de qualquer "
        "transacao"
    )
    print("=" * 70)
    membership_before = check_membership(contract, node_addresses)
    for node, address in node_addresses.items():
        status = "JA E MEMBRO" if membership_before[node] else "AINDA NAO E MEMBRO"
        print(f"  {node} ({address}): {status}")
    print()

    missing_nodes = [n for n in NODES if not membership_before[n]]
    missing_addresses = [node_addresses[n] for n in missing_nodes]

    if not missing_addresses:
        print(
            f"Os 3 nos ja sao membros do canal '{CHANNEL_NAME}'. Nada sera "
            "enviado — nenhuma transacao sera montada."
        )
    else:
        print(
            f"Faltam {len(missing_addresses)} de {len(NODES)} nos como "
            f"membro: {missing_nodes}. Serao adicionados em UMA UNICA "
            "transacao addChannelMembers()."
        )
        print()

        print("=" * 70)
        print("PASSO 2: montar, assinar e enviar addChannelMembers()")
        print("=" * 70)
        try:
            nonce = w3.eth.get_transaction_count(account.address)
            # Rede zero-gas (Semear/Besu privado) — lemos o gas_price real
            # do node em vez de hardcodar 0 (mesma justificativa de
            # create_channel.py).
            gas_price = w3.eth.gas_price
            print(f"nonce: {nonce}")
            print(f"gas_price lido de w3.eth.gas_price: {gas_price}")
            print(f"enderecos a adicionar: {missing_addresses}")

            checksummed_missing = [
                Web3.to_checksum_address(a) for a in missing_addresses
            ]

            estimated_gas = contract.functions.addChannelMembers(
                CHANNEL_NAME, checksummed_missing
            ).estimate_gas({"from": account.address})
            print(f"gas estimado (estimate_gas): {estimated_gas}")

            tx = contract.functions.addChannelMembers(
                CHANNEL_NAME, checksummed_missing
            ).build_transaction(
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
                "ERRO ao estimar gas ou montar a transacao de "
                "addChannelMembers(). Se a conta nao tiver "
                f"CHANNEL_ADMIN_ROLE, o revert deve aparecer aqui: "
                f"{type(exc).__name__}: {exc}"
            )
            sys.exit(1)

        try:
            signed_tx = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print(f"Transacao enviada. hash: {tx_hash.hex()}")

            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
            status_text = (
                "SUCESSO (status=1)" if receipt.status == 1 else "FALHA (status=0)"
            )
            print(f"Receipt recebido. status: {receipt.status} -> {status_text}")
            print()
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            print(
                "ERRO ao assinar/enviar a transacao, ou a transacao foi "
                "minerada com falha (revert on-chain). Isso inclui o caso de "
                f"a conta nao ter CHANNEL_ADMIN_ROLE: {type(exc).__name__}: "
                f"{exc}"
            )
            sys.exit(1)

    print("=" * 70)
    print("PASSO 3: isChannelMember() para os 3 nos, DEPOIS (confirmacao)")
    print("=" * 70)
    membership_after = check_membership(contract, node_addresses)
    all_members = True
    for node, address in node_addresses.items():
        status = "MEMBRO" if membership_after[node] else "NAO E MEMBRO"
        print(f"  {node} ({address}): {status}")
        if not membership_after[node]:
            all_members = False
    print()

    if all_members:
        print(f"OK: os 3 nos sao membros do canal '{CHANNEL_NAME}'.")
    else:
        print(
            "AVISO: nem todos os nos estao confirmados como membros do "
            f"canal '{CHANNEL_NAME}' apos este script. Ver detalhes acima."
        )


if __name__ == "__main__":
    main()
