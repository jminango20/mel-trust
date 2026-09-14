"""
Token endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M2.

Réplica exata da lógica de coop-a/token_endpoint.py (ver docstring lá para
a justificativa completa) — apenas o pre-authorized_code, TTL e store de
tokens são próprios deste processo (coop-b). Mesmo motivo para o nome do
arquivo (`token_endpoint.py`, não `token.py`, para não sombrear o módulo
stdlib `token` usado por `tokenize`).

Escopo: só o pre-authorized_code grant (seção "Token Request" da spec
OID4VCI, reaproveitando RFC 6749 para a mecânica de token/erro).

Duas simplificações deliberadas de MVP, idênticas a coop-a:
1. O pre-authorized_code é um valor FIXO (config.PRE_AUTHORIZED_CODE), sem
   geração dinâmica via credential offer nem consumo de uso único.
2. O access_token é armazenado em um dict em memória do processo
   (_issued_tokens), não em Redis/DB/JWT assinado.
"""
import time
import uuid
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import ACCESS_TOKEN_TTL_SECONDS, PRE_AUTHORIZED_CODE

# Nome do grant type do pre-authorized_code flow, definido pela própria
# spec OID4VCI como um URN OAuth2 (não é um valor inventado aqui).
GRANT_TYPE_PRE_AUTHORIZED_CODE = "urn:ietf:params:oauth:grant-type:pre-authorized_code"

# Nome do parâmetro do corpo da requisição, conforme a spec: "pre-authorized_code"
# (com hífens, não "pre_authorized_code").
PARAM_PRE_AUTHORIZED_CODE = "pre-authorized_code"

# Store de tokens emitidos em memória: access_token -> {"expires_at": epoch}.
# MVP: sem persistência, sem revogação, sem escopo/claims associados ao
# token além do próprio fato de ele existir e não estar expirado.
_issued_tokens: dict[str, dict] = {}

router = APIRouter()


def is_valid_access_token(access_token: str) -> bool:
    """
    Helper usado pelo credential endpoint (M3): confere se o access_token
    existe no store em memória e ainda não expirou.
    """
    entry = _issued_tokens.get(access_token)
    if entry is None:
        return False
    return time.time() < entry["expires_at"]


def _oauth_error(status_code: int, error: str, description: str) -> JSONResponse:
    """Corpo de erro no formato RFC 6749 §5.2: {"error", "error_description"}."""
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
    )


@router.post("/token")
async def token_endpoint(request: Request):
    raw_body = await request.body()
    params = dict(parse_qsl(raw_body.decode("utf-8")))

    grant_type = params.get("grant_type")
    if grant_type != GRANT_TYPE_PRE_AUTHORIZED_CODE:
        return _oauth_error(
            400,
            "unsupported_grant_type",
            f"Este endpoint só suporta grant_type={GRANT_TYPE_PRE_AUTHORIZED_CODE}",
        )

    pre_authorized_code = params.get(PARAM_PRE_AUTHORIZED_CODE)
    if not pre_authorized_code:
        return _oauth_error(
            400,
            "invalid_request",
            f"Parâmetro obrigatório ausente: {PARAM_PRE_AUTHORIZED_CODE}",
        )

    if pre_authorized_code != PRE_AUTHORIZED_CODE:
        return _oauth_error(
            400,
            "invalid_grant",
            "pre-authorized_code inválido ou não reconhecido.",
        )

    access_token = str(uuid.uuid4())
    _issued_tokens[access_token] = {
        "expires_at": time.time() + ACCESS_TOKEN_TTL_SECONDS,
    }

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
    }
