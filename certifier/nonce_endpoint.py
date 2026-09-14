"""
Nonce endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M2.5.

Réplica exata da lógica de coop-a/nonce_endpoint.py (ver docstring lá para a
justificativa completa do papel do c_nonce vs. access_token, e a nota de
confiança sobre a spec quanto ao Nonce Endpoint dedicado). Aqui só o store
de nonces é próprio deste processo (certifier) — o mecanismo é idêntico.

Duas simplificações deliberadas de MVP, idênticas a coop-a:
1. O `c_nonce` é armazenado em um dict em memória do processo
   (_issued_nonces), não em Redis/DB.
2. O c_nonce é tratado como uso único (single-use): `is_valid_c_nonce` já
   CONSOME o nonce por padrão.
"""
import secrets
import time

from fastapi import APIRouter

from config import C_NONCE_TTL_SECONDS

# Store de nonces emitidos em memória: c_nonce -> {"expires_at": epoch}.
# MVP: sem persistência, sem associação a um holder/sessão específica.
_issued_nonces: dict[str, dict] = {}

router = APIRouter()


def is_valid_c_nonce(c_nonce: str, consume: bool = True) -> bool:
    """
    Helper usado pelo credential endpoint (M3): confere se o c_nonce
    (extraído do claim `nonce` dentro do JWT de key proof) foi realmente
    emitido por este endpoint e ainda não expirou.

    `consume=True` (default): trata o nonce como uso único, removendo-o do
    store após uma validação bem-sucedida.
    """
    entry = _issued_nonces.get(c_nonce)
    if entry is None:
        return False
    valid = time.time() < entry["expires_at"]
    if consume:
        _issued_nonces.pop(c_nonce, None)
    return valid


@router.post("/nonce")
async def nonce_endpoint():
    """
    Sem corpo de requisição, sem autenticação — o nonce endpoint é
    deliberadamente público: ele só entrega um desafio opaco, não libera
    nenhum dado nem emite credencial.
    """
    c_nonce = secrets.token_urlsafe(32)
    _issued_nonces[c_nonce] = {
        "expires_at": time.time() + C_NONCE_TTL_SECONDS,
    }

    return {"c_nonce": c_nonce}
