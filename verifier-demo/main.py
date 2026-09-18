"""
App FastAPI mínima do nó verifier-demo (auditor/comprador).

M1: endpoint de health check. Este nó não tem Fuseki próprio nem pipeline
SHACL de entrada — ele é um consumidor: consulta os outros nós via consulta
federada (ver verify_endpoint.py para a decisão entre `SERVICE` de verdade
e 3 queries orquestradas em Python).

M9.5 (esta entrega): `GET /verify/{batch_number}` — checagem ODRL de
propósito (header `X-Access-Purpose`) e validação SHACL da FORMA da
resposta agregada (`VerifierResponseShape`) já incluídas; checagem on-chain
(AssetRegistry.sol) ainda não (vem depois, em cima deste endpoint).
"""
from fastapi import FastAPI

from config import NODE_NAME
from verify_endpoint import router as verify_router

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")
app.include_router(verify_router)


@app.get("/health")
def get_health() -> dict:
    """Health check simples, para confirmar que o serviço está no ar."""
    return {"status": "ok", "node": NODE_NAME}
