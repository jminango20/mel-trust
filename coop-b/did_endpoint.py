"""
DID Document endpoint (did:web) do coop-b — M5, Tarefa 1.

Mesmo mecanismo de coop-a/did_endpoint.py (ver docstring lá para a
justificativa completa de cada campo, do desvio de HTTP em dev, e da
escolha de JsonWebKey2020) — aqui só a instância muda: `DID_WEB` resolve
para `did:web:localhost%3A8002` (config.py) e a chave reaproveitada é a de
`coop-b/keys.py` (get_issuer_key()), a mesma que assina os SD-JWT VC
emitidos por `credential_endpoint.py` deste nó.
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
