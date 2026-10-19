"""
App FastAPI mínima do nó coop-a (cooperativa produtora).

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
futuros (M4+).

Escopo M5: DID Document (GET /.well-known/did.json) — ver did_endpoint.py.
Resolução real de did:web (o `credential_issuer`/`iss` abaixo deixa de ser
só um identificador opaco e passa a apontar para um documento de fato
resolvível, com a chave pública que assina as credenciais deste nó).

Ajuste pós-narrativa: POST /lote (ver lote_endpoint.py). Cadastra um lote via
HTTP com o mesmo gate SHACL de pipeline.py (M1.5), sem precisar editar
data/sample-instance.ttl à mão nem rodar o script via terminal. Não substitui
pipeline.py — é uma segunda porta de entrada para o mesmo gate
(shacl_pipeline.py).
"""
from fastapi import FastAPI

from config import BASE_URL, DID_WEB, NODE_NAME
from credential_endpoint import router as credential_router
from data_contract_endpoint import router as data_contract_router
from delivery import router as delivery_router
from did_endpoint import router as did_router
from lote_endpoint import router as lote_router
from nonce_endpoint import router as nonce_router
from token_endpoint import router as token_router
from withdraw_endpoint import router as withdraw_router

app = FastAPI(title=f"AgriTrust Mel — {NODE_NAME}")
app.include_router(token_router)
app.include_router(nonce_router)
app.include_router(credential_router)
app.include_router(did_router)
app.include_router(lote_router)
app.include_router(delivery_router)
app.include_router(data_contract_router)
app.include_router(withdraw_router)


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
