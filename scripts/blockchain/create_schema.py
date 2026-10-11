"""
create_schema.py

Registra o schema SHACL "honey-validation-shapes" no contrato SchemaRegistry,
na rede Semear (Besu privado, chain ID 648, zero gas, acessivel via tunel SSH
em http://localhost:8545).

Uso:
    python scripts/blockchain/create_schema.py

Pre-requisitos:
    - Tunel SSH ativo expondo o RPC do Besu em http://localhost:8545.
    - Dependencias instaladas (ver scripts/blockchain/requirements.txt).
    - Arquivo `certifier/keys/certifier-besu.key` presente (hex puro, 64
      caracteres, sem prefixo "0x" — formato confirmado via inspecao binaria
      antes de assumir). A tx de escrita e assinada por essa conta, NAO por
      DEPLOYER_PRIVATE_KEY — ver secao "MUDANCA NESTA RODADA" abaixo.
    - Arquivo `ontology/validation-shapes.ttl` presente (o hash e calculado
      do CONTEUDO ATUAL desse arquivo, em bytes crus — qualquer alteracao
      nele depois de rodar este script invalida o dataHash registrado
      on-chain).

MUDANCA NESTA RODADA (por que a conta trocou)
-----------------------------------------------
Rodada anterior: assinando com DEPLOYER_PRIVATE_KEY (conta admin
0xf39Fd6...2266), registerSchema reverteu generico e createSchema reverteu
com um custom error (address, string). Confirmou-se depois, via
isChannelMember("mel-trust", 0xf39Fd6...2266) == False, que essa conta NAO e
membro do canal "mel-trust" — hipotese: createSchema exige ser membro do
canal (nao uma role administrativa separada). Esta rodada assina com a
chave Besu do certifier (certifier/keys/certifier-besu.key) e, ANTES de
qualquer transacao, confirma isChannelMember("mel-trust", <endereco
derivado>) == True. Se vier False, o script para e reporta, sem tentar a
transacao. So tenta createSchema (nao registerSchema de novo) — combinado
explicitamente para esta rodada.

O QUE ESTE SCRIPT FAZ
----------------------
1. Le `ontology/validation-shapes.ttl` como bytes crus (sem normalizar quebra
   de linha, sem decodificar como texto) e calcula keccak256 desses bytes.
   Esse e o `dataHash` (bytes32) do schema — a blockchain nunca guarda o
   conteudo do .ttl em si, so o hash (mesmo principio de ancoragem de VC
   descrito no CLAUDE.md do projeto: blockchain guarda hash, nao dado bruto).
2. Chama `SchemaRegistry.getActiveSchema("mel-trust", "honey-validation-shapes")`
   — assinatura CONFIRMADA em docs/referencia-contratos/README.md secao 3
   (testada contra o node real nesta mesma sessao de trabalho anterior, sem
   revert, com exists=False para este mesmo schemaId).
   - Se `exists == True`: imprime a struct Schema retornada por completo e
     ENCERRA sem montar nem enviar nenhuma transacao (idempotencia, mesmo
     padrao de create_channel.py com getChannelInfo).
3. Se `exists == False`: tenta registrar o schema. A funcao de escrita NAO
   esta confirmada (ver disclaimer detalhado abaixo) — tenta, nessa ordem,
   ate uma funcionar, parando no primeiro sucesso:
     a. registerSchema(SchemaInput calldata input)
     b. createSchema(SchemaInput calldata input)
   Ambas usam a MESMA struct de entrada (SchemaInput), ordem de campos dada
   pelo usuario via chat: (schemaId, name, dataHash, channelName,
   description). Se as duas revertarem, o script para e reporta os DOIS
   erros exatos — nao tenta mais nomes/ordens sem confirmacao humana.
4. Se a transacao teve sucesso (receipt.status == 1): chama
   getActiveSchema() de novo, confirma exists=True e compara o dataHash
   retornado byte a byte com o hash calculado no passo 1.

ORIGEM DA STRUCT SchemaInput E DAS FUNCOES DE ESCRITA — DISCLAIMER COMPLETO
-----------------------------------------------------------------------------
NADA nesta secao foi confirmado contra codigo-fonte Solidity real. Nao existe
nenhum `SchemaRegistry.sol` neste repositorio (mel-trust nunca teve nenhum
.sol de producao versionado aqui, ver CLAUDE.md e
docs/referencia-contratos/README.md). O que temos:

- A struct `Schema` (retorno de `getActiveSchema`) ESTA confirmada (ver
  docs/referencia-contratos/README.md secao 3, testada com sucesso, sem
  revert, exists=False para "honey-validation-shapes" e "mel-trust-shapes").
  Ela tem 10 campos: schemaId, name, version, dataHash, owner, channelName,
  status (enum/uint8), createdAt, updatedAt, description.

- A struct `SchemaInput` usada abaixo NAO e essa struct de retorno — e uma
  DESCRICAO em linguagem natural dada pelo usuario nesta sessao para a
  entrada da funcao de escrita: SchemaInput{schemaId, name, dataHash,
  channelName, description} (5 campos, sem version/owner/status/timestamps
  — presumivelmente preenchidos pelo contrato internamente). Isso e uma
  suposicao razoavel dado o padrao de "struct de input reduzido vs struct de
  storage completo" comum em contratos Solidity, mas NUNCA foi vista em
  codigo-fonte real. Se a ordem real dos campos for diferente, ou se algum
  campo adicional for obrigatorio (ex: version explicito), a chamada abaixo
  vai reverter por mismatch de selector/encoding — e esse erro deve aparecer
  no output tal como veio do node, sem ser mascarado.

- Os NOMES das funcoes de escrita (`registerSchema`, `createSchema`) tambem
  NAO estao confirmados. docs/referencia-contratos/README.md e explicito:
  "NAO CONFIRMADO: funcao de escrita para registrar schema novo (nome/
  assinatura exatos ainda não pedidos)". `registerSchema` e `createSchema`
  sao os dois nomes mais plausiveis dado o padrao de nomenclatura do resto
  do contrato (getActiveSchema, getVersion), mas sao PALPITES, nao fatos.
  Se ambos revertarem, isso NAO sera tratado como "tentar mais um nome" —
  sera reportado como falha real, pedindo confirmacao humana antes de
  qualquer nova tentativa (mesma disciplina usada durante toda esta sessao
  de trabalho, ver docs/referencia-contratos/README.md).

- O tipo de retorno das funcoes de escrita tambem e desconhecido. O ABI
  abaixo declara `outputs: []` (sem retorno) para as duas tentativas, que e
  a suposicao mais simples/comum (mutacao de estado sem valor de retorno,
  como createChannel() no AccessChannelManager). Se a funcao real retornar
  algo (ex: um schemaId ou bool), a chamada `.transact()`/build_transaction
  ainda deve funcionar (ABI com outputs errado nao impede enviar uma tx,
  so afetaria decodificacao de um `.call()`, que nao e usado aqui para a
  escrita) — mas fica documentado como suposicao, nao fato.

ENDERECO USADO
---------------
SchemaRegistry: 0x0DCd1Bf9A1b36cE34237eEaFef220932846BCD82
(mesmo endereco ja confirmado com bytecode real e getVersion()=="1.0.0" em
check_addresses.py, e com getActiveSchema() funcionando sem revert em sessao
anterior — ver docs/referencia-contratos/README.md secao 3). Este script NAO
resolve o endereco de novo via AddressDiscovery, usa o valor ja confirmado.

POR QUE gasPrice = w3.eth.gas_price EM VEZ DE 0 HARDCODADO
-------------------------------------------------------------
Mesmo raciocinio de create_channel.py: a rede Semear e descrita como "zero
gas", mas o script le o valor real via w3.eth.gas_price em vez de supor 0,
para que o output documente o valor realmente usado e o script continue
correto se a politica de gas da rede mudar.

FLUXO DESTE SCRIPT (resumo)
-----------------------------
1. Le validation-shapes.ttl em bytes crus, calcula keccak256 -> dataHash.
2. Carrega DEPLOYER_PRIVATE_KEY do .env local (nunca hardcoded, nunca
   impresso — so o endereco publico derivado dela aparece no output).
3. Chama getActiveSchema("mel-trust", "honey-validation-shapes") ANTES de
   qualquer transacao.
   - Se exists == True: imprime a struct Schema completa e TERMINA, sem
     montar nem enviar transacao alguma.
4. Se exists == False: tenta registerSchema(SchemaInput) e, se reverter,
   tenta createSchema(SchemaInput) com a mesma struct. Para no primeiro
   sucesso. Se as duas revertarem, reporta os 2 erros exatos e encerra sem
   tentar mais nada.
5. Se uma tx teve sucesso (receipt.status == 1): chama getActiveSchema() de
   novo, confirma exists=True e dataHash retornado == dataHash calculado no
   passo 1 (comparacao byte a byte).

Erros (revert por selector/struct incorreto, falta de role/permissao,
timeout de RPC, etc.) sao deixados aparecer no output real, nao sao
mascarados nem engolidos silenciosamente.
"""

import sys
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


RPC_URL = "http://localhost:8545"
EXPECTED_CHAIN_ID = 648

SCHEMA_REGISTRY_ADDRESS = "0x0DCd1Bf9A1b36cE34237eEaFef220932846BCD82"

# AccessChannelManager — usado so para a checagem de membership
# (isChannelMember) antes de qualquer transacao. Endereco e ABI CONFIRMADOS
# (ver docs/referencia-contratos/README.md secao 2 e
# scripts/blockchain/add_members.py, ja usados com sucesso nesta mesma sessao).
ACCESS_CHANNEL_MANAGER_ADDRESS = "0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0"

IS_CHANNEL_MEMBER_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "address", "name": "member", "type": "address"},
        ],
        "name": "isChannelMember",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    }
]

# Chave Besu do certifier (Holder/emissor no fluxo de credenciais, tambem
# usada aqui como signer da tx de registro de schema porque e a conta que
# de fato e membro do canal "mel-trust" — a conta admin DEPLOYER_PRIVATE_KEY
# NAO e membro, confirmado via isChannelMember == False em rodada anterior
# desta mesma sessao). Formato do arquivo: hex puro (64 caracteres, sem
# prefixo "0x", sem quebras de linha), confirmado com `xxd` antes de assumir
# como carregar. Mesmo padrao de leitura usado em
# scripts/blockchain/add_members.py (path.read_text().strip() +
# Account.from_key()).
CERTIFIER_KEY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "certifier" / "keys" / "certifier-besu.key"
)

CHANNEL_NAME = "mel-trust"
SCHEMA_ID = "honey-validation-shapes"
SCHEMA_NAME = "Honey Validation Shapes"
SCHEMA_DESCRIPTION = (
    "SHACL shapes para validacao de lotes de mel (IN 11/2000, Portaria "
    "795/2023, 1.439/2025)"
)

VALIDATION_SHAPES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "ontology" / "validation-shapes.ttl"
)

# ABI minimo para getActiveSchema — CONFIRMADO contra o node real em sessao
# anterior desta mesma conversa (ver docs/referencia-contratos/README.md
# secao 3): chamada sem revert, exists=False para "honey-validation-shapes".
SCHEMA_STRUCT_COMPONENTS = [
    {"internalType": "string", "name": "schemaId", "type": "string"},
    {"internalType": "string", "name": "name", "type": "string"},
    {"internalType": "uint256", "name": "version", "type": "uint256"},
    {"internalType": "bytes32", "name": "dataHash", "type": "bytes32"},
    {"internalType": "address", "name": "owner", "type": "address"},
    {"internalType": "string", "name": "channelName", "type": "string"},
    {"internalType": "uint8", "name": "status", "type": "uint8"},
    {"internalType": "uint256", "name": "createdAt", "type": "uint256"},
    {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
    {"internalType": "string", "name": "description", "type": "string"},
]

GET_ACTIVE_SCHEMA_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "string", "name": "schemaId", "type": "string"},
        ],
        "name": "getActiveSchema",
        "outputs": [
            {
                "internalType": "struct Schema",
                "name": "schema",
                "type": "tuple",
                "components": SCHEMA_STRUCT_COMPONENTS,
            },
            {"internalType": "bool", "name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# ABI para a tentativa de escrita (createSchema). NAO CONFIRMADO ate esta
# rodada — ver disclaimer completo no topo do arquivo. SchemaInput e uma
# struct DIFERENTE de Schema (5 campos, nao 10) — descricao em linguagem
# natural dada pelo usuario via chat, nunca vista em .sol real.
# registerSchema foi tentado e removido desta rodada: reverteu generico com
# a conta admin (nao-membro) em rodada anterior, e a instrucao desta rodada
# e refazer SO createSchema com a conta do certifier (que e membro).
SCHEMA_INPUT_COMPONENTS = [
    {"internalType": "string", "name": "schemaId", "type": "string"},
    {"internalType": "string", "name": "name", "type": "string"},
    {"internalType": "bytes32", "name": "dataHash", "type": "bytes32"},
    {"internalType": "string", "name": "channelName", "type": "string"},
    {"internalType": "string", "name": "description", "type": "string"},
]

CREATE_SCHEMA_ABI = [
    {
        "inputs": [
            {
                "internalType": "struct SchemaInput",
                "name": "input",
                "type": "tuple",
                "components": SCHEMA_INPUT_COMPONENTS,
            }
        ],
        "name": "createSchema",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def load_certifier_private_key() -> str:
    """Carrega a chave privada Besu do certifier de
    certifier/keys/certifier-besu.key.

    Formato do arquivo confirmado via inspecao binaria (xxd) antes de
    assumir: hex puro, 64 caracteres (32 bytes), sem prefixo "0x", sem
    quebra de linha. Nunca imprime a chave — so o endereco derivado dela
    (impresso por quem chama esta funcao).
    """
    if not CERTIFIER_KEY_PATH.is_file():
        print(
            f"ERRO: arquivo de chave do certifier nao encontrado em "
            f"{CERTIFIER_KEY_PATH}.",
            file=sys.stderr,
        )
        sys.exit(1)

    private_key = CERTIFIER_KEY_PATH.read_text(encoding="utf-8").strip()
    if not private_key:
        print(
            f"ERRO: arquivo de chave do certifier em {CERTIFIER_KEY_PATH} "
            "esta vazio.",
            file=sys.stderr,
        )
        sys.exit(1)
    return private_key


def canonical_schema_hash(path) -> bytes:
    """keccak256 dos BYTES do arquivo com CRLF trocado por LF.

    Forma canonica: o repositorio guarda .ttl em LF (.gitattributes
    "*.ttl text eol=lf"), mas no Windows o arquivo em disco pode estar em
    CRLF (core.autocrlf). Normalizar para LF faz o hash ser o mesmo em
    qualquer maquina/CI. Nao decodifica como texto; so troca b"\\r\\n" por
    b"\\n".
    """
    raw = Path(path).read_bytes()
    return Web3.keccak(raw.replace(b"\r\n", b"\n"))


def compute_data_hash() -> bytes:
    """Calcula o dataHash do validation-shapes.ttl pela forma canonica
    (canonical_schema_hash: bytes com CRLF -> LF, keccak256)."""
    print("=" * 70)
    print("PASSO 0: calculo do dataHash (keccak256 do conteudo do .ttl)")
    print("=" * 70)
    print(f"Arquivo: {VALIDATION_SHAPES_PATH}")

    if not VALIDATION_SHAPES_PATH.is_file():
        print(f"ERRO: arquivo nao encontrado em {VALIDATION_SHAPES_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Tamanho do arquivo em disco: {VALIDATION_SHAPES_PATH.stat().st_size} bytes")

    data_hash = canonical_schema_hash(VALIDATION_SHAPES_PATH)
    print(f"keccak256(conteudo canonico, CRLF->LF) = {data_hash.hex()}")
    print()
    return data_hash


def print_schema(label: str, schema_tuple, exists: bool) -> None:
    print(f"--- getActiveSchema('{CHANNEL_NAME}', '{SCHEMA_ID}') [{label}] ---")
    print(f"  exists: {exists}")
    if exists:
        (
            schema_id,
            name,
            version,
            data_hash,
            owner,
            channel_name,
            status,
            created_at,
            updated_at,
            description,
        ) = schema_tuple
        print(f"  schemaId:    {schema_id}")
        print(f"  name:        {name}")
        print(f"  version:     {version}")
        print(f"  dataHash:    {data_hash.hex() if isinstance(data_hash, (bytes, bytearray)) else data_hash}")
        print(f"  owner:       {owner}")
        print(f"  channelName: {channel_name}")
        print(f"  status:      {status}")
        print(f"  createdAt:   {created_at}")
        print(f"  updatedAt:   {updated_at}")
        print(f"  description: {description}")
    print()


def try_write_function(
    w3: Web3,
    account,
    abi: list,
    function_name: str,
    schema_input: tuple,
):
    """Tenta montar, assinar e enviar uma transacao para a funcao de escrita
    dada. Retorna (True, receipt, tx_hash) em sucesso, (False, erro_str, None)
    em falha (seja na estimativa de gas, seja no envio, seja no revert
    on-chain). Nao mascara nenhum erro — so captura para permitir tentar a
    proxima funcao candidata.
    """
    print(f"--- Tentativa: {function_name}(SchemaInput) ---")
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(SCHEMA_REGISTRY_ADDRESS), abi=abi
    )
    fn = getattr(contract.functions, function_name)

    try:
        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = w3.eth.gas_price
        print(f"  nonce: {nonce}")
        print(f"  gas_price lido de w3.eth.gas_price: {gas_price}")

        estimated_gas = fn(schema_input).estimate_gas({"from": account.address})
        print(f"  gas estimado (estimate_gas): {estimated_gas}")

        tx = fn(schema_input).build_transaction(
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
        return False, error_text, None

    try:
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"  Transacao enviada. hash: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        status_text = "SUCESSO (status=1)" if receipt.status == 1 else "FALHA (status=0)"
        print(f"  Receipt recebido. status: {receipt.status} -> {status_text}")
        print()

        if receipt.status == 1:
            return True, receipt, tx_hash
        else:
            return False, f"receipt.status == 0 (revert on-chain), tx_hash={tx_hash.hex()}", None
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        error_text = f"{type(exc).__name__}: {exc}"
        print(f"  ERRO ao assinar/enviar a transacao, ou revert on-chain: {error_text}")
        print()
        return False, error_text, None


def main() -> None:
    print("=" * 70)
    print(f"REGISTRO DE SCHEMA '{SCHEMA_ID}' NO SchemaRegistry (canal '{CHANNEL_NAME}')")
    print("=" * 70)
    print(f"RPC URL: {RPC_URL}")
    print(f"SchemaRegistry: {SCHEMA_REGISTRY_ADDRESS}")
    print()

    data_hash = compute_data_hash()

    private_key = load_certifier_private_key()
    account = Account.from_key(private_key)
    print(f"Conta derivada de certifier/keys/certifier-besu.key: {account.address}")
    print("(a chave privada em si nunca e impressa)")
    print()

    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 10}))

    connected = w3.is_connected()
    print(f"is_connected(): {connected}")
    try:
        chain_id = w3.eth.chain_id
    except Exception as exc:  # noqa: BLE001
        print(f"ERRO ao consultar chain_id (tunel pode ter caido): {type(exc).__name__}: {exc}")
        sys.exit(1)
    print(f"chain_id lido: {chain_id} (esperado: {EXPECTED_CHAIN_ID})")
    if chain_id != EXPECTED_CHAIN_ID:
        print(
            f"AVISO: chain_id ({chain_id}) diferente do esperado "
            f"({EXPECTED_CHAIN_ID}). Continuando mesmo assim, mas isso deve "
            "ser investigado antes de confiar no resultado."
        )
    print()

    print("=" * 70)
    print("PASSO 0b: isChannelMember('mel-trust', conta do certifier)")
    print("=" * 70)
    access_contract = w3.eth.contract(
        address=Web3.to_checksum_address(ACCESS_CHANNEL_MANAGER_ADDRESS),
        abi=IS_CHANNEL_MEMBER_ABI,
    )
    try:
        is_member = access_contract.functions.isChannelMember(
            CHANNEL_NAME, account.address
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada isChannelMember(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print(f"isChannelMember('{CHANNEL_NAME}', {account.address}) = {is_member}")
    print()

    if not is_member:
        print(
            "PARANDO: a conta derivada da chave do certifier NAO e membro do "
            f"canal '{CHANNEL_NAME}'. Nenhuma transacao sera tentada — "
            "reportando este resultado sem prosseguir."
        )
        sys.exit(1)

    read_contract = w3.eth.contract(
        address=Web3.to_checksum_address(SCHEMA_REGISTRY_ADDRESS),
        abi=GET_ACTIVE_SCHEMA_ABI,
    )

    print("=" * 70)
    print("PASSO 1: getActiveSchema() ANTES de qualquer transacao")
    print("=" * 70)
    try:
        schema_before, exists_before = read_contract.functions.getActiveSchema(
            CHANNEL_NAME, SCHEMA_ID
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getActiveSchema(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_schema("antes", schema_before, exists_before)

    if exists_before:
        print(
            f"AVISO: o schema '{SCHEMA_ID}' no canal '{CHANNEL_NAME}' JA EXISTE. "
            "Nada sera enviado — encerrando sem montar nenhuma transacao "
            "(idempotencia)."
        )
        return

    print(
        f"Schema '{SCHEMA_ID}' ainda nao existe no canal '{CHANNEL_NAME}'. "
        "Prosseguindo para tentar registra-lo."
    )
    print()

    schema_input = (
        SCHEMA_ID,
        SCHEMA_NAME,
        data_hash,
        CHANNEL_NAME,
        SCHEMA_DESCRIPTION,
    )
    print("SchemaInput a ser enviado (ordem: schemaId, name, dataHash, channelName, description):")
    print(f"  schemaId:    {SCHEMA_ID!r}")
    print(f"  name:        {SCHEMA_NAME!r}")
    print(f"  dataHash:    {data_hash.hex()}")
    print(f"  channelName: {CHANNEL_NAME!r}")
    print(f"  description: {SCHEMA_DESCRIPTION!r}")
    print()

    print("=" * 70)
    print("PASSO 2: createSchema(SchemaInput), assinada pela conta do certifier")
    print("=" * 70)
    print(
        "Rodada anterior: registerSchema reverteu generico e createSchema "
        "reverteu com custom error (address, string), assinando com a conta "
        "admin (DEPLOYER_PRIVATE_KEY) — que NAO e membro do canal. Hipotese "
        "confirmada nesta rodada (PASSO 0b acima): a conta do certifier E "
        "membro. Tentando so createSchema, conforme combinado — se reverter "
        "de novo, para e reporta o erro exato, sem tentar mais variacoes."
    )
    print()

    success, result, tx_hash = try_write_function(
        w3, account, CREATE_SCHEMA_ABI, "createSchema", schema_input
    )

    if not success:
        print("=" * 70)
        print("FALHA: createSchema(SchemaInput) reverteu mesmo com a conta do certifier (membro confirmado).")
        print("=" * 70)
        print(f"  createSchema: {result}")
        print()
        print(
            "Nao tentando mais nomes/ordens de struct sem confirmacao humana "
            "(mesma disciplina do resto desta sessao — ver "
            "docs/referencia-contratos/README.md)."
        )
        sys.exit(1)

    receipt = result
    print(f"SUCESSO com createSchema(SchemaInput). tx_hash={tx_hash.hex()}")
    print()

    print("=" * 70)
    print("PASSO 3: getActiveSchema() DEPOIS da confirmacao, e comparacao do dataHash")
    print("=" * 70)
    try:
        schema_after, exists_after = read_contract.functions.getActiveSchema(
            CHANNEL_NAME, SCHEMA_ID
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getActiveSchema(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_schema("depois", schema_after, exists_after)

    if not exists_after:
        print(
            "AVISO: apos a transacao com receipt.status=1, getActiveSchema() "
            "ainda retorna exists=False. Isso e inconsistente e deve ser "
            "investigado — a funcao de escrita pode ter tido sucesso sem "
            "de fato persistir o schema, ou getActiveSchema() pode ter algum "
            "criterio de ativacao adicional nao conhecido."
        )
        return

    data_hash_returned = schema_after[3]  # indice 3 = dataHash na struct Schema
    hashes_match = bytes(data_hash_returned) == bytes(data_hash)
    print(f"dataHash calculado no passo 0: {data_hash.hex()}")
    print(
        f"dataHash retornado por getActiveSchema(): "
        f"{data_hash_returned.hex() if isinstance(data_hash_returned, (bytes, bytearray)) else data_hash_returned}"
    )
    print(f"Batem byte a byte: {hashes_match}")

    if exists_after and hashes_match:
        print(
            f"OK: schema '{SCHEMA_ID}' registrado com sucesso no canal "
            f"'{CHANNEL_NAME}', exists=True e dataHash confere."
        )
    else:
        print(
            "AVISO: exists=True mas o dataHash NAO confere com o calculado. "
            "Investigar antes de considerar o registro correto."
        )


if __name__ == "__main__":
    main()
