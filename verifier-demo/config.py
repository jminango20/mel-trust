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
# `127.0.0.1`, não `localhost`: `requests` (chamado pelo verifier-demo, no
# HOST) resolve "localhost" tentando IPv6 primeiro, e o encaminhamento de
# porta do Docker Desktop nesta classe de ambiente de dev pode estar
# quebrado para IPv6 (conecta e reseta a meio da requisição) — `127.0.0.1`
# força IPv4 e evita isso. Achado real, ver docs/known-issues.md. NÃO
# aplicável aos dois `_SERVICE_URL` abaixo: aqueles resolvem de DENTRO do
# container do coop-a, onde `127.0.0.1` apontaria para o próprio
# container, não para o host — por isso continuam em `host.docker.internal`.
COOP_A_SPARQL_URL = os.environ.get(
    "COOP_A_SPARQL_URL", "http://127.0.0.1:3030/coop-a/sparql"
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


# --- Rede Semear (Besu): RPC, chainId, AddressDiscovery e canal (leitura on-chain) ---
# Lidos por onchain_verification.py. Variáveis com prefixo BESU_; os DEFAULTS são os
# valores confirmados em docs/referencia-contratos/README.md — sem a variável, nada
# muda. O verifier só LÊ (getAsset): não usa o TransactionOrchestrator.
RPC_URL = os.environ.get("BESU_RPC_URL", "http://127.0.0.1:8545")
EXPECTED_CHAIN_ID = int(os.environ.get("BESU_CHAIN_ID", "648"))
ADDRESS_DISCOVERY_ADDRESS = os.environ.get(
    "BESU_ADDRESS_DISCOVERY_ADDRESS", "0xB7f8BC63BbcaD18155201308C8f3540b07f84F5e"
)
CHANNEL_NAME = os.environ.get("BESU_CHANNEL_NAME", "mel-trust")


# --- Acesso aos dados com desafio assinado (data_access_endpoint.py) ---
# coop-a: de onde o verifier-demo lê buyerWallet e status do pagamento (GET /data-contract/{ref}/payment).
COOP_A_URL = os.environ.get("COOP_A_URL", "http://127.0.0.1:8001")
# Validade do desafio (nonce), em segundos.
CHALLENGE_TTL_SECONDS = int(os.environ.get("CHALLENGE_TTL_SECONDS", "300"))
