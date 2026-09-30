"""
create_process.py

Registra o processo "honey-certification" (natureza "HoneyApis", estagio
"issuance") no contrato ProcessRegistry, na rede Semear (Besu privado, chain
ID 648, zero gas, acessivel via tunel SSH em http://localhost:8545).

Uso:
    python scripts/blockchain/create_process.py

Pre-requisitos:
    - Tunel SSH ativo expondo o RPC do Besu em http://localhost:8545.
    - Dependencias instaladas (ver scripts/blockchain/requirements.txt).
    - Arquivo `certifier/keys/certifier-besu.key` presente (hex puro, 64
      caracteres, sem prefixo "0x" — mesmo formato ja confirmado em
      create_schema.py). A tx de escrita e assinada por essa conta, mesma
      usada com sucesso em create_schema.py.

MESMO PADRAO DE create_schema.py
-----------------------------------
Este script segue o mesmo padrao estrutural, disclaimers e conta assinante
de scripts/blockchain/create_schema.py:
  - Confirma tunel (is_connected + chain_id==648) antes de qualquer coisa.
  - Chama a funcao de leitura (getProcess) ANTES de qualquer transacao;
    se exists=True, imprime e PARA sem enviar nada (idempotencia).
  - So tenta a funcao de escrita se exists=False.
  - Assina com certifier/keys/certifier-besu.key (mesma conta que ja
    confirmou ser membro do canal "mel-trust" em create_schema.py — esta
    rodada NAO reconfirma isChannelMember, so confirma que o arquivo de
    chave carrega do mesmo jeito).
  - Erros de transacao (revert, mismatch de selector, etc.) aparecem no
    output tal como vieram do node, nunca mascarados.

DIFERENCA vs create_schema.py: ORDEM DE TENTATIVA DAS FUNCOES DE ESCRITA
---------------------------------------------------------------------------
create_schema.py tentava registerSchema primeiro, depois createSchema (e
so createSchema teve sucesso). Para este script, a instrucao explicita foi
tentar `createProcess` PRIMEIRO e SO tentar `registerProcess` se
`createProcess` reverter — nao o contrario. Se ambos revertarem, o script
para e reporta os 2 erros exatos, sem inventar um terceiro nome.

ORIGEM DA STRUCT ProcessInput E DAS FUNCOES DE ESCRITA — DISCLAIMER COMPLETO
-----------------------------------------------------------------------------
NADA nesta secao foi confirmado contra codigo-fonte Solidity real. Nao
existe nenhum `ProcessRegistry.sol` neste repositorio (ver CLAUDE.md e
docs/referencia-contratos/README.md).

- A struct `Process` (retorno de `getProcess`) ESTA confirmada na forma dos
  campos de alto nivel (ver docs/referencia-contratos/README.md secao 4:
  processId, natureId, stageId, schemas (SchemaReference[]), action,
  performCalculation, generateNewAsset, description, owner, channelName,
  status, createdAt, lastUpdated — 4 chamadas testadas sem revert,
  exists=False). O CONTEUDO INTERNO do struct `SchemaReference` (elemento do
  array `schemas`), porem, NUNCA foi lido/confirmado no README — so
  decodificado "o suficiente pra nao quebrar o ABI decoder", sem detalhar
  campos.

- Para este script, o campo interno de `SchemaReference`/entrada de
  `schemas` em `ProcessInput` foi dado pelo USUARIO via chat, explicitamente
  como SUPOSICAO, nao fato confirmado: `{schemaId: string, version:
  uint256}`. Esse mesmo layout de 2 campos e usado tanto para MONTAR o
  array `schemas` do `ProcessInput` de escrita quanto para DECODIFICAR o
  array `schemas` retornado por `getProcess` (reaproveitando a mesma
  suposicao para leitura e escrita, ja que o README nao documenta um layout
  alternativo). Se o layout real for diferente, a chamada de leitura
  getProcess() pode falhar ao decodificar (erro ja teria aparecido em testes
  anteriores se fosse esse o caso) ou a chamada de escrita pode reverter por
  mismatch de encoding — erro reportado tal como vier do node.

- A struct `ProcessInput` usada abaixo NAO e a struct `Process` de retorno —
  e uma DESCRICAO em linguagem natural dada pelo usuario nesta sessao para a
  entrada da funcao de escrita: ProcessInput{processId, natureId, stageId,
  schemas, action, performCalculation, generateNewAsset, description,
  channelName} (9 campos, sem owner/status/createdAt/lastUpdated —
  presumivelmente preenchidos pelo contrato internamente, mesmo padrao de
  SchemaInput vs Schema em create_schema.py). NUNCA foi vista em codigo-fonte
  real.

- O valor do enum `ProcessAction` usado (`CREATE_ASSET = 0`) e SUPOSICAO do
  usuario, no README o enum e citado como "decodificado como uint8", sem os
  valores nomeados confirmados.

- Os NOMES das funcoes de escrita (`createProcess`, `registerProcess`)
  tambem NAO estao confirmados. README secao 4 e explicito: "NAO CONFIRMADO:
  funcao de escrita para registrar processo novo (nome/assinatura exatos
  ainda nao pedidos)". Sao PALPITES dado o padrao de nomenclatura de
  createSchema/registerSchema tentados no contrato irmao SchemaRegistry, nao
  fatos. Se ambos revertarem, isso NAO sera tratado como "tentar mais um
  nome" — sera reportado como falha real, pedindo confirmacao humana antes
  de qualquer nova tentativa (mesma disciplina de create_schema.py).

- O tipo de retorno das funcoes de escrita tambem e desconhecido. O ABI
  abaixo declara `outputs: []` (sem retorno) para as duas tentativas, mesma
  suposicao de create_schema.py (createSchema/createChannel sem retorno).

ENDERECO USADO
---------------
ProcessRegistry: 0x9A676e781A523b5d0C0e43731313A708CB607508
(ja confirmado com getVersion()=="1.0.0" e getProcess() funcionando sem
revert em sessao anterior — ver docs/referencia-contratos/README.md secao
4). Este script NAO resolve o endereco de novo via AddressDiscovery, usa o
valor ja confirmado (mesmo padrao de create_schema.py com SchemaRegistry).

POR QUE gasPrice = w3.eth.gas_price EM VEZ DE 0 HARDCODADO
-------------------------------------------------------------
Mesmo raciocinio de create_schema.py: a rede e "zero gas", mas o script le o
valor real via w3.eth.gas_price em vez de supor 0.

FLUXO DESTE SCRIPT (resumo)
-----------------------------
1. Confirma tunel: is_connected() e chain_id==648. Se cair no meio da
   execucao, para e reporta.
2. Carrega a chave do certifier (mesmo arquivo/formato de create_schema.py).
3. Chama getProcess("mel-trust", "honey-certification", "HoneyApis",
   "issuance") ANTES de qualquer transacao.
   - Se exists == True: imprime a struct Process completa e TERMINA, sem
     montar nem enviar transacao alguma.
4. Se exists == False: tenta createProcess(ProcessInput) primeiro; SO se
   reverter, tenta registerProcess(ProcessInput) com a mesma struct. Para no
   primeiro sucesso. Se as duas revertarem, reporta os 2 erros exatos e
   encerra sem tentar mais nada.
5. Se uma tx teve sucesso (receipt.status == 1): chama getProcess() de novo,
   confirma exists=True e compara processId/natureId/stageId/description/
   channelName com o que foi enviado.

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

PROCESS_REGISTRY_ADDRESS = "0x9A676e781A523b5d0C0e43731313A708CB607508"

# Chave Besu do certifier — mesma conta usada com sucesso em
# create_schema.py (membro confirmado do canal "mel-trust"). Formato do
# arquivo: hex puro (64 caracteres, sem prefixo "0x", sem quebras de linha),
# ja confirmado antes. Mesmo padrao de leitura: path.read_text().strip() +
# Account.from_key().
CERTIFIER_KEY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "certifier" / "keys" / "certifier-besu.key"
)

CHANNEL_NAME = "mel-trust"
PROCESS_ID = "honey-certification"
NATURE_ID = "HoneyApis"
STAGE_ID = "issuance"
PROCESS_DESCRIPTION = (
    "Emissao de credencial de certificacao para lote de mel de Apis, apos "
    "validacao regulatoria"
)
SCHEMA_ID_REF = "honey-validation-shapes"
SCHEMA_VERSION_REF = 1
PROCESS_ACTION_CREATE_ASSET = 0  # suposicao do usuario, ver disclaimer acima

# Layout de SchemaReference — SUPOSICAO do usuario (nao confirmada contra
# .sol real), usada tanto para montar ProcessInput.schemas quanto para
# decodificar Process.schemas no retorno de getProcess.
SCHEMA_REFERENCE_COMPONENTS = [
    {"internalType": "string", "name": "schemaId", "type": "string"},
    {"internalType": "uint256", "name": "version", "type": "uint256"},
]

# ABI para getProcess — CONFIRMADO na forma dos campos de alto nivel contra
# o node real em sessao anterior (ver docs/referencia-contratos/README.md
# secao 4: 4 combinacoes testadas, sem revert, exists=False). O conteudo
# interno de SchemaReference (dentro de `schemas`) usa a suposicao acima,
# nao confirmada.
PROCESS_STRUCT_COMPONENTS = [
    {"internalType": "string", "name": "processId", "type": "string"},
    {"internalType": "string", "name": "natureId", "type": "string"},
    {"internalType": "string", "name": "stageId", "type": "string"},
    {
        "internalType": "struct SchemaReference[]",
        "name": "schemas",
        "type": "tuple[]",
        "components": SCHEMA_REFERENCE_COMPONENTS,
    },
    {"internalType": "uint8", "name": "action", "type": "uint8"},
    {"internalType": "bool", "name": "performCalculation", "type": "bool"},
    {"internalType": "bool", "name": "generateNewAsset", "type": "bool"},
    {"internalType": "string", "name": "description", "type": "string"},
    {"internalType": "address", "name": "owner", "type": "address"},
    {"internalType": "string", "name": "channelName", "type": "string"},
    {"internalType": "uint8", "name": "status", "type": "uint8"},
    {"internalType": "uint256", "name": "createdAt", "type": "uint256"},
    {"internalType": "uint256", "name": "lastUpdated", "type": "uint256"},
]

GET_PROCESS_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "string", "name": "processId", "type": "string"},
            {"internalType": "string", "name": "natureId", "type": "string"},
            {"internalType": "string", "name": "stageId", "type": "string"},
        ],
        "name": "getProcess",
        "outputs": [
            {
                "internalType": "struct Process",
                "name": "process",
                "type": "tuple",
                "components": PROCESS_STRUCT_COMPONENTS,
            },
            {"internalType": "bool", "name": "exists", "type": "bool"},
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

# ProcessInput — DESCRICAO em linguagem natural dada pelo usuario via chat
# (9 campos, sem owner/status/createdAt/lastUpdated). NUNCA vista em .sol
# real — ver disclaimer completo no topo do arquivo.
PROCESS_INPUT_COMPONENTS = [
    {"internalType": "string", "name": "processId", "type": "string"},
    {"internalType": "string", "name": "natureId", "type": "string"},
    {"internalType": "string", "name": "stageId", "type": "string"},
    {
        "internalType": "struct SchemaReference[]",
        "name": "schemas",
        "type": "tuple[]",
        "components": SCHEMA_REFERENCE_COMPONENTS,
    },
    {"internalType": "uint8", "name": "action", "type": "uint8"},
    {"internalType": "bool", "name": "performCalculation", "type": "bool"},
    {"internalType": "bool", "name": "generateNewAsset", "type": "bool"},
    {"internalType": "string", "name": "description", "type": "string"},
    {"internalType": "string", "name": "channelName", "type": "string"},
]

CREATE_PROCESS_ABI = [
    {
        "inputs": [
            {
                "internalType": "struct ProcessInput",
                "name": "input",
                "type": "tuple",
                "components": PROCESS_INPUT_COMPONENTS,
            }
        ],
        "name": "createProcess",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

REGISTER_PROCESS_ABI = [
    {
        "inputs": [
            {
                "internalType": "struct ProcessInput",
                "name": "input",
                "type": "tuple",
                "components": PROCESS_INPUT_COMPONENTS,
            }
        ],
        "name": "registerProcess",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


def load_certifier_private_key() -> str:
    """Carrega a chave privada Besu do certifier de
    certifier/keys/certifier-besu.key. Mesmo formato/leitura de
    create_schema.py: hex puro, 64 caracteres, sem prefixo "0x". Nunca
    imprime a chave — so o endereco derivado dela.
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


def print_process(label: str, process_tuple, exists: bool) -> None:
    print(
        f"--- getProcess('{CHANNEL_NAME}', '{PROCESS_ID}', '{NATURE_ID}', "
        f"'{STAGE_ID}') [{label}] ---"
    )
    print(f"  exists: {exists}")
    if exists:
        (
            process_id,
            nature_id,
            stage_id,
            schemas,
            action,
            perform_calculation,
            generate_new_asset,
            description,
            owner,
            channel_name,
            status,
            created_at,
            last_updated,
        ) = process_tuple
        print(f"  processId:          {process_id}")
        print(f"  natureId:           {nature_id}")
        print(f"  stageId:            {stage_id}")
        print(f"  schemas:            {schemas}")
        print(f"  action:             {action}")
        print(f"  performCalculation: {perform_calculation}")
        print(f"  generateNewAsset:   {generate_new_asset}")
        print(f"  description:        {description}")
        print(f"  owner:              {owner}")
        print(f"  channelName:        {channel_name}")
        print(f"  status:             {status}")
        print(f"  createdAt:          {created_at}")
        print(f"  lastUpdated:        {last_updated}")
    print()


def try_write_function(
    w3: Web3,
    account,
    abi: list,
    function_name: str,
    process_input: tuple,
):
    """Tenta montar, assinar e enviar uma transacao para a funcao de escrita
    dada. Retorna (True, receipt, tx_hash) em sucesso, (False, erro_str,
    None) em falha. Nao mascara nenhum erro.
    """
    print(f"--- Tentativa: {function_name}(ProcessInput) ---")
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(PROCESS_REGISTRY_ADDRESS), abi=abi
    )
    fn = getattr(contract.functions, function_name)

    try:
        nonce = w3.eth.get_transaction_count(account.address)
        gas_price = w3.eth.gas_price
        print(f"  nonce: {nonce}")
        print(f"  gas_price lido de w3.eth.gas_price: {gas_price}")

        estimated_gas = fn(process_input).estimate_gas({"from": account.address})
        print(f"  gas estimado (estimate_gas): {estimated_gas}")

        tx = fn(process_input).build_transaction(
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
    print(
        f"REGISTRO DE PROCESSO '{PROCESS_ID}' (natureza '{NATURE_ID}', "
        f"estagio '{STAGE_ID}') NO ProcessRegistry (canal '{CHANNEL_NAME}')"
    )
    print("=" * 70)
    print(f"RPC URL: {RPC_URL}")
    print(f"ProcessRegistry: {PROCESS_REGISTRY_ADDRESS}")
    print()

    private_key = load_certifier_private_key()
    account = Account.from_key(private_key)
    print(f"Conta derivada de certifier/keys/certifier-besu.key: {account.address}")
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

    read_contract = w3.eth.contract(
        address=Web3.to_checksum_address(PROCESS_REGISTRY_ADDRESS),
        abi=GET_PROCESS_ABI,
    )

    print("=" * 70)
    print("PASSO 1: getProcess() ANTES de qualquer transacao")
    print("=" * 70)
    try:
        process_before, exists_before = read_contract.functions.getProcess(
            CHANNEL_NAME, PROCESS_ID, NATURE_ID, STAGE_ID
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getProcess(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_process("antes", process_before, exists_before)

    if exists_before:
        print(
            f"AVISO: o processo '{PROCESS_ID}'/'{NATURE_ID}'/'{STAGE_ID}' no "
            f"canal '{CHANNEL_NAME}' JA EXISTE. Nada sera enviado — "
            "encerrando sem montar nenhuma transacao (idempotencia)."
        )
        return

    print(
        f"Processo '{PROCESS_ID}'/'{NATURE_ID}'/'{STAGE_ID}' ainda nao "
        f"existe no canal '{CHANNEL_NAME}'. Prosseguindo para tentar "
        "registra-lo."
    )
    print()

    schemas_input = ((SCHEMA_ID_REF, SCHEMA_VERSION_REF),)
    process_input = (
        PROCESS_ID,
        NATURE_ID,
        STAGE_ID,
        schemas_input,
        PROCESS_ACTION_CREATE_ASSET,
        False,  # performCalculation
        False,  # generateNewAsset
        PROCESS_DESCRIPTION,
        CHANNEL_NAME,
    )
    print(
        "ProcessInput a ser enviado (ordem: processId, natureId, stageId, "
        "schemas, action, performCalculation, generateNewAsset, "
        "description, channelName):"
    )
    print(f"  processId:          {PROCESS_ID!r}")
    print(f"  natureId:           {NATURE_ID!r}")
    print(f"  stageId:            {STAGE_ID!r}")
    print(f"  schemas:            [{{schemaId: {SCHEMA_ID_REF!r}, version: {SCHEMA_VERSION_REF}}}]")
    print(f"  action:             {PROCESS_ACTION_CREATE_ASSET} (CREATE_ASSET, suposicao)")
    print("  performCalculation: False")
    print("  generateNewAsset:   False")
    print(f"  description:        {PROCESS_DESCRIPTION!r}")
    print(f"  channelName:        {CHANNEL_NAME!r}")
    print()

    print("=" * 70)
    print("PASSO 2: createProcess(ProcessInput), assinada pela conta do certifier")
    print("=" * 70)
    print(
        "Tentando createProcess primeiro (nao registerProcess), conforme "
        "combinado. So tenta registerProcess se createProcess reverter. Se "
        "ambos revertarem, para e reporta os 2 erros exatos, sem inventar "
        "um terceiro nome."
    )
    print()

    success, result, tx_hash = try_write_function(
        w3, account, CREATE_PROCESS_ABI, "createProcess", process_input
    )

    if not success:
        create_process_error = result
        print("=" * 70)
        print("createProcess reverteu. Tentando registerProcess(ProcessInput) em seguida.")
        print("=" * 70)
        success2, result2, tx_hash2 = try_write_function(
            w3, account, REGISTER_PROCESS_ABI, "registerProcess", process_input
        )
        if not success2:
            print("=" * 70)
            print("FALHA: createProcess E registerProcess reverteram.")
            print("=" * 70)
            print(f"  createProcess:   {create_process_error}")
            print(f"  registerProcess: {result2}")
            print()
            print(
                "Nao tentando mais nomes/ordens de struct sem confirmacao "
                "humana (mesma disciplina de create_schema.py — ver "
                "docs/referencia-contratos/README.md)."
            )
            sys.exit(1)
        success, result, tx_hash = success2, result2, tx_hash2
        function_used = "registerProcess"
    else:
        function_used = "createProcess"

    receipt = result
    print(f"SUCESSO com {function_used}(ProcessInput). tx_hash={tx_hash.hex()}")
    print()

    print("=" * 70)
    print("PASSO 3: getProcess() DEPOIS da confirmacao, e comparacao dos campos")
    print("=" * 70)
    try:
        process_after, exists_after = read_contract.functions.getProcess(
            CHANNEL_NAME, PROCESS_ID, NATURE_ID, STAGE_ID
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getProcess(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_process("depois", process_after, exists_after)

    if not exists_after:
        print(
            "AVISO: apos a transacao com receipt.status=1, getProcess() "
            "ainda retorna exists=False. Isso e inconsistente e deve ser "
            "investigado."
        )
        return

    (
        process_id_ret,
        nature_id_ret,
        stage_id_ret,
        _schemas_ret,
        _action_ret,
        _perform_calc_ret,
        _generate_asset_ret,
        description_ret,
        _owner_ret,
        channel_name_ret,
        _status_ret,
        _created_at_ret,
        _last_updated_ret,
    ) = process_after

    fields_match = (
        process_id_ret == PROCESS_ID
        and nature_id_ret == NATURE_ID
        and stage_id_ret == STAGE_ID
        and description_ret == PROCESS_DESCRIPTION
        and channel_name_ret == CHANNEL_NAME
    )
    print(
        f"Campos batendo (processId, natureId, stageId, description, "
        f"channelName): {fields_match}"
    )

    if exists_after and fields_match:
        print(
            f"OK: processo '{PROCESS_ID}'/'{NATURE_ID}'/'{STAGE_ID}' "
            f"registrado com sucesso no canal '{CHANNEL_NAME}', exists=True "
            "e campos conferem."
        )
    else:
        print(
            "AVISO: exists=True mas algum campo NAO confere com o enviado. "
            "Investigar antes de considerar o registro correto."
        )


if __name__ == "__main__":
    main()
