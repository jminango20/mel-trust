"""
App FastAPI mínima do nó coop-b (cooperativa produtora).

Escopo M1: só o metadata do issuer (OID4VCI), servido em
GET /.well-known/openid-credential-issuer. Nada de emissão de credencial,
SD-JWT, SHACL ou ancoragem ainda — isso vem em milestones futuros.
"""
from fastapi import FastAPI

from config import BASE_URL, DID_WEB, NODE_NAME

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")


@app.get("/.well-known/openid-credential-issuer")
def get_issuer_metadata() -> dict:
    """
    Metadata do issuer conforme OID4VCI (OpenID for Verifiable Credential
    Issuance), seção "Credential Issuer Metadata".

    Campos:
    - issuer: identificador do emissor. Usamos o did:web placeholder do
      coop-b (resolução real do documento DID é M5).
    - credential_endpoint: onde o wallet faria o POST para pedir a
      credencial (ainda não implementado — placeholder de rota).
    - credential_configurations_supported: catálogo dos tipos de
      credencial que este nó pode emitir. Aqui só descrevemos o formato
      (SD-JWT VC) e o vocabulário (bee-extension.ttl), sem lógica de
      emissão real por trás.
    """
    return {
        "credential_issuer": DID_WEB,
        "credential_endpoint": f"{BASE_URL}/credential",
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
