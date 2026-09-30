"""
Configuração básica do nó lab (laboratório de análise).

Nó bem mais simples que coop-a/coop-b/certifier: não é issuer OID4VCI, não
tem DID próprio, não assina nada. Só recebe análises via POST /analise,
valida contra ontology/lab-analysis-shape.ttl (gate de forma/plausibilidade,
ver shacl_pipeline.py) e guarda no seu próprio Fuseki — quem consome esse
dado depois é o certifier, via SPARQL federado (fora do escopo deste nó).

HISTÓRICO: este nó chegou a virar stateless (sem Fuseki, POST /avaliar sem
persistência) por um turno, e foi REVERTIDO — voltou a ter Fuseki próprio e
POST /analise com persistência, como era originalmente. Ver
docs/known-issues.md/CHANGELOG.md.

Mesmo padrão de leitura de env var (python-dotenv + os.environ, sem
pydantic-settings) que os outros três nós já usam.
"""
import os

from dotenv import load_dotenv

# Carrega lab/.env se existir (não obrigatório em dev).
load_dotenv()

# Nome legível do nó, usado em metadata/logs.
NODE_NAME = os.environ.get("NODE_NAME", "lab")

# URL base pública do nó. Próxima porta livre depois de coop-a=8001,
# coop-b=8002 (?), certifier=8003 (?) e verifier-demo=8004.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8005")

# Endpoint do Fuseki (dataset "lab"). `127.0.0.1`, não `localhost`: mesmo
# achado de coop-a/coop-b/certifier/verifier-demo — `requests` resolve
# "localhost" tentando IPv6 primeiro, e o encaminhamento de porta do Docker
# Desktop nesta máquina está quebrado para IPv6 em portas publicadas (conecta
# e reseta a meio da requisição). `127.0.0.1` força IPv4 e evita isso — ver
# docs/known-issues.md.
FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://127.0.0.1:3033/lab")
FUSEKI_ADMIN_USER = os.environ.get("FUSEKI_ADMIN_USER", "admin")
FUSEKI_ADMIN_PASSWORD = os.environ.get("FUSEKI_ADMIN_PASSWORD")

# --- Federação SPARQL para resolver o productType do lote (ancoragem do
# laudo, M8/M9) ---
#
# POST /analise só recebe `batchNumber` — não o `productType`
# ("HoneyApis"/"HoneyNativeStinglessBee") — mas a ancoragem on-chain do
# laudo (`anchoring.py`) precisa dele como `natureId` do
# TransactionRequest, para casar com os processos `honey-lab-analysis`
# já registrados no ProcessRegistry por natureId (ver
# docs/referencia-contratos/README.md, seção 4).
#
# Mesmo padrão exato de `certifier/config.py`
# (COOP_A_SPARQL_SERVICE_URL/COOP_B_SPARQL_SERVICE_URL): estas URLs são
# resolvidas de DENTRO do container do Fuseki do LAB (que aqui atua como
# nó de entrada da query federada, mesmo papel que o Fuseki do certifier
# tem em credential_endpoint.py), via `host.docker.internal` — não
# `127.0.0.1` (essa troca só vale para conexão HOST -> porta publicada,
# contexto diferente de container -> host).
COOP_A_SPARQL_SERVICE_URL = os.environ.get(
    "COOP_A_SPARQL_SERVICE_URL", "http://host.docker.internal:3030/coop-a/sparql"
)
COOP_B_SPARQL_SERVICE_URL = os.environ.get(
    "COOP_B_SPARQL_SERVICE_URL", "http://host.docker.internal:3031/coop-b/sparql"
)
