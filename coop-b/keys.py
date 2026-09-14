"""
Gestão da chave de assinatura do issuer coop-b — M3.

Réplica exata do mecanismo de coop-a/keys.py (ver docstring lá para a
justificativa completa de escolha de algoritmo/formato — não repetida aqui
palavra por palavra, mas o raciocínio é o mesmo): Ed25519 (JWA `EdDSA`),
armazenada como PEM (PKCS8, sem senha) em `coop-b/keys/coop-b.pem`, path
configurável via `ISSUER_KEY_PATH` (config.py), gerada e persistida
automaticamente na primeira inicialização se o arquivo não existir.

Cada nó (coop-a, coop-b, certifier) tem seu PRÓPRIO par de chaves — não faz
sentido compartilhar uma chave de assinatura entre issuers distintos, cada
DID (`did:web:coop-b.example` etc.) precisa corresponder a uma chave própria
para que a assinatura do SD-JWT seja, no futuro (M5), verificável contra o
`did:web` correto de cada emissor.
"""
import os

from jwcrypto.jwk import JWK

from config import ISSUER_KEY_PATH

_cached_key: JWK | None = None


def _generate_and_persist(path: str) -> JWK:
    key = JWK.generate(kty="OKP", crv="Ed25519")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pem = key.export_to_pem(private_key=True, password=None)
    with open(path, "wb") as f:
        f.write(pem)
    return key


def get_issuer_key() -> JWK:
    """
    Retorna a chave Ed25519 do issuer coop-b como um objeto `jwcrypto.jwk.JWK`
    (formato que a lib `sd_jwt.issuer.SDJWTIssuer` espera para o parâmetro
    `issuer_key`), carregando de `ISSUER_KEY_PATH` ou gerando+persistindo se
    o arquivo ainda não existir. Cacheada em processo após a primeira
    chamada — não relê o arquivo a cada requisição.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    path = ISSUER_KEY_PATH
    if os.path.isfile(path):
        with open(path, "rb") as f:
            _cached_key = JWK.from_pem(f.read())
    else:
        _cached_key = _generate_and_persist(path)

    return _cached_key
