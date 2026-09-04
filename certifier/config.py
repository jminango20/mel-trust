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
