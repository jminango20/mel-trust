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

# DID do certifier. PLACEHOLDER — a resolução real de did:web (documento DID
# publicado em /.well-known/did.json, chaves, etc.) é escopo do M5.
# Por ora só serve para preencher o campo "issuer" do metadata OID4VCI.
DID_WEB = os.environ.get("DID_WEB", "did:web:certifier.example")

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
