"""
create_lab_analysis_processes.py

Registra DOIS processos "honey-lab-analysis" (estagio "reporting") no
contrato ProcessRegistry, na rede Semear (Besu privado, chain ID 648, zero
gas, acessivel via tunel SSH em http://localhost:8545) — um por especie:

  1. natureId "HoneyApis"                  (mel de Apis)
  2. natureId "HoneyNativeStinglessBee"     (mel nativo/meliponineo)

Uso:
    python scripts/blockchain/create_lab_analysis_processes.py

Pre-requisitos:
    - Tunel SSH ativo expondo o RPC do Besu em http://localhost:8545.
    - Dependencias instaladas (ver scripts/blockchain/requirements.txt).
    - Arquivo `lab/keys/lab-besu.key` presente (hex puro, 64 caracteres,
      sem prefixo "0x" — mesmo formato ja confirmado em
      certifier/keys/certifier-besu.key e coop-a/keys/coop-a-besu.key). A
      tx de escrita e assinada por essa conta (lab), NAO pela conta do
      certifier ou da coop-a usadas em scripts anteriores.

MESMO PADRAO DE create_batch_registration_processes.py
--------------------------------------------------------
Este script reaproveita a mesma struct/ABI/disclaimers de
scripts/blockchain/create_batch_registration_processes.py (que por sua vez
reaproveita create_process.py — ver secao 4 do README de referencia de
contratos), mudando so:
  - a conta assinante: lab (lab-besu.key) em vez de coop-a/certifier;
  - processId/stageId/description dos 2 processos (honey-lab-analysis /
    reporting, em vez de honey-batch-registration / creation);
  - o valor do enum `action`: 9 em vez de 0 (ver disclaimer ABAIXO — esta e
    uma suposicao NOVA, nunca testada antes nesta sessao);
  - a checagem OBRIGATORIA de isChannelMember("mel-trust", <endereco do
    lab>) ANTES de qualquer transacao — mesmo se a chave do lab ja tiver
    sido confirmada membro do canal em tarefa anterior, este script
    reconfirma sempre. Se vier False, para e reporta, sem tentar nenhuma
    escrita.

ORIGEM DA STRUCT ProcessInput E DAS FUNCOES DE ESCRITA — DISCLAIMER COMPLETO
-----------------------------------------------------------------------------
Repete o disclaimer de create_process.py / create_batch_registration_processes.py
(ver esses arquivos e docs/referencia-contratos/README.md secao 4 para o
historico completo):

- `createProcess(ProcessInput)` esta CONFIRMADO contra o node real (multiplas
  tx bem-sucedidas em rodadas anteriores, ver README secao 4). Este script
  tenta SO `createProcess` — nao tenta `registerProcess` nem nenhuma
  variacao de nome.
- O layout de `SchemaReference` (`{schemaId: string, version: uint256}`)
  continua sendo SUPOSICAO do usuario, nao confirmada contra .sol real.
- Struct `Process` (retorno de getProcess) e `ProcessInput` (entrada de
  createProcess) tem os campos de alto nivel CONFIRMADOS (ver README secao
  4); o conteudo interno de `schemas` nao.

DISCLAIMER NOVO DESTA RODADA — enum ProcessAction, valor 9
-------------------------------------------------------------
Ate agora, o unico valor de `action` (uint8, enum `ProcessAction`) testado
contra o node real foi `0` (usado como "CREATE_ASSET", tambem suposicao de
nome — so o valor numerico 0 foi de fato exercitado com sucesso).

Nesta rodada, o usuario pediu explicitamente `action: 9`, com a hipotese de
que corresponda a um valor chamado "CREATE_DOCUMENT" no enum `ProcessAction`
real do contrato. Isso e:
  - uma suposicao NOVA, nunca testada nesta sessao nem em rodadas
    anteriores;
  - so o INTEIRO 9 esta sendo testado aqui, nao o nome "CREATE_DOCUMENT".
    Se a transacao passar (receipt.status == 1), isso e evidencia de que o
    enum ProcessAction aceita/nao rejeita o valor 9 (ou seja, o enum tem
    pelo menos 10 valores possiveis, 0..9, ou o contrato nao valida o
    range) — MAS NAO confirma que o nome semantico associado a esse valor
    seja "CREATE_DOCUMENT". O nome so poderia ser confirmado lendo o .sol
    real do enum, que nao esta disponivel neste repositorio (ver secao "Como
    este arquivo deve ser mantido" do README).
  - se a transacao reverter, o script para imediatamente e reporta o erro
    exato — NAO tenta nenhum outro valor de enum sem confirmacao humana.

DIFERENCA vs create_batch_registration_processes.py: DOIS PROCESSOS, MESMA
ORDEM DO CHAT, PROCESSO/STAGE/ACTION DIFERENTES, CONTA DIFERENTE
---------------------------------------------------------------------------
Para CADA processo, nessa ordem (Apis primeiro, depois nativo/meliponineo):
  1. getProcess() ANTES — confirma exists=False.
     - Se ja exists=True: pula a escrita DESSE processo, reporta
       claramente (idempotencia), e segue para o proximo processo da lista
       (nao para o script inteiro).
  2. Se exists=False: createProcess(ProcessInput). Espera receipt, confirma
     status.
  3. Se sucesso: getProcess() DEPOIS, confirma exists=True e campos
     batendo.
Se createProcess reverter para algum dos dois processos, o script reporta o
erro exato e PARA IMEDIATAMENTE (nao tenta o segundo processo, nao tenta
nome de funcao alternativo, nao tenta outro valor de enum).

ENDERECO USADO
---------------
ProcessRegistry: 0x9A676e781A523b5d0C0e43731313A708CB607508
(mesmo endereco ja confirmado em rodadas anteriores e no README secao 4;
este script NAO resolve de novo via AddressDiscovery).

CHECAGEM DE MEMBRO DE CANAL — POR QUE RECONFIRMAR
-----------------------------------------------------
A conta do lab foi confirmada membro do canal `mel-trust` em uma tarefa
anterior, mas nunca nesta sessao/script. Por isso a checagem e feita de
novo, sempre, antes de qualquer transacao, mesmo com a confirmacao previa
relatada pelo usuario.

POR QUE gasPrice = w3.eth.gas_price EM VEZ DE 0 HARDCODADO
-------------------------------------------------------------
Mesmo raciocinio dos scripts anteriores: a rede e "zero gas", mas o script
le o valor real via w3.eth.gas_price em vez de supor 0.

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


RPC_URL = "http://127.0.0.1:8545"
EXPECTED_CHAIN_ID = 648

ACCESS_CHANNEL_MANAGER_ADDRESS = "0xA51c1fc2f0D1a1b8494Ed1FE312d7C3a78Ed91C0"
PROCESS_REGISTRY_ADDRESS = "0x9A676e781A523b5d0C0e43731313A708CB607508"

# Chave Besu do lab — mesmo formato ja confirmado para certifier/coop-a: hex
# puro (64 caracteres, sem prefixo "0x", sem quebras de linha).
LAB_KEY_PATH = (
    Path(__file__).resolve().parent.parent.parent / "lab" / "keys" / "lab-besu.key"
)

CHANNEL_NAME = "mel-trust"
STAGE_ID = "reporting"
SCHEMA_ID_REF = "honey-validation-shapes"
SCHEMA_VERSION_REF = 1
# SUPOSICAO NOVA desta rodada — ver disclaimer completo no topo do arquivo.
# So o inteiro 9 e testado; o nome "CREATE_DOCUMENT" NAO e confirmado.
PROCESS_ACTION_VALUE = 9

PROCESSES = [
    {
        "process_id": "honey-lab-analysis",
        "nature_id": "HoneyApis",
        "description": "Registro do laudo de analise laboratorial, vinculado ao lote",
    },
    {
        "process_id": "honey-lab-analysis",
        "nature_id": "HoneyNativeStinglessBee",
        "description": "Registro do laudo de analise laboratorial de mel nativo/meliponineo, vinculado ao lote",
    },
]

# Layout de SchemaReference — SUPOSICAO do usuario (nao confirmada contra
# .sol real), usada tanto para montar ProcessInput.schemas quanto para
# decodificar Process.schemas no retorno de getProcess.
SCHEMA_REFERENCE_COMPONENTS = [
    {"internalType": "string", "name": "schemaId", "type": "string"},
    {"internalType": "uint256", "name": "version", "type": "uint256"},
]

# ABI para getProcess — CONFIRMADO na forma dos campos de alto nivel contra
# o node real em sessao anterior (ver docs/referencia-contratos/README.md
# secao 4). O conteudo interno de SchemaReference (dentro de `schemas`) usa
# a suposicao acima, nao confirmada.
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

IS_CHANNEL_MEMBER_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "channelName", "type": "string"},
            {"internalType": "address", "name": "account", "type": "address"},
        ],
        "name": "isChannelMember",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def load_lab_private_key() -> str:
    """Carrega a chave privada Besu do lab de lab/keys/lab-besu.key. Mesmo
    formato/leitura dos scripts anteriores: hex puro, 64 caracteres, sem
    prefixo "0x". Nunca imprime a chave — so o endereco derivado dela.
    """
    if not LAB_KEY_PATH.is_file():
        print(
            f"ERRO: arquivo de chave do lab nao encontrado em "
            f"{LAB_KEY_PATH}.",
            file=sys.stderr,
        )
        sys.exit(1)

    private_key = LAB_KEY_PATH.read_text(encoding="utf-8").strip()
    if not private_key:
        print(
            f"ERRO: arquivo de chave do lab em {LAB_KEY_PATH} esta vazio.",
            file=sys.stderr,
        )
        sys.exit(1)
    return private_key


def print_process(label: str, process_id: str, nature_id: str, process_tuple, exists: bool) -> None:
    print(
        f"--- getProcess('{CHANNEL_NAME}', '{process_id}', '{nature_id}', "
        f"'{STAGE_ID}') [{label}] ---"
    )
    print(f"  exists: {exists}")
    if exists:
        (
            process_id_ret,
            nature_id_ret,
            stage_id_ret,
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
        print(f"  processId:          {process_id_ret}")
        print(f"  natureId:           {nature_id_ret}")
        print(f"  stageId:            {stage_id_ret}")
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


def try_create_process(w3: Web3, account, process_input: tuple):
    """Tenta montar, assinar e enviar uma transacao createProcess. Retorna
    (True, receipt, tx_hash) em sucesso, (False, erro_str, None) em falha.
    Nao mascara nenhum erro.
    """
    print("--- Tentativa: createProcess(ProcessInput) ---")
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(PROCESS_REGISTRY_ADDRESS), abi=CREATE_PROCESS_ABI
    )
    fn = contract.functions.createProcess

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


def process_one(w3: Web3, account, read_contract, process_id: str, nature_id: str, description: str) -> bool:
    """Executa o fluxo completo (getProcess antes -> createProcess ->
    getProcess depois) para um unico processo. Retorna True se o fluxo
    terminou sem erro fatal (inclui o caso 'ja existia, pulado'). Retorna
    False (e o caller deve parar o script) se createProcess reverteu.
    """
    print("=" * 70)
    print(
        f"PROCESSO: processId='{process_id}' natureId='{nature_id}' "
        f"stageId='{STAGE_ID}'"
    )
    print("=" * 70)

    print("--- PASSO 1: getProcess() ANTES de qualquer transacao ---")
    try:
        process_before, exists_before = read_contract.functions.getProcess(
            CHANNEL_NAME, process_id, nature_id, STAGE_ID
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getProcess(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_process("antes", process_id, nature_id, process_before, exists_before)

    if exists_before:
        print(
            f"AVISO: o processo '{process_id}'/'{nature_id}'/'{STAGE_ID}' no "
            f"canal '{CHANNEL_NAME}' JA EXISTE. Pulando a escrita DESTE "
            "processo (idempotencia) e seguindo para o proximo, se houver."
        )
        print()
        return True

    print(
        f"Processo '{process_id}'/'{nature_id}'/'{STAGE_ID}' ainda nao "
        f"existe no canal '{CHANNEL_NAME}'. Prosseguindo para registra-lo."
    )
    print()

    schemas_input = ((SCHEMA_ID_REF, SCHEMA_VERSION_REF),)
    process_input = (
        process_id,
        nature_id,
        STAGE_ID,
        schemas_input,
        PROCESS_ACTION_VALUE,
        False,  # performCalculation
        False,  # generateNewAsset
        description,
        CHANNEL_NAME,
    )
    print(
        "ProcessInput a ser enviado (ordem: processId, natureId, stageId, "
        "schemas, action, performCalculation, generateNewAsset, "
        "description, channelName):"
    )
    print(f"  processId:          {process_id!r}")
    print(f"  natureId:           {nature_id!r}")
    print(f"  stageId:            {STAGE_ID!r}")
    print(f"  schemas:            [{{schemaId: {SCHEMA_ID_REF!r}, version: {SCHEMA_VERSION_REF}}}]")
    print(
        f"  action:             {PROCESS_ACTION_VALUE} (SUPOSICAO NOVA, "
        "nome 'CREATE_DOCUMENT' NAO confirmado — ver disclaimer no topo do "
        "arquivo)"
    )
    print("  performCalculation: False")
    print("  generateNewAsset:   False")
    print(f"  description:        {description!r}")
    print(f"  channelName:        {CHANNEL_NAME!r}")
    print()

    print("--- PASSO 2: createProcess(ProcessInput), assinada pela conta do lab ---")
    success, result, tx_hash = try_create_process(w3, account, process_input)

    if not success:
        print("=" * 70)
        print(f"FALHA: createProcess reverteu para '{process_id}'/'{nature_id}'.")
        print("=" * 70)
        print(f"  erro: {result}")
        print()
        print(
            "PARANDO (nao tentando o proximo processo, nao tentando "
            "variacao de nome de funcao nem de valor de enum sem "
            "confirmacao humana)."
        )
        return False

    receipt = result
    print(f"SUCESSO com createProcess(ProcessInput). tx_hash={tx_hash.hex()}")
    print()

    print("--- PASSO 3: getProcess() DEPOIS da confirmacao, e comparacao dos campos ---")
    try:
        process_after, exists_after = read_contract.functions.getProcess(
            CHANNEL_NAME, process_id, nature_id, STAGE_ID
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada getProcess(): {type(exc).__name__}: {exc}")
        sys.exit(1)

    print_process("depois", process_id, nature_id, process_after, exists_after)

    if not exists_after:
        print(
            "AVISO: apos a transacao com receipt.status=1, getProcess() "
            "ainda retorna exists=False. Isso e inconsistente e deve ser "
            "investigado."
        )
        return True

    (
        process_id_ret,
        nature_id_ret,
        stage_id_ret,
        _schemas_ret,
        action_ret,
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
        process_id_ret == process_id
        and nature_id_ret == nature_id
        and stage_id_ret == STAGE_ID
        and description_ret == description
        and channel_name_ret == CHANNEL_NAME
        and action_ret == PROCESS_ACTION_VALUE
    )
    print(
        f"Campos batendo (processId, natureId, stageId, description, "
        f"channelName, action): {fields_match}"
    )

    if exists_after and fields_match:
        print(
            f"OK: processo '{process_id}'/'{nature_id}'/'{STAGE_ID}' "
            f"registrado com sucesso no canal '{CHANNEL_NAME}', exists=True "
            "e campos conferem."
        )
    else:
        print(
            "AVISO: exists=True mas algum campo NAO confere com o enviado. "
            "Investigar antes de considerar o registro correto."
        )
    print()
    return True


def main() -> None:
    print("=" * 70)
    print(
        "REGISTRO DE 2 PROCESSOS 'honey-lab-analysis' (estagio "
        "'reporting') NO ProcessRegistry (canal 'mel-trust'), assinados "
        "pela conta do lab"
    )
    print("=" * 70)
    print(f"RPC URL: {RPC_URL}")
    print(f"AccessChannelManager: {ACCESS_CHANNEL_MANAGER_ADDRESS}")
    print(f"ProcessRegistry: {PROCESS_REGISTRY_ADDRESS}")
    print()

    private_key = load_lab_private_key()
    account = Account.from_key(private_key)
    print(f"Conta derivada de lab/keys/lab-besu.key: {account.address}")
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

    print("=" * 70)
    print("PASSO 0: isChannelMember('mel-trust', <endereco lab>) — reconfirmado agora")
    print("=" * 70)
    access_channel_manager = w3.eth.contract(
        address=Web3.to_checksum_address(ACCESS_CHANNEL_MANAGER_ADDRESS),
        abi=IS_CHANNEL_MEMBER_ABI,
    )
    try:
        is_member = access_channel_manager.functions.isChannelMember(
            CHANNEL_NAME, account.address
        ).call()
    except Exception as exc:  # noqa: BLE001 - reportar erro real, sem mascarar
        print(f"ERRO na chamada isChannelMember(): {type(exc).__name__}: {exc}")
        sys.exit(1)
    print(f"isChannelMember('{CHANNEL_NAME}', {account.address}): {is_member}")
    print()

    if not is_member:
        print(
            "PARANDO: a conta do lab NAO e membro do canal 'mel-trust' "
            "(isChannelMember == False). Nenhuma transacao sera tentada."
        )
        sys.exit(1)

    print(
        "Conta do lab confirmada como membro do canal 'mel-trust'. "
        "Prosseguindo para os 2 processos."
    )
    print()

    read_contract = w3.eth.contract(
        address=Web3.to_checksum_address(PROCESS_REGISTRY_ADDRESS),
        abi=GET_PROCESS_ABI,
    )

    for spec in PROCESSES:
        ok = process_one(
            w3,
            account,
            read_contract,
            spec["process_id"],
            spec["nature_id"],
            spec["description"],
        )
        if not ok:
            sys.exit(1)

    print("=" * 70)
    print("FIM: os 2 processos foram processados (registrados ou ja existentes).")
    print("=" * 70)


if __name__ == "__main__":
    main()
