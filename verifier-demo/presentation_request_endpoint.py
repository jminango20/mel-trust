"""
POST /presentation-request/{batch_number} — primeiro passo de OID4VP
(apresentação de credencial pelo holder), M9.6.

Escopo desta entrega: só a CRIAÇÃO da solicitação de apresentação — gera um
`nonce` único (mesmo padrão/mecanismo de `c_nonce` já usado em
`<node>/nonce_endpoint.py` nos nós emissores: `secrets.token_urlsafe`,
store em memória do processo), vinculado ao `batch_number` E a um
identificador de requisição próprio (`request_id`), com flag `usado: False`.
A APRESENTAÇÃO em si (holder enviando a VC de volta, assinada contra esse
nonce) é passo futuro, fora deste escopo — ver
tests/features/presentation_request.feature.

Mesma simplificação de MVP de `nonce_endpoint.py`: store em memória
(`_issued_presentation_requests`), sem persistência — reinicia o processo,
perde as solicitações pendentes.

Por que um nonce por SOLICITAÇÃO (não um pool genérico de nonces como o
c_nonce dos emissores): o verifier aqui está pedindo uma apresentação de um
LOTE específico — o nonce precisa estar amarrado a esse contexto (qual
lote, qual solicitação), não ser um desafio de frescor genérico
reutilizável para qualquer apresentação.
"""
import secrets
import uuid

from fastapi import APIRouter

router = APIRouter()

# request_id -> {"batchNumber": str, "nonce": str, "usado": bool}
_issued_presentation_requests: dict[str, dict] = {}

# nonce -> request_id (lookup reverso, para uso futuro pelo endpoint de
# apresentação em si, que vai validar o nonce recebido e marcar `usado`).
_nonce_to_request_id: dict[str, str] = {}


def get_request_by_nonce(nonce: str) -> dict | None:
    """Helper para uso futuro pelo endpoint de apresentação (ainda não
    implementado): resolve a solicitação completa a partir do nonce."""
    request_id = _nonce_to_request_id.get(nonce)
    if request_id is None:
        return None
    return _issued_presentation_requests.get(request_id)


@router.post("/presentation-request/{batch_number}")
async def criar_presentation_request(batch_number: str) -> dict:
    request_id = str(uuid.uuid4())
    nonce = secrets.token_urlsafe(32)

    _issued_presentation_requests[request_id] = {
        "batchNumber": batch_number,
        "nonce": nonce,
        "usado": False,
    }
    _nonce_to_request_id[nonce] = request_id

    return {
        "requestId": request_id,
        "batchNumber": batch_number,
        "nonce": nonce,
        "used": False,
    }
