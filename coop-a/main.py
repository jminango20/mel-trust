"""
App FastAPI mínima do nó coop-a (cooperativa produtora).

Escopo M1: metadata do issuer (OID4VCI), servido em
GET /.well-known/openid-credential-issuer.

Escopo M2: token endpoint (POST /token) do pre-authorized_code flow —
ver token_endpoint.py.

Escopo M2.5: nonce endpoint (POST /nonce) — ver nonce_endpoint.py. Emite o
c_nonce que o M3 vai exigir dentro do JWT de key proof do credential
request. Ainda sem emissão de credencial real (SD-JWT), SHACL neste fluxo
ou ancoragem — isso vem em milestones futuros (M3+).
"""
from fastapi import FastAPI

from config import BASE_URL, DID_WEB, NODE_NAME
from nonce_endpoint import router as nonce_router
from token_endpoint import router as token_router

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")
app.include_router(token_router)
app.include_router(nonce_router)


@app.get("/.well-known/openid-credential-issuer")
def get_issuer_metadata() -> dict:
    """
    Metadata do issuer conforme OID4VCI (OpenID for Verifiable Credential
    Issuance), seção "Credential Issuer Metadata".

    Campos:
    - issuer: identificador do emissor. Usamos o did:web placeholder do
      coop-a (resolução real do documento DID é M5).
    - credential_endpoint: onde o wallet faria o POST para pedir a
      credencial (ainda não implementado — placeholder de rota).
    - nonce_endpoint: onde o wallet obtém o c_nonce (POST /nonce, M2.5)
      usado para montar o JWT de key proof exigido pelo credential
      endpoint (M3). Estou razoavelmente confiante de que a versão final
      da spec OID4VCI define este parâmetro de metadata com o nome exato
      "nonce_endpoint" (introduzido junto com o Nonce Endpoint dedicado,
      substituindo o esquema anterior de c_nonce embutido na resposta do
      token/credential endpoint) — mas não tenho certeza absoluta se ele é
      classificado como OBRIGATÓRIO ou OPCIONAL no metadata quando o
      issuer suporta esse mecanismo. Declarar o campo aqui é seguro em
      qualquer um dos dois casos (um campo opcional presente não quebra
      nada); se a obrigatoriedade for relevante para conformidade formal,
      vale confirmar contra o texto oficial da spec antes de depender
      disso.
    - credential_configurations_supported: catálogo dos tipos de
      credencial que este nó pode emitir. Aqui só descrevemos o formato
      (SD-JWT VC) e o vocabulário (bee-extension.ttl), sem lógica de
      emissão real por trás.
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
