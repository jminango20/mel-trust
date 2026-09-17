"""
App FastAPI mínima do nó verifier-demo (auditor/comprador).

M1: endpoint de health check. Este nó não tem Fuseki próprio nem pipeline
SHACL de entrada — ele é um consumidor: consulta os outros nós via consulta
federada (ver verify_endpoint.py para a decisão entre `SERVICE` de verdade
e 3 queries orquestradas em Python).

M9.5 (esta entrega): `GET /verify/{batch_number}` — primeira fatia do fluxo
de verificação, sem ODRL, sem validação SHACL da resposta e sem checagem
on-chain ainda (essas partes vêm depois, em cima deste endpoint).
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
