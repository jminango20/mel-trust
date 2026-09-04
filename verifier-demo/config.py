"""
Configuração básica do nó verifier-demo.

Lê variáveis de ambiente (com defaults sensatos para dev local) via
python-dotenv + os.environ. Sem pydantic-settings — fora do escopo do M1.

Diferente de coop-a/coop-b/certifier, este nó não tem Fuseki próprio: ele
só consulta os SPARQL endpoints dos outros três nós (SPARQL federado, via
SERVICE) — implementado em M9.5. Por ora as URLs abaixo são só a
configuração dos endpoints remotos que serão usados depois.
"""
import os

from dotenv import load_dotenv

# Carrega verifier-demo/.env se existir (não obrigatório em dev).
load_dotenv()

# Nome legível do nó, usado em metadata/logs.
NODE_NAME = os.environ.get("NODE_NAME", "verifier-demo")

# URL base pública do nó. Próxima porta livre depois de coop-a (8001),
# coop-b (8002) e certifier (8003).
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8004")

# Endpoints SPARQL dos três nós de dados, usados pela consulta federada
# (SERVICE) implementada em M9.5. Defaults coerentes com o
# docker-compose.yml de cada nó (Fuseki na porta 3030/3031/3032,
# dataset com o mesmo nome do nó).
COOP_A_SPARQL_URL = os.environ.get(
    "COOP_A_SPARQL_URL", "http://localhost:3030/coop-a/sparql"
)
COOP_B_SPARQL_URL = os.environ.get(
    "COOP_B_SPARQL_URL", "http://localhost:3031/coop-b/sparql"
)
CERTIFIER_SPARQL_URL = os.environ.get(
    "CERTIFIER_SPARQL_URL", "http://localhost:3032/certifier/sparql"
)
