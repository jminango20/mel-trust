"""
Token endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M2.

Nome do arquivo é `token_endpoint.py`, não `token.py`, de propósito:
`token` é nome de módulo da biblioteca padrão do Python (usado por
`tokenize`); um arquivo local `token.py` na raiz do projeto sombrearia
esse módulo stdlib para qualquer import feito a partir deste diretório,
o que pode quebrar bibliotecas que dependem dele internamente.

Escopo: só o pre-authorized_code grant (seção "Token Request" da spec
OID4VCI, que reaproveita RFC 6749 — OAuth 2.0 — para a mecânica de
token/erro). Nada de emissão de credencial aqui: esse endpoint só troca um
código por um access_token opaco. O credential endpoint (M3) é quem vai
exigir esse token no header Authorization para liberar a emissão.

Simplificação deliberada de MVP: o access_token emitido é armazenado em um
dict em memória do processo (_issued_tokens), não em Redis/DB/JWT assinado.
Reinicia o processo, perde todos os tokens emitidos — aceitável para MVP
local.

Validação do pre-authorized_code (2 caminhos, ver pre_auth_codes.py):
1. Código DINÂMICO, único por lote, gerado por POST /lote (pre_auth_codes.py)
   — uso único: consumido (marcado `usado=True`) na primeira troca por
   token; uma segunda tentativa com o MESMO código recebe `invalid_grant`.
2. Código FIXO legado (config.PRE_AUTHORIZED_CODE) — bypass de dev/teste,
   sempre aceito, nunca consumido. Necessário porque lotes inseridos fora de
   POST /lote (fixture .ttl via pipeline.py, ex. MS-2026-042) nunca passam
   pelo store dinâmico e não têm código próprio associado.

Parsing do corpo da requisição: o grant do OAuth2 (RFC 6749 §4.5) exige
Content-Type application/x-www-form-urlencoded. Em vez de usar
`Request.form()` do Starlette (que exige a dependência `python-multipart`
mesmo para corpos urlencoded, só para poder tratar também multipart/
form-data), fazemos o parse manual do corpo com urllib.parse — é
exatamente o formato que a spec exige aqui, então não perdemos nada em
corretude e evitamos adicionar uma dependência só para este endpoint.
"""
import time
import uuid
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config import ACCESS_TOKEN_TTL_SECONDS, PRE_AUTHORIZED_CODE
from pre_auth_codes import get_entry, mark_used

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
    Helper para uso futuro pelo credential endpoint (M3): confere se o
    access_token existe no store em memória e ainda não expirou.
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

    if pre_authorized_code == PRE_AUTHORIZED_CODE:
        # Bypass fixo de dev/teste — ver docstring do módulo. Nunca marcado
        # como usado, sempre aceito.
        pass
    else:
        entry = get_entry(pre_authorized_code)
        if entry is None:
            return _oauth_error(
                400,
                "invalid_grant",
                "pre-authorized_code inválido ou não reconhecido.",
            )
        if entry["usado"]:
            return _oauth_error(
                400,
                "invalid_grant",
                "pre-authorized_code já utilizado.",
            )
        mark_used(pre_authorized_code)

    access_token = str(uuid.uuid4())
    _issued_tokens[access_token] = {
        "expires_at": time.time() + ACCESS_TOKEN_TTL_SECONDS,
    }

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
    }
