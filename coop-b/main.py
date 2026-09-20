"""
App FastAPI mínima do nó coop-b (cooperativa produtora — mel nativo).

Escopo M1: metadata do issuer (OID4VCI), servido em
GET /.well-known/openid-credential-issuer.

Escopo M2: token endpoint (POST /token) do pre-authorized_code flow —
ver token_endpoint.py.

Escopo M2.5: nonce endpoint (POST /nonce) — ver nonce_endpoint.py. Emite o
c_nonce que o M3 vai exigir dentro do JWT de key proof do credential
request.

Escopo M3: credential endpoint (POST /credential) — ver
credential_endpoint.py. Emite o SD-JWT VC `HoneyBatchCredential` declarado
no metadata abaixo, com dados de lote FIXOS/hardcoded (M4 pluga Fuseki
depois). Ainda sem SHACL neste fluxo ou ancoragem — isso vem em milestones
futuros (M4+). Mesmo mecanismo de coop-a, claims diferentes (mel de Jataí,
não Apis — ver credential_endpoint.py).

Escopo M5: DID Document (GET /.well-known/did.json) — ver did_endpoint.py.
Mesmo mecanismo de coop-a.
"""
from fastapi import FastAPI

from config import BASE_URL, DID_WEB, NODE_NAME
from credential_endpoint import router as credential_router
from did_endpoint import router as did_router
from nonce_endpoint import router as nonce_router
from token_endpoint import router as token_router

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")
app.include_router(token_router)
app.include_router(nonce_router)
app.include_router(credential_router)
app.include_router(did_router)


@app.get("/.well-known/openid-credential-issuer")
def get_issuer_metadata() -> dict:
    """
    Metadata do issuer conforme OID4VCI (OpenID for Verifiable Credential
    Issuance), seção "Credential Issuer Metadata". Mesmo formato de
    coop-a/main.py (ver docstring lá para a justificativa completa de cada
    campo, incluindo a nota de confiança sobre `nonce_endpoint`).
    """
    return {
        "credential_issuer": DID_WEB,
        "credential_endpoint": f"{BASE_URL}/credential",
        "nonce_endpoint": f"{BASE_URL}/nonce",
        "credential_configurations_supported": {
            "HoneyBatchCredential": {
                "format": "dc+sd-jwt",
                "vct": "https://agritrust.example/vct/honey-batch",
                "display": [
                    {
                        "name": "Lote de Mel Rastreado",
                        "locale": "pt-BR",
                    }
                ],
            }
        },
    }
