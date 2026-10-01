"""
App FastAPI mínima do nó verifier-demo (auditor/comprador).

M1: endpoint de health check. Este nó não tem Fuseki próprio nem pipeline
SHACL de entrada — ele é um consumidor: consulta os outros nós via consulta
federada (ver verify_endpoint.py para a decisão entre `SERVICE` de verdade
e 3 queries orquestradas em Python).

M9.5: `GET /verify/{batch_number}` — checagem ODRL de propósito (header
`X-Access-Purpose`), validação SHACL da FORMA da resposta agregada
(`VerifierResponseShape`) e verificação de integridade on-chain
(`AssetRegistry.getAsset()`, ver `onchain_verification.py`).

M9.6 (esta entrega): `POST /presentation-request/{batch_number}` — primeiro
passo de OID4VP (criação da solicitação de apresentação, nonce único por
solicitação). Apresentação em si (holder enviando a VC de volta) ainda não
implementada — ver `presentation_request_endpoint.py`.
"""
from fastapi import FastAPI

from config import NODE_NAME
from presentation_request_endpoint import router as presentation_request_router
from verify_endpoint import router as verify_router

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")
app.include_router(verify_router)
app.include_router(presentation_request_router)


@app.get("/health")
def get_health() -> dict:
    """Health check simples, para confirmar que o serviço está no ar."""
    return {"status": "ok", "node": NODE_NAME}
