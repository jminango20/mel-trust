"""
App FastAPI mínima do nó lab (laboratório de análise).

Escopo: só receber e guardar análises laboratoriais (POST /analise, ver
analise_endpoint.py), validadas via SHACL de forma/plausibilidade
(ontology/lab-analysis-shape.ttl) antes de entrar no Fuseki deste nó.

HISTÓRICO: este nó chegou a virar stateless (sem Fuseki, POST /avaliar) por
um turno — REVERTIDO. Voltou a ter Fuseki próprio, como era originalmente.
Ver docs/known-issues.md/CHANGELOG.md.

lab NÃO é issuer de credencial: sem metadata OID4VCI
(/.well-known/openid-credential-issuer), sem /token, /nonce, /credential, e
sem DID próprio (sem /.well-known/did.json). Quem consulta o dado gravado
aqui e o usa para compor uma credencial é o certifier, via SPARQL federado
(SERVICE contra o Fuseki deste nó) — fora do escopo deste arquivo.
"""
from fastapi import FastAPI

from analise_endpoint import router as analise_router
from config import NODE_NAME

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")
app.include_router(analise_router)
