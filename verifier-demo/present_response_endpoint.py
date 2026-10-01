"""
POST /present-response/{batch_number} — recebe e valida a VP (OID4VP) de
volta do holder, M9.7 (fecha o ciclo aberto por
`presentation_request_endpoint.py`, M9.6).

Formato recebido: `<Credential-JWT>~<Disclosure1>~...~<KB-JWT>` (mesmo
formato montado por `coop-a/lote_endpoint.py:apresentar_certificacao`).

Validações, nessa ordem (para no primeiro erro, cada um com um motivo
próprio — não um "invalid_presentation" genérico sem explicação):

1. Nonce: existe no store (`presentation_request_endpoint.get_request_by_nonce`),
   ainda não foi usado, e corresponde ao MESMO `batch_number` desta
   chamada. Marcado como USADO aqui — consumo definitivo, mesma decisão
   já tomada para o `c_nonce` dos emissores (quem CONSOME a prova é quem
   valida e marca como usado).
2. `sd_hash`: recalculado sobre `"<Credential-JWT>~<Disclosures>~"`
   recebidos (mesmo algoritmo de `coop-a/lote_endpoint.py:_compute_sd_hash`,
   duplicado aqui — mesmo padrão de duplicação entre processos já usado
   por `anchoring.py`/`hash_utils.py`), comparado com o `sd_hash` dentro
   do payload do KB-JWT. Se não bater: a apresentação foi adulterada ou
   remontada incorretamente.
3. Assinatura do KB-JWT: verificada contra `cnf.jwk` (extraído do payload
   do Credential-JWT, decodificado SEM verificar a assinatura do ISSUER —
   mesma simplificação de MVP já documentada para o proof JWT em
   `certifier/credential_endpoint.py`: quem assina o Credential-JWT é o
   certifier, confiado "fora de banda" neste MVP; o que ESTE endpoint
   verifica de verdade é que QUEM apresentou a VP possui a chave privada
   correspondente a `cnf.jwk`, não a cadeia de confiança completa do
   issuer). Confirma posse real da chave.

Se (2) ou (3) falharem: `onChainIntegrity.certificacao = "invalid_presentation"`
— DIFERENTE de `"not_verifiable_via_query"` (que significa "não dava pra
verificar por este meio", situação de antes de receber qualquer VP) e
diferente de `"mismatch"` (significa "verificamos e o dado não bate com o
ancorado", não "a prova em si é inválida").

4. Só se (1)-(3) passarem: recalcula o hash do Credential-JWT + Disclosures
   (a MESMA string usada no passo 2 pro `sd_hash`, hasheada agora com
   keccak256 em vez de SHA-256 — é exatamente o que
   `certifier/credential_endpoint.py:588` ancorou,
   `Web3.keccak(text=sdjwt.sd_jwt_issuance)`, confirmado antes de
   implementar este endpoint) e compara com `dataHashes[0]` lido via
   `onchain_verification.read_certification_data_hash`. Bateu →
   `"confirmed"`. Não bateu → `"mismatch"`.
"""
import base64
import hashlib
import logging

import jwt as pyjwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jwcrypto.jwk import JWK
from jwcrypto.jws import JWS
from web3 import Web3

from onchain_verification import read_certification_data_hash, verify_onchain_integrity
from presentation_request_endpoint import get_request_by_nonce, mark_nonce_used

logger = logging.getLogger(__name__)

router = APIRouter()


def _decode_unverified(jwt_str: str) -> dict:
    """Decodifica o payload de um JWT SEM verificar assinatura — mesma
    simplificação de MVP já usada em `certifier/credential_endpoint.py:_decode_proof_jwt`."""
    return pyjwt.decode(jwt_str, options={"verify_signature": False})


def _compute_sd_hash(credential_jwt: str, disclosures: list[str]) -> str:
    """Réplica EXATA de `coop-a/lote_endpoint.py:_compute_sd_hash` — ver
    docstring do módulo acima sobre por que é duplicada, não importada
    entre processos."""
    pre_kb = "~".join([credential_jwt, *disclosures, ""])
    digest = hashlib.sha256(pre_kb.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _resultado(batch_number: str, certificacao_status: str, motivo: str | None = None) -> dict:
    """Monta a resposta reaproveitando `verify_onchain_integrity` pra
    lote/laudo (mesma checagem on-chain já existente), sobrescrevendo só
    `certificacao` com o resultado REAL desta validação de VP."""
    integrity = verify_onchain_integrity(batch_number)
    integrity["certificacao"] = certificacao_status
    body = {"batchNumber": batch_number, "onChainIntegrity": integrity}
    if motivo is not None:
        body["certificacaoMotivo"] = motivo
    return body


@router.post("/present-response/{batch_number}")
async def receber_apresentacao(batch_number: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "invalid_request", "error_description": "Corpo não é JSON válido."})

    vp = body.get("vp") if isinstance(body, dict) else None
    if not vp or not isinstance(vp, str):
        return JSONResponse(status_code=400, content={"error": "invalid_request", "error_description": "Campo 'vp' obrigatório."})

    parts = vp.split("~")
    if len(parts) < 2:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "VP malformada: esperado pelo menos <Credential-JWT>~<KB-JWT>."},
        )
    credential_jwt = parts[0]
    kb_jwt = parts[-1]
    disclosures = parts[1:-1]

    # --- 1. Decodifica o KB-JWT (sem verificar assinatura ainda — só para
    # extrair nonce/sd_hash e decidir o que validar primeiro) ---
    try:
        kb_payload = _decode_unverified(kb_jwt)
    except Exception as exc:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", f"KB-JWT malformado: {exc}"))

    nonce = kb_payload.get("nonce")
    presentation_request = get_request_by_nonce(nonce) if nonce else None
    if presentation_request is None:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", "nonce desconhecido (nunca emitido, expirado, ou processo reiniciou)."))
    if presentation_request["usado"]:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", "nonce já utilizado em outra apresentação."))
    if presentation_request["batchNumber"] != batch_number:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", "nonce não corresponde a este batch_number."))

    # Consumo definitivo do nonce — a partir daqui, mesmo que as validações
    # seguintes falhem, este nonce específico nunca pode ser reaproveitado.
    mark_nonce_used(nonce)

    # --- 2. sd_hash ---
    expected_sd_hash = _compute_sd_hash(credential_jwt, disclosures)
    if kb_payload.get("sd_hash") != expected_sd_hash:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", "sd_hash do KB-JWT não bate com Credential-JWT + disclosures recebidos."))

    # --- 3. Assinatura do KB-JWT contra cnf.jwk da credencial ---
    try:
        credential_payload = _decode_unverified(credential_jwt)
    except Exception as exc:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", f"Credential-JWT malformado: {exc}"))

    cnf_jwk_dict = (credential_payload.get("cnf") or {}).get("jwk")
    if not cnf_jwk_dict:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", "cnf.jwk ausente no Credential-JWT."))

    try:
        holder_key = JWK(**cnf_jwk_dict)
        jws = JWS()
        jws.deserialize(kb_jwt)
        jws.verify(holder_key)
    except Exception as exc:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "invalid_presentation", f"assinatura do KB-JWT inválida: {type(exc).__name__}: {exc}"))

    # --- 4. Hash do Credential-JWT (+ disclosures, sem o KB-JWT) vs. dataHashes[] ancorado ---
    pre_kb = "~".join([credential_jwt, *disclosures, ""])
    recalculated_hash = bytes(Web3.keccak(text=pre_kb))

    try:
        exists, anchored_hash = read_certification_data_hash(batch_number)
    except Exception as exc:
        logger.error("present-response: falha ao ler AssetRegistry para %r: %s", batch_number, exc)
        return JSONResponse(status_code=200, content=_resultado(batch_number, "unavailable", f"falha ao consultar AssetRegistry: {exc}"))

    if not exists or anchored_hash is None:
        return JSONResponse(status_code=200, content=_resultado(batch_number, "not_anchored", "certificação não encontrada on-chain."))

    certificacao_status = "confirmed" if recalculated_hash == anchored_hash else "mismatch"
    return JSONResponse(status_code=200, content=_resultado(batch_number, certificacao_status))
