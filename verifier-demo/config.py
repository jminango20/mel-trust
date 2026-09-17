"""
Configuração básica do nó verifier-demo.

Lê variáveis de ambiente (com defaults sensatos para dev local) via
python-dotenv + os.environ. Sem pydantic-settings — fora do escopo do M1.

Diferente de coop-a/coop-b/certifier, este nó não tem Fuseki próprio: ele
só consulta os SPARQL endpoints dos outros três nós via SPARQL federado
de verdade (SERVICE) — M9.5, `verify_endpoint.py`.

Duas famílias de URL, PERSPECTIVAS DIFERENTES de rede (ver
docs/notas-arquitetura/verifier-demo-service-federado.md para o porquê):
- `COOP_A_SPARQL_URL`: como o verifier-demo (rodando no HOST, fora de
  Docker) enxerga o Fuseki do coop-a — é o único endpoint que o
  verifier-demo chama diretamente; ele é o "nó de entrada" da consulta
  federada.
- `COOP_B_SPARQL_SERVICE_URL`/`CERTIFIER_SPARQL_SERVICE_URL`: como o
  FUSEKI DO COOP-A (rodando DENTRO de um container Docker) enxerga os
  outros dois Fuseki — usadas dentro das cláusulas `SERVICE <url> {...}`
  da query, executadas pelo container do coop-a, não pelo verifier-demo.
  `localhost` de dentro desse container aponta pro próprio container, não
  pro host onde coop-b/certifier publicam suas portas — por isso
  `host.docker.internal` (ver `extra_hosts` em cada docker-compose.yml).
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

# Endpoint SPARQL de ENTRADA da consulta federada — o único que o
# verifier-demo chama diretamente via HTTP. coop-a foi escolhido como nó
# de entrada fixo (decisão documentada em
# docs/notas-arquitetura/verifier-demo-service-federado.md) — não há
# razão de domínio para ser especificamente coop-a em vez de coop-b, só
# precisa ser um dos três, de forma consistente.
COOP_A_SPARQL_URL = os.environ.get(
    "COOP_A_SPARQL_URL", "http://localhost:3030/coop-a/sparql"
)

# URLs usadas DENTRO das cláusulas SERVICE da query (resolvidas pelo
# container do Fuseki do coop-a, não pelo verifier-demo) — ver docstring
# do módulo. `host.docker.internal` funciona nos 3 nós porque cada
# docker-compose.yml declara `extra_hosts: host.docker.internal:host-gateway`.
COOP_B_SPARQL_SERVICE_URL = os.environ.get(
    "COOP_B_SPARQL_SERVICE_URL", "http://host.docker.internal:3031/coop-b/sparql"
)
CERTIFIER_SPARQL_SERVICE_URL = os.environ.get(
    "CERTIFIER_SPARQL_SERVICE_URL", "http://host.docker.internal:3032/certifier/sparql"
)
