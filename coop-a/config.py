"""
Configuração básica do nó coop-a.

Lê variáveis de ambiente (com defaults sensatos para dev local) via
python-dotenv + os.environ. Sem pydantic-settings — fora do escopo do M1
"""
import os

from dotenv import load_dotenv

# Carrega coop-a/.env se existir (não obrigatório em dev).
load_dotenv()

# Nome legível do nó, usado em metadata/logs.
NODE_NAME = os.environ.get("NODE_NAME", "coop-a")

# URL base pública do nó. Em produção isso deveria bater com o domínio
# usado para resolver o did:web (ver DID_WEB abaixo). Em dev, localhost.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8001")

# DID real do coop-a (M5). Convenção did:web spec (§ "Read Resolve", domínio
# com porta): o host `localhost:8001` vira o segmento
# `localhost%3A8001` — o `:` da porta é percent-encoded porque `:` já é o
# separador de método/segmentos dentro do próprio DID (did:web:<host>[:<path>...]).
# Resolve para GET {BASE_URL}/.well-known/did.json (ver main.py) — sem
# path adicional, então o document fica direto em /.well-known/did.json,
# não em /.well-known/did.json/<path>.
DID_WEB = os.environ.get("DID_WEB", "did:web:localhost%3A8001")

# --- M2: token endpoint (pre-authorized_code flow) ---

# Código pre-authorized_code HARDCODED/fixo para o MVP. Na spec real este
# código seria gerado dinamicamente por requisição (um por "credential
# offer", de uso único), mas o fluxo de credential offer não existe ainda
# neste repo. Trocável via env var só para não deixar o valor literal
# espalhado, mas continua sendo um único valor fixo compartilhado — sem
# geração, expiração ou consumo de uso único real.
PRE_AUTHORIZED_CODE = os.environ.get("PRE_AUTHORIZED_CODE", "coop-a-demo-pre-auth-code")

# TTL do access_token emitido pelo /token, em segundos.
ACCESS_TOKEN_TTL_SECONDS = int(os.environ.get("ACCESS_TOKEN_TTL_SECONDS", "3600"))

# --- M2.5: nonce endpoint (c_nonce para key proof, ver nonce_endpoint.py) ---

# TTL do c_nonce emitido pelo /nonce, em segundos. Curto de propósito: o
# c_nonce só precisa viver o suficiente para o wallet montar e assinar o
# JWT de proof-of-possession e enviar o credential request (M3) logo em
# seguida — não é um token de sessão de longa duração como o access_token.
C_NONCE_TTL_SECONDS = int(os.environ.get("C_NONCE_TTL_SECONDS", "300"))

# --- M3: credential endpoint (emissão de SD-JWT VC) ---

# Diretório deste arquivo (coop-a/), usado para resolver o path default da
# chave do issuer de forma independente do cwd em que o uvicorn é iniciado.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Path do arquivo PEM (PKCS8, sem senha) com o par de chaves Ed25519 do
# issuer coop-a, usado para assinar (JWS) os SD-JWT VC emitidos em
# POST /credential. NUNCA hardcoded no código — sempre via esta env var,
# para não acoplar o path físico da chave à lógica de assinatura (facilita
# trocar de chave/segredo por ambiente sem tocar em código). Ver keys.py:
# se o arquivo não existir neste path, um novo par é gerado e salvo aqui
# na primeira inicialização (decisão de MVP — ver docstring de keys.py).
# O diretório keys/ é git-ignorado (ver .gitignore na raiz do repo):
# a chave privada nunca é versionada, nem em ambiente de teste.
ISSUER_KEY_PATH = os.environ.get(
    "ISSUER_KEY_PATH", os.path.join(_THIS_DIR, "keys", "coop-a.pem")
)

# Validade (em segundos) da credencial emitida, usada para calcular o claim
# `exp` a partir do `iat` no momento da emissão. Um lote de mel rastreado
# não tem uma noção natural e óbvia de "expiração" da spec (diferente de,
# por ex., uma credencial de habilitação para dirigir) — usamos um valor
# arbitrário mas plausível de MVP (1 ano) só para popular o claim `exp`
# exigido pela spec SD-JWT VC como sempre-visível.
CREDENTIAL_VALIDITY_SECONDS = int(
    os.environ.get("CREDENTIAL_VALIDITY_SECONDS", str(365 * 24 * 3600))
)

# --- M4: leitura do Fuseki (SPARQL SELECT) para montar claims reais ---
#
# Mesmos nomes de env var que pipeline.py (M1.5) já usa para ESCREVER no
# Fuseki (INSERT DATA via /update) — aqui reaproveitados para LER (SELECT via
# /sparql), porque é o mesmo dataset "coop-a", a mesma instância Fuseki, só
# um endpoint HTTP diferente dentro dela. Não faz sentido introduzir nomes
# de env var paralelos (ex.: FUSEKI_QUERY_URL) para a mesma credencial/host.
# `127.0.0.1`, não `localhost`: `requests` resolve "localhost" tentando
# IPv6 primeiro, e o encaminhamento de porta do Docker Desktop nesta classe
# de ambiente de dev pode estar quebrado para IPv6 (conecta e reseta a
# meio da requisição) — `127.0.0.1` força IPv4 e evita isso. Achado real,
# não hipotético — ver docs/known-issues.md.
FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
FUSEKI_ADMIN_USER = os.environ.get("FUSEKI_ADMIN_USER", "admin")
FUSEKI_ADMIN_PASSWORD = os.environ.get("FUSEKI_ADMIN_PASSWORD")

# Batch number default usado quando o corpo de POST /credential não informa
# um `batch_number` explícito (extensão própria do MVP, ver
# credential_endpoint.py Parte 2). Mesmo valor literal que M3 tinha
# hardcoded como ÚNICO claim de lote — agora é só o fallback de qual lote
# buscar no Fuseki, não mais o próprio valor do claim.
DEFAULT_BATCH_NUMBER = os.environ.get("DEFAULT_BATCH_NUMBER", "MS-2026-042")
