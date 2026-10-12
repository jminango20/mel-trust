"""
Configuração básica do nó certifier.

Lê variáveis de ambiente (com defaults sensatos para dev local) via
python-dotenv + os.environ. Sem pydantic-settings — fora do escopo do M1
"""
import os

from dotenv import load_dotenv

# Carrega certifier/.env se existir (não obrigatório em dev).
load_dotenv()

# Nome legível do nó, usado em metadata/logs.
NODE_NAME = os.environ.get("NODE_NAME", "certifier")

# URL base pública do nó. Em produção isso deveria bater com o domínio
# usado para resolver o did:web (ver DID_WEB abaixo). Em dev, localhost.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8003")

# DID real do certifier (M5). Mesma convenção de coop-a/config.py: porta
# percent-encoded (`%3A`) como segmento do did:web, resolve para
# GET {BASE_URL}/.well-known/did.json (ver main.py).
DID_WEB = os.environ.get("DID_WEB", "did:web:localhost%3A8003")

# --- M2: token endpoint (pre-authorized_code flow) ---

# Código pre-authorized_code HARDCODED/fixo para o MVP. Na spec real este
# código seria gerado dinamicamente por requisição (um por "credential
# offer", de uso único), mas o fluxo de credential offer não existe ainda
# neste repo. Trocável via env var só para não deixar o valor literal
# espalhado, mas continua sendo um único valor fixo compartilhado — sem
# geração, expiração ou consumo de uso único real. Mesmo padrão de coop-a.
PRE_AUTHORIZED_CODE = os.environ.get("PRE_AUTHORIZED_CODE", "certifier-demo-pre-auth-code")

# TTL do access_token emitido pelo /token, em segundos.
ACCESS_TOKEN_TTL_SECONDS = int(os.environ.get("ACCESS_TOKEN_TTL_SECONDS", "3600"))

# --- M2.5: nonce endpoint (c_nonce para key proof, ver nonce_endpoint.py) ---

# TTL do c_nonce emitido pelo /nonce, em segundos. Curto de propósito: o
# c_nonce só precisa viver o suficiente para o wallet montar e assinar o
# JWT de proof-of-possession e enviar o credential request (M3) logo em
# seguida — não é um token de sessão de longa duração como o access_token.
C_NONCE_TTL_SECONDS = int(os.environ.get("C_NONCE_TTL_SECONDS", "300"))

# --- M3: credential endpoint (emissão de SD-JWT VC) ---

# Diretório deste arquivo (certifier/), usado para resolver o path default
# da chave do issuer de forma independente do cwd em que o uvicorn é
# iniciado.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Path do arquivo PEM (PKCS8, sem senha) com o par de chaves Ed25519 do
# issuer certifier, usado para assinar (JWS) os SD-JWT VC emitidos em
# POST /credential. NUNCA hardcoded no código — sempre via esta env var,
# para não acoplar o path físico da chave à lógica de assinatura (facilita
# trocar de chave/segredo por ambiente sem tocar em código). Ver keys.py:
# se o arquivo não existir neste path, um novo par é gerado e salvo aqui
# na primeira inicialização (decisão de MVP — ver docstring de keys.py).
# O diretório keys/ é git-ignorado (ver .gitignore na raiz do repo):
# a chave privada nunca é versionada, nem em ambiente de teste.
ISSUER_KEY_PATH = os.environ.get(
    "ISSUER_KEY_PATH", os.path.join(_THIS_DIR, "keys", "certifier.pem")
)

# Validade (em segundos) da credencial emitida, usada para calcular o claim
# `exp` a partir do `iat` no momento da emissão. Uma credencial de
# certificação laboratorial tem uma noção mais natural de "validade" que um
# lote (um laudo pode ter prazo de validade regulatória), mas ainda não
# modelamos isso a partir de dado real — usamos o mesmo valor arbitrário de
# MVP (1 ano) de coop-a/coop-b só para popular o claim `exp` exigido pela
# spec SD-JWT VC como sempre-visível.
CREDENTIAL_VALIDITY_SECONDS = int(
    os.environ.get("CREDENTIAL_VALIDITY_SECONDS", str(365 * 24 * 3600))
)

# --- M4: leitura do Fuseki (SPARQL SELECT) para montar claims reais ---
#
# Mesmos nomes de env var que pipeline.py (M1.5) já usa para ESCREVER no
# Fuseki (INSERT DATA via /update) — reaproveitados aqui para LER (SELECT via
# /sparql): é o mesmo dataset "certifier", a mesma instância Fuseki, só um
# endpoint HTTP diferente dentro dela. Mesma decisão de nomenclatura de
# coop-a/coop-b.
# `127.0.0.1`, não `localhost` — mesma razão de coop-a/config.py (IPv6
# quebrado no encaminhamento de porta do Docker Desktop nesta máquina).
FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
FUSEKI_ADMIN_USER = os.environ.get("FUSEKI_ADMIN_USER", "admin")
FUSEKI_ADMIN_PASSWORD = os.environ.get("FUSEKI_ADMIN_PASSWORD")

# Batch number default usado quando o corpo de POST /credential não informa
# um `batch_number` explícito (extensão própria do MVP). AQUI, diferente de
# coop-a/coop-b, este valor identifica o LOTE SOBRE O QUAL O LAUDO FALA —
# fallback de qual lote consultar via SPARQL federado (ver abaixo) quando
# nenhum `batch_number` vem no corpo de POST /credential.
DEFAULT_BATCH_NUMBER = os.environ.get("DEFAULT_BATCH_NUMBER", "MS-2026-042")

# --- M5.6 (pós-narrativa): federação para o gate regulatório real ---
#
# O certifier consulta, em tempo real, o Fuseki do lab (medições
# físico-químicas) e de coop-a/coop-b (productType do lote) via SPARQL
# federado, mesmo padrão `SERVICE` já usado em
# verifier-demo/verify_endpoint.py (ver docstring lá para o porquê de
# `host.docker.internal`: estas URLs são resolvidas de DENTRO do container
# do Fuseki do certifier — que atua como nó de entrada desta consulta —,
# não pelo processo Python do certifier). Nenhuma dessas três é
# `127.0.0.1`: essa correção (ver FUSEKI_URL acima) vale para conexão
# HOST -> porta publicada; aqui o contexto de rede é outro (container ->
# host), resolvido por host.docker.internal.
#
# HISTÓRICO: esta federação chegou a ser reduzida pra só coop-a/coop-b (lab
# virou stateless por um turno) — REVERTIDO. LAB_SPARQL_SERVICE_URL voltou.
# Ver docs/known-issues.md/CHANGELOG.md.
COOP_A_SPARQL_SERVICE_URL = os.environ.get(
    "COOP_A_SPARQL_SERVICE_URL", "http://host.docker.internal:3030/coop-a/sparql"
)
COOP_B_SPARQL_SERVICE_URL = os.environ.get(
    "COOP_B_SPARQL_SERVICE_URL", "http://host.docker.internal:3031/coop-b/sparql"
)
LAB_SPARQL_SERVICE_URL = os.environ.get(
    "LAB_SPARQL_SERVICE_URL", "http://host.docker.internal:3033/lab/sparql"
)


# --- Rede Semear (Besu): RPC, chainId, contratos e canal da ancoragem on-chain ---
# Lidos por anchoring.py. Variáveis com prefixo BESU_ (mesmo prefixo do
# BESU_KEY_PATH de anchoring.py); os DEFAULTS são os valores confirmados em
# docs/referencia-contratos/README.md — sem a variável, nada muda.
# `localhost` (não 127.0.0.1) no RPC é o valor histórico dos nós; ver
# docs/known-issues.md sobre o custo do `localhost` no Windows.
RPC_URL = os.environ.get("BESU_RPC_URL", "http://127.0.0.1:8545")
EXPECTED_CHAIN_ID = int(os.environ.get("BESU_CHAIN_ID", "648"))
# TransactionOrchestrator: endereço já CONFIRMADO (seção 5 do README dos contratos),
# hardcoded por default e não resolvido via AddressDiscovery em runtime (o contrato
# não é upgradeable neste MVP).
TRANSACTION_ORCHESTRATOR_ADDRESS = os.environ.get(
    "BESU_TRANSACTION_ORCHESTRATOR_ADDRESS", "0x959922bE3CAee4b8Cd9a407cc3ac1C251C2007B1"
)
# AddressDiscovery: usado para resolver o AssetRegistry AO VIVO (leituras via getAsset).
ADDRESS_DISCOVERY_ADDRESS = os.environ.get(
    "BESU_ADDRESS_DISCOVERY_ADDRESS", "0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e"
)
CHANNEL_NAME = os.environ.get("BESU_CHANNEL_NAME", "mel-trust")
