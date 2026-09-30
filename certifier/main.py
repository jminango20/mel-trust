"""
App FastAPI mínima do nó certifier (certificador).

Escopo M1: metadata do issuer (OID4VCI), servido em
GET /.well-known/openid-credential-issuer.

Escopo M2: token endpoint (POST /token) do pre-authorized_code flow —
ver token_endpoint.py.

Escopo M2.5: nonce endpoint (POST /nonce) — ver nonce_endpoint.py.

Escopo M3: credential endpoint (POST /credential) — ver
credential_endpoint.py. Emite o SD-JWT VC `HoneyCertificationCredential`
(laudo laboratorial sobre um lote já produzido por uma cooperativa),
distinto de `HoneyBatchCredential` emitido por coop-a/coop-b.

CORREÇÃO nesta entrega: o metadata de M1 abaixo, herdado de quando os três
nós (coop-a, coop-b, certifier) foram criados a partir do mesmo template,
declarava erroneamente `HoneyBatchCredential` como o tipo suportado pelo
certifier — um bug de copy-paste do M1, nunca corrigido até agora porque o
certifier não tinha nenhuma lógica de emissão real por trás para expor essa
inconsistência. Corrigido para `HoneyCertificationCredential` (vct
`.../honey-certification`), que é o tipo que credential_endpoint.py
realmente implementa e valida via `CREDENTIAL_CONFIGURATION_ID`.

Escopo M5: DID Document (GET /.well-known/did.json) — ver did_endpoint.py.
Mesmo mecanismo de coop-a/coop-b.

Escopo M9 (holder da certificação): GET /lote/{batch_number}/pre-authorized-code
— ver pre_authorized_code_endpoint.py. Fecha o gap de código fixo/genérico
sem amarração a lote (ver docstring lá e
tests/features/holder_certificacao.feature).
"""
from fastapi import FastAPI

from config import BASE_URL, DID_WEB, NODE_NAME
from credential_endpoint import router as credential_router
from did_endpoint import router as did_router
from nonce_endpoint import router as nonce_router
from pre_authorized_code_endpoint import router as pre_authorized_code_router
from token_endpoint import router as token_router

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")
app.include_router(token_router)
app.include_router(nonce_router)
app.include_router(credential_router)
app.include_router(did_router)
app.include_router(pre_authorized_code_router)


@app.get("/.well-known/openid-credential-issuer")
def get_issuer_metadata() -> dict:
    """
    Metadata do issuer conforme OID4VCI (OpenID for Verifiable Credential
    Issuance), seção "Credential Issuer Metadata". Mesmo formato de
    coop-a/main.py (ver docstring lá para a justificativa completa de cada
    campo, incluindo a nota de confiança sobre `nonce_endpoint`) — a
    diferença é o tipo de credencial declarado em
    `credential_configurations_supported`, que agora reflete corretamente
    `HoneyCertificationCredential` (ver nota de correção no topo do
    arquivo).
    """
    return {
        "credential_issuer": DID_WEB,
        "credential_endpoint": f"{BASE_URL}/credential",
        "nonce_endpoint": f"{BASE_URL}/nonce",
        "credential_configurations_supported": {
            "HoneyCertificationCredential": {
                "format": "dc+sd-jwt",
                "vct": "https://agritrust.example/vct/honey-certification",
                "display": [
                    {
                        "name": "Certificação de Análise de Mel",
                        "locale": "pt-BR",
                    }
                ],
            }
        },
    }
