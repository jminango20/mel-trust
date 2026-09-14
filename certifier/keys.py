"""
Gestão da chave de assinatura do issuer certifier — M3.

Réplica exata do mecanismo de coop-a/keys.py (ver docstring lá para a
justificativa completa de escolha de algoritmo/formato): Ed25519 (JWA
`EdDSA`), armazenada como PEM (PKCS8, sem senha) em
`certifier/keys/certifier.pem`, path configurável via `ISSUER_KEY_PATH`
(config.py), gerada e persistida automaticamente na primeira inicialização
se o arquivo não existir.

O certifier tem seu PRÓPRIO par de chaves, distinto de coop-a/coop-b: é um
issuer diferente (`did:web:certifier.example`), assinando um tipo de
credencial diferente (`HoneyCertificationCredential`, não
`HoneyBatchCredential`) — cada DID precisa corresponder à sua própria chave
para que a assinatura seja, no futuro (M5), verificável contra o emissor
correto.
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
    Retorna a chave Ed25519 do issuer certifier como um objeto
    `jwcrypto.jwk.JWK` (formato que a lib `sd_jwt.issuer.SDJWTIssuer` espera
    para o parâmetro `issuer_key`), carregando de `ISSUER_KEY_PATH` ou
    gerando+persistindo se o arquivo ainda não existir. Cacheada em
    processo após a primeira chamada.
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
