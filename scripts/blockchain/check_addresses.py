"""
check_addresses.py

Verificacao standalone de conectividade com a rede Semear (Besu privado, chain ID 648)
e resolucao dos enderecos de contrato via AddressDiscovery.

Uso:
    python scripts/blockchain/check_addresses.py

Pre-requisitos:
    - Tunel SSH ativo expondo o RPC do Besu em http://localhost:8545
    - Dependencia `web3` instalada (ver scripts/blockchain/requirements.txt)

O QUE ESTE SCRIPT FAZ
----------------------
1. Conecta em http://localhost:8545 via web3.py (HTTPProvider) e le o chainId,
   comparando com o valor esperado (648) documentado no CLAUDE.md do projeto.
2. Chama o contrato AddressDiscovery (0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e)
   para resolver 5 nomes de contrato conhecidos: ACCESS_CHANNEL_MANAGER,
   SCHEMA_REGISTRY, PROCESS_REGISTRY, TRANSACTION_ORCHESTRATOR e ASSET_REGISTRY.
3. Imprime o endereco resolvido para cada nome, ou o erro (revert, endereco zero,
   timeout, etc.) sem esconder nada — especialmente para ASSET_REGISTRY, que ainda
   NAO temos confirmacao de que esteja registrado no AddressDiscovery.
4. Para cada endereco resolvido no passo 3, chama eth_getCode (w3.eth.get_code) e
   reporta o tamanho em bytes do bytecode retornado. Isso NAO depende de nenhum ABI
   ou suposicao de struct: confirma, de forma independente da ABI assumida acima,
   se ha um contrato de fato deployado naquele endereco (bytecode nao-vazio) ou se
   o endereco esta vazio / e um EOA / nunca foi usado (bytecode == "0x").

SUPOSICOES EXPLICITAS (nao confirmadas contra o .sol real, que nao existe neste repo)
---------------------------------------------------------------------------------------
- Assumimos que a funcao no contrato AddressDiscovery tem a assinatura minima:

      function getContractAddress(bytes32 nameHash) external view returns (address)

  Isso e uma suposicao razoavel dado o nome da funcao e o padrao descrito nas
  instrucoes do projeto ("resolvidos via AddressDiscovery.getContractAddress
  (keccak256(NOME))"), mas o ABI completo do contrato NUNCA foi visto neste repo.
  Se a assinatura real for diferente (outro tipo de retorno, outro nome de
  parametro, funcao nao-view, overload, etc.), a chamada abaixo vai falhar — e o
  erro deve aparecer no output tal como veio do node/web3, nao ser mascarado.

- Cada nome de contrato (ex. "ACCESS_CHANNEL_MANAGER") e convertido para bytes32
  via Web3.keccak(text=nome), conforme padrao keccak256(NOME) mencionado nas
  instrucoes do projeto. Isso tambem NAO foi confirmado contra o .sol real — se o
  contrato espera outro encoding (ex. string diretamente, ou keccak de bytes UTF-8
  com padding diferente), a resolucao pode retornar endereco errado ou reverter.

Nenhum ABI foi inventado alem desta unica funcao minima necessaria para o teste.

ETAPA 5 (hasRole) — ORIGEM DA ASSINATURA E RACIOCINIO
--------------------------------------------------------
Na rodada anterior, getVersion() reverteu ESPECIFICAMENTE para o endereco do
ACCESS_CHANNEL_MANAGER (0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0), enquanto
funcionou (ou pelo menos nao reverteu da mesma forma) para os outros enderecos
resolvidos via AddressDiscovery. Isso e esperado: ACCESS_CHANNEL_MANAGER
plausivelmente NAO compartilha a mesma interface que SCHEMA_REGISTRY,
PROCESS_REGISTRY, TRANSACTION_ORCHESTRATOR e ASSET_REGISTRY parecem
compartilhar (getVersion). "Channel manager" e um conceito de controle de
acesso — faz sentido que ele implemente, em vez disso, o padrao OpenZeppelin
AccessControl:

    function hasRole(bytes32 role, address account) external view returns (bool)

Essa e uma interface DIFERENTE de getVersion(), entao o fato de getVersion()
ter revertido ali NAO diz nada sobre se hasRole vai funcionar ou nao — sao
funcoes/selectors distintos. Isso e uma suposicao plausivel dada a
nomenclatura ("channel manager" + "access control"), mas NAO confirmada
contra nenhum .sol real (nao existe neste repo). Se o contrato nao
implementar AccessControl, ou implementar com outro layout de roles, a
chamada abaixo vai reverter — e isso deve ser reportado como erro real, nao
mascarado nem assumido como sucesso.

Testamos duas roles, ambas necessarias no fluxo de canal (contexto do
usuario): CHANNEL_AUTHORITY_ROLE (criar o canal) e CHANNEL_ADMIN_ROLE
(adicionar membros depois), ambas calculadas como Web3.keccak(text=NOME),
mesmo padrao keccak256(NOME) usado na etapa (2) para AddressDiscovery — de
novo, suposicao nao confirmada contra o .sol, reportada como tal.

ETAPA 4 (getVersion) — ORIGEM DA ASSINATURA
---------------------------------------------
O usuario mencionou ter um "AssetRegistry.sol" com a funcao:

    function getVersion() external pure returns (string memory)

Esse arquivo .sol NAO existe neste repositorio (mel-trust) — foi buscado em todo o
repo e nao ha nenhum .sol do projeto aqui (so contratos de teste internos da lib
web3.py, sem relacao). Portanto o codigo abaixo NAO le nenhum .sol local: usa
apenas a assinatura acima, informada por texto pelo usuario. O ABI minimo montado
a partir dela e usado para chamar getVersion() nos 5 enderecos ja resolvidos na
etapa (2). E esperado que essa chamada tenha sucesso apenas no endereco que for
de fato um AssetRegistry (ou que compartilhe essa interface) — para os demais, um
revert ou erro de decodificacao e o resultado real e informativo, nao um bug do
script.
"""

import sys

try:
    from web3 import Web3
except ImportError:
    print(
        "ERRO: pacote 'web3' nao encontrado neste interpretador Python.\n"
        "Instale com: pip install -r scripts/blockchain/requirements.txt\n"
        f"Interpretador atual: {sys.executable}",
        file=sys.stderr,
    )
    sys.exit(1)

RPC_URL = "http://127.0.0.1:8545"
EXPECTED_CHAIN_ID = 648

ADDRESS_DISCOVERY_ADDRESS = "0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e"

# ABI minimo e assumido (ver docstring do modulo para o disclaimer completo).
ADDRESS_DISCOVERY_ABI = [
    {
        "constant": True,
        "inputs": [{"internalType": "bytes32", "name": "nameHash", "type": "bytes32"}],
        "name": "getContractAddress",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    }
]

CONTRACT_NAMES = [
    "ACCESS_CHANNEL_MANAGER",
    "SCHEMA_REGISTRY",
    "PROCESS_REGISTRY",
    "TRANSACTION_ORCHESTRATOR",
    "ASSET_REGISTRY",
]

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

EXPECTED_VERSION = "1.0.0"

# ABI minimo para getVersion() — NAO vem de nenhum .sol lido neste repo (nao existe
# aqui). Montado apenas a partir da assinatura informada pelo usuario via chat:
#   function getVersion() external pure returns (string memory)
GET_VERSION_ABI = [
    {
        "inputs": [],
        "name": "getVersion",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "pure",
        "type": "function",
    }
]


def check_chain_id(w3: Web3) -> bool:
    print("=" * 70)
    print("1) VERIFICACAO DE CONEXAO E CHAIN ID")
    print("=" * 70)
    print(f"RPC URL: {RPC_URL}")
    try:
        connected = w3.is_connected()
        print(f"is_connected(): {connected}")
        chain_id = w3.eth.chain_id
        print(f"chain_id lido: {chain_id}")
        if chain_id == EXPECTED_CHAIN_ID:
            print(f"OK: chain_id bate com o esperado ({EXPECTED_CHAIN_ID}).")
            return True
        else:
            print(
                f"AVISO: chain_id ({chain_id}) DIFERENTE do esperado "
                f"({EXPECTED_CHAIN_ID})."
            )
            return False
    except Exception as exc:  # noqa: BLE001 - queremos capturar e reportar qualquer falha de RPC
        print(f"ERRO ao consultar chain_id: {type(exc).__name__}: {exc}")
        return False


def check_address_discovery(w3: Web3) -> dict:
    print()
    print("=" * 70)
    print("2) RESOLUCAO DE ENDERECOS VIA AddressDiscovery.getContractAddress")
    print("=" * 70)
    print(f"AddressDiscovery: {ADDRESS_DISCOVERY_ADDRESS}")
    print(
        "Suposicao de ABI: getContractAddress(bytes32) -> address "
        "(ver disclaimer no topo do arquivo)."
    )
    print()

    resolved_addresses: dict = {}

    try:
        contract_address = Web3.to_checksum_address(ADDRESS_DISCOVERY_ADDRESS)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO: endereco do AddressDiscovery invalido: {exc}")
        return resolved_addresses

    contract = w3.eth.contract(address=contract_address, abi=ADDRESS_DISCOVERY_ABI)

    for name in CONTRACT_NAMES:
        name_hash = Web3.keccak(text=name)
        highlight = " <<< AINDA NAO CONFIRMADO QUE EXISTE" if name == "ASSET_REGISTRY" else ""
        print(f"--- {name}{highlight} ---")
        print(f"  keccak256('{name}') = {name_hash.hex()}")
        try:
            resolved = contract.functions.getContractAddress(name_hash).call()
            if resolved == ZERO_ADDRESS or int(resolved, 16) == 0:
                print(f"  resultado: {resolved} (endereco ZERO -> nao registrado)")
            else:
                print(f"  resultado: {resolved}")
                resolved_addresses[name] = resolved
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            print(f"  ERRO na chamada: {type(exc).__name__}: {exc}")
        print()

    return resolved_addresses


def check_bytecode(w3: Web3, resolved_addresses: dict) -> None:
    print()
    print("=" * 70)
    print("3) VERIFICACAO DE BYTECODE (eth_getCode) NOS ENDERECOS RESOLVIDOS")
    print("=" * 70)
    print(
        "Esta verificacao NAO depende de nenhum ABI ou suposicao de struct: "
        "eth_getCode so olha o que esta de fato deployado naquele endereco "
        "on-chain. Bytecode vazio (0x) = endereco sem contrato (EOA ou nunca "
        "usado). Bytecode nao-vazio = ha um contrato de verdade deployado ali."
    )
    print()

    if not resolved_addresses:
        print(
            "Nenhum endereco foi resolvido na etapa (2) — nao ha nada para "
            "verificar aqui."
        )
        return

    for name in CONTRACT_NAMES:
        if name not in resolved_addresses:
            print(f"--- {name}: PULADO (nao resolvido na etapa 2) ---")
            print()
            continue

        address = resolved_addresses[name]
        highlight = (
            " <<< confirmacao independente da ABI (ASSET_REGISTRY)"
            if name == "ASSET_REGISTRY"
            else ""
        )
        print(f"--- {name}{highlight} ---")
        print(f"  endereco: {address}")
        try:
            code = w3.eth.get_code(Web3.to_checksum_address(address))
            size = len(code)
            if size == 0:
                print(
                    "  eth_getCode: 0x (0 bytes) -> SEM CODIGO / possivel EOA "
                    "ou endereco nunca usado"
                )
            else:
                print(f"  eth_getCode: {size} bytes -> CONTRATO (bytecode presente)")
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            print(f"  ERRO ao chamar eth_getCode: {type(exc).__name__}: {exc}")
        print()


def check_get_version(w3: Web3, resolved_addresses: dict) -> dict:
    print()
    print("=" * 70)
    print("4) CHAMADA getVersion() NOS 5 ENDERECOS RESOLVIDOS")
    print("=" * 70)
    print(
        "Assinatura usada (informada pelo usuario via chat, NAO lida de nenhum "
        ".sol deste repo — nao ha .sol aqui):\n"
        "    function getVersion() external pure returns (string memory)\n"
    )
    print(
        "E esperado que isto funcione apenas no endereco que for de fato um "
        "AssetRegistry (ou compartilhe essa interface). Erro/revert nos demais "
        "e resultado real, nao falha do script."
    )
    print()

    version_results: dict = {}

    if not resolved_addresses:
        print(
            "Nenhum endereco foi resolvido na etapa (2) — nao ha nada para "
            "chamar aqui."
        )
        return version_results

    for name in CONTRACT_NAMES:
        if name not in resolved_addresses:
            print(f"--- {name}: PULADO (nao resolvido na etapa 2) ---")
            print()
            continue

        address = resolved_addresses[name]
        print(f"--- {name} ---")
        print(f"  endereco: {address}")
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(address), abi=GET_VERSION_ABI
            )
            version = contract.functions.getVersion().call()
            version_results[name] = version
            match = "OK, bate com o esperado" if version == EXPECTED_VERSION else (
                f"DIFERENTE do esperado ({EXPECTED_VERSION!r})"
            )
            print(f"  getVersion() retornou: {version!r} -> {match}")
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            print(f"  ERRO na chamada getVersion(): {type(exc).__name__}: {exc}")
        print()

    return version_results


# ABI minimo para hasRole(bytes32,address) — padrao OpenZeppelin AccessControl.
# NAO vem de nenhum .sol lido neste repo (nao existe aqui). Ver disclaimer da
# ETAPA 5 no topo do arquivo para o raciocinio de por que testamos essa
# interface especificamente no ACCESS_CHANNEL_MANAGER.
HAS_ROLE_ABI = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "role", "type": "bytes32"},
            {"internalType": "address", "name": "account", "type": "address"},
        ],
        "name": "hasRole",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    }
]

ACCESS_CHANNEL_MANAGER_ADDRESS = "0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0"
HAS_ROLE_TEST_ACCOUNT = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

ROLE_NAMES_TO_CHECK = [
    "CHANNEL_AUTHORITY_ROLE",  # necessario para criar o canal
    "CHANNEL_ADMIN_ROLE",  # necessario para adicionar membros depois
]


def check_has_role(w3: Web3) -> dict:
    print()
    print("=" * 70)
    print("5) VERIFICACAO hasRole(bytes32, address) NO ACCESS_CHANNEL_MANAGER")
    print("=" * 70)
    print(
        "Interface OpenZeppelin AccessControl (DIFERENTE da interface "
        "getVersion() testada na etapa 4 — ver disclaimer da ETAPA 5 no topo "
        "do arquivo). O revert de getVersion() nesse mesmo endereco na rodada "
        "anterior nao implica nada sobre hasRole; sao selectors distintos."
    )
    print(f"ACCESS_CHANNEL_MANAGER: {ACCESS_CHANNEL_MANAGER_ADDRESS}")
    print(f"Conta consultada: {HAS_ROLE_TEST_ACCOUNT}")
    print()

    results: dict = {}

    try:
        contract_address = Web3.to_checksum_address(ACCESS_CHANNEL_MANAGER_ADDRESS)
        account = Web3.to_checksum_address(HAS_ROLE_TEST_ACCOUNT)
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO: endereco invalido: {exc}")
        return results

    contract = w3.eth.contract(address=contract_address, abi=HAS_ROLE_ABI)

    for role_name in ROLE_NAMES_TO_CHECK:
        role_hash = Web3.keccak(text=role_name)
        print(f"--- {role_name} ---")
        print(f"  keccak256('{role_name}') = {role_hash.hex()}")
        try:
            has_role = contract.functions.hasRole(role_hash, account).call()
            results[role_name] = has_role
            print(
                f"  hasRole({role_name}, {HAS_ROLE_TEST_ACCOUNT}) = {has_role}"
            )
        except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
            print(f"  ERRO na chamada hasRole(): {type(exc).__name__}: {exc}")
        print()

    return results


def main() -> None:
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))

    chain_ok = check_chain_id(w3)
    resolved_addresses = check_address_discovery(w3)
    check_bytecode(w3, resolved_addresses)
    version_results = check_get_version(w3, resolved_addresses)
    has_role_results = check_has_role(w3)

    print("=" * 70)
    print("RESUMO")
    print("=" * 70)
    print(f"Chain ID esperado: {EXPECTED_CHAIN_ID} | verificacao OK: {chain_ok}")
    print(
        "Enderecos do AddressDiscovery: ver secao (2) acima para cada nome, "
        "incluindo ASSET_REGISTRY."
    )
    print(
        "Bytecode (eth_getCode): ver secao (3) acima — confirmacao "
        "independente de ABI de que ha contrato de verdade deployado em "
        "cada endereco resolvido."
    )
    print()
    print(f"getVersion() — esperado pelo usuario: {EXPECTED_VERSION!r}")
    if not version_results:
        print("  Nenhum endereco respondeu com sucesso a getVersion() (ver secao 4).")
    for name in CONTRACT_NAMES:
        if name in version_results:
            v = version_results[name]
            status = "== esperado" if v == EXPECTED_VERSION else "!= esperado"
            print(f"  {name}: {v!r} ({status})")
        elif name in resolved_addresses:
            print(f"  {name}: chamada falhou/reverteu (ver secao 4)")
        else:
            print(f"  {name}: nao resolvido (ver secao 2)")

    print()
    print(
        f"hasRole() no ACCESS_CHANNEL_MANAGER ({ACCESS_CHANNEL_MANAGER_ADDRESS}) "
        f"para a conta {HAS_ROLE_TEST_ACCOUNT}:"
    )
    if not has_role_results:
        print("  Nenhuma chamada teve sucesso (ver secao 5 acima para o erro real).")
    for role_name in ROLE_NAMES_TO_CHECK:
        if role_name in has_role_results:
            print(f"  {role_name}: {has_role_results[role_name]}")
        else:
            print(f"  {role_name}: chamada falhou/reverteu (ver secao 5)")


if __name__ == "__main__":
    main()
