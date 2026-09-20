"""
DID Document endpoint (did:web) do coop-a — M5, Tarefa 1.

Escopo: `GET /.well-known/did.json`. Publica o documento DID que faz
`DID_WEB` (config.py, `did:web:localhost%3A8001`) ser efetivamente
RESOLVÍVEL, não só um identificador de string usado como valor do claim
`iss`/`credential_issuer`. Resolução did:web (draft do W3C CCG): o
resolvedor pega o identificador `did:web:<host>[:<path>...]`, substitui
`:` por `/` nos segmentos depois do host, percent-decodifica cada
segmento, e faz `GET https://<host>[/<path>...]/did.json` — quando não há
path (nosso caso: só host+porta), o document fica direto em
`/.well-known/did.json` (regra específica da spec did:web para esse caso,
não um path arbitrário nosso).

Por que reaproveita `get_issuer_key()` (keys.py) em vez de gerar uma chave
nova: a CHAVE PÚBLICA publicada aqui precisa ser exatamente a mesma que
assina os SD-JWT VC em `credential_endpoint.py` (claim `iss` = DID_WEB) —
senão o DID Document publicado seria inconsistente com as credenciais
reais emitidas por este nó, e nenhum verificador conseguiria checar a
assinatura de uma credencial de verdade contra a chave pública do DID
Document. Não há geração de chave nova nem endpoint de rotação aqui — só
exposição em formato de documento.

Formato: DID Core (https://www.w3.org/TR/did-core/) + JSON Web Key 2020
(https://w3c-ccg.github.io/lds-jws2020/) para o verificationMethod.
JsonWebKey2020 foi escolhido em vez de, por exemplo, converter a chave
para multibase (formato usado por did:key, ver scripts/generate_did_key.py)
porque `jwcrypto.jwk.JWK.export_public(as_dict=True)` já retorna a chave
pública em JWK nativamente ({"kty": "OKP", "crv": "Ed25519", "x": "..."},
formato definido por RFC 8037 para curvas Ed25519/X25519) — não precisamos
de nenhuma conversão/dependência extra para publicar `publicKeyJwk`.

`@context` inclui `https://w3id.org/security/suites/jws-2020/v1` porque é
o context que define o termo `JsonWebKey2020` usado em `verificationMethod`
— sem ele, um processador JSON-LD estrito não saberia resolver esse termo.
`authentication` e `assertionMethod` referenciam o verification method
PELO ID (string), não embutido — forma padrão de "verification
relationship by reference" da spec DID Core, usada aqui porque o mesmo
par de chaves serve tanto para autenticação quanto para assinar
declarações (assertions, como uma VC) neste MVP; não há necessidade de
chaves separadas por propósito.

DESVIO DE AMBIENTE, deliberado (mesmo padrão de documentação usado para
`host.docker.internal` em docs/notas-arquitetura/verifier-demo-service-federado.md):
a spec did:web assume resolução via HTTPS por padrão (a URL construída a
partir do identificador é sempre `https://`). Este projeto roda em HTTP
puro em dev local (sem TLS configurado — configurar TLS local é fora do
escopo do MVP, ver CLAUDE.md/roadmap). `BASE_URL` (config.py) usa
`http://localhost:8001`, e é isso que qualquer chamador real usaria para
buscar este documento neste ambiente — não uma leitura estrita da spec
did:web (que technically exigiria HTTPS), só uma concessão de ambiente de
desenvolvimento.
"""
from fastapi import APIRouter

from config import DID_WEB
from keys import get_issuer_key

router = APIRouter()


@router.get("/.well-known/did.json")
def get_did_document() -> dict:
    """
    Monta o DID Document a partir da MESMA chave Ed25519 usada para
    assinar SD-JWT VC (get_issuer_key(), keys.py) — nunca gera uma chave
    separada aqui.
    """
    key = get_issuer_key()
    public_jwk = key.export_public(as_dict=True)  # {"kty", "crv", "x"}

    verification_method_id = f"{DID_WEB}#key-1"

    return {
        "@context": [
            "https://www.w3.org/ns/did/v1",
            "https://w3id.org/security/suites/jws-2020/v1",
        ],
        "id": DID_WEB,
        "verificationMethod": [
            {
                "id": verification_method_id,
                "type": "JsonWebKey2020",
                "controller": DID_WEB,
                "publicKeyJwk": public_jwk,
            }
        ],
        "authentication": [verification_method_id],
        "assertionMethod": [verification_method_id],
    }
