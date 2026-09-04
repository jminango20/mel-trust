"""
App FastAPI mínima do nó verifier-demo (auditor/comprador).

Escopo M1: só um endpoint de health check. Este nó não tem Fuseki próprio
nem pipeline SHACL de entrada — ele é um consumidor: consulta os outros
nós via SPARQL federado (SERVICE), checa propósito ODRL e valida a
resposta contra o AssetRegistry.sol. Esse fluxo é escopo de M9.5+, ainda
não implementado aqui.
"""
from fastapi import FastAPI

from config import NODE_NAME

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")


@app.get("/health")
def get_health() -> dict:
    """Health check simples, para confirmar que o serviço está no ar."""
    return {"status": "ok", "node": NODE_NAME}
