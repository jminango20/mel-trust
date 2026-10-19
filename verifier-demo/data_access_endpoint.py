"""
Acesso aos dados de um lote com DESAFIO ASSINADO — o comprador do Contrato de Dados prova que
controla a carteira do contrato (buyerWallet) e, pago o contrato, recebe o mesmo corpo do GET /verify.

  POST /data-access/challenge/{contractRef}
      Pergunta ao coop-a (GET /data-contract/{ref}/payment) se o contrato existe: 404 {"error":
      "not_found"} se não; 503 se o coop-a estiver fora. Senão emite {"nonce", "message"}. A mensagem
      contém o contractRef, o nonce e "verifier-demo" (`_message`). O nonce é de uso único, tem TTL
      (CHALLENGE_TTL_SECONDS, padrão 300 s) e fica amarrado ao contractRef.

  GET /data-access/{batch_number}   (X-Data-Contract, X-Challenge-Nonce, X-Challenge-Signature,
                                      X-Access-Purpose)
      Ordem das respostas:
        1. 401 invalid_challenge: cabeçalho ausente, nonce desconhecido/expirado/já usado/de outro
           contractRef, ou assinatura malformada. O nonce é CONSUMIDO na primeira apresentação, qualquer
           que seja o resultado depois dela (um desafio vale uma tentativa).
        2. Lê o contrato no coop-a (buyerWallet + status do pagamento): 404 se não existe, 503 se o
           coop-a estiver fora.
        3. 403 forbidden_wallet: a assinatura é válida (EIP-191), mas de uma carteira que não é o buyerWallet.
        4. 402 payment_required {"status"}: status diferente de "paid" e "released".
        5. Só então `verify_batch` (verify_endpoint.py), que mantém o 400/403 do propósito; os erros
           de corpo são outros (invalid_request / access_denied), distintos de forbidden_wallet.

Store do desafio: em memória do processo, mesmo padrão de presentation_request_endpoint.py
(`secrets.token_urlsafe`, dicionário por nonce); reinicia o processo, perde os desafios. Diferença:
este tem expiração (o presentation-request não tem). Entradas expiradas são varridas a cada novo desafio.

Logs: só a razão (código curto) e o tipo da exceção; nunca a assinatura, o nonce nem o texto da exceção.
Os 503 têm mensagem FIXA.
"""
import logging
import secrets
import threading
import time

import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from config import CHALLENGE_TTL_SECONDS, COOP_A_URL
from verify_endpoint import verify_batch

logger = logging.getLogger(__name__)
router = APIRouter()

SERVICE_UNAVAILABLE_MSG = "acesso indisponível no momento; tente novamente mais tarde"
INVALID_CHALLENGE_MSG = "desafio ausente, inválido, expirado ou já usado"
_ACCESS_STATUSES = ("paid", "released")

# nonce -> {"contractRef": str, "expiresAt": epoch}. Consumido (removido) na primeira apresentação.
_challenges: dict[str, dict] = {}
_challenges_lock = threading.Lock()


def _message(contract_ref: str, nonce: str) -> str:
    return f"verifier-demo: acesso aos dados do contrato {contract_ref} (nonce {nonce})"


def _error(status_code: int, error: str, description: str, extra: dict | None = None) -> JSONResponse:
    body = {"error": error, "error_description": description}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _unavailable() -> JSONResponse:
    return _error(503, "service_unavailable", SERVICE_UNAVAILABLE_MSG)


def _invalid(reason: str) -> JSONResponse:
    logger.info("data-access: desafio recusado (%s)", reason)
    return _error(401, "invalid_challenge", INVALID_CHALLENGE_MSG)


def _read_contract(contract_ref: str) -> dict | None:
    """GET /data-contract/{ref}/payment do coop-a: {"buyerWallet", "status", ...}, ou None se o coop-a
    não conhece o contrato (404). Levanta em coop-a fora do ar ou resposta inesperada."""
    resp = requests.get(f"{COOP_A_URL}/data-contract/{contract_ref}/payment", timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json()
    if "buyerWallet" not in body or "status" not in body:
        raise ValueError("resposta do coop-a sem buyerWallet/status")
    return body


def _consume(nonce: str, contract_ref: str) -> bool:
    """Consome o nonce (atômico). True só se existia, não expirou e é do contractRef informado."""
    with _challenges_lock:
        entry = _challenges.pop(nonce, None)
    return entry is not None and entry["contractRef"] == contract_ref and time.time() < entry["expiresAt"]


@router.post("/data-access/challenge/{contract_ref}")
def create_challenge(contract_ref: str):
    try:
        contract = _read_contract(contract_ref)
    except Exception as exc:  # noqa: BLE001 - coop-a fora/lento/resposta ruim: 503 fixo; só o TIPO vai ao log
        logger.error("data-access: leitura do contrato no coop-a falhou (%s)", type(exc).__name__)
        return _unavailable()
    if contract is None:
        return _error(404, "not_found", "contrato não encontrado")

    nonce = secrets.token_urlsafe(32)
    now = time.time()
    with _challenges_lock:
        for key in [k for k, v in _challenges.items() if v["expiresAt"] <= now]:
            del _challenges[key]
        _challenges[nonce] = {"contractRef": contract_ref, "expiresAt": now + CHALLENGE_TTL_SECONDS}
    return {"nonce": nonce, "message": _message(contract_ref, nonce)}


@router.get("/data-access/{batch_number}")
def data_access(
    batch_number: str,
    x_data_contract: str | None = Header(None),
    x_challenge_nonce: str | None = Header(None),
    x_challenge_signature: str | None = Header(None),
    x_access_purpose: str | None = Header(None),
):
    if not (x_data_contract and x_challenge_nonce and x_challenge_signature):
        return _invalid("cabecalho_ausente")
    if not _consume(x_challenge_nonce, x_data_contract):
        return _invalid("nonce_invalido")  # desconhecido, expirado, já usado ou de outro contractRef
    try:
        signer = Account.recover_message(
            encode_defunct(text=_message(x_data_contract, x_challenge_nonce)), signature=x_challenge_signature)
    except Exception as exc:  # noqa: BLE001 - assinatura malformada
        return _invalid(f"assinatura_malformada:{type(exc).__name__}")

    try:
        contract = _read_contract(x_data_contract)
    except Exception as exc:  # noqa: BLE001
        logger.error("data-access: leitura do contrato no coop-a falhou (%s)", type(exc).__name__)
        return _unavailable()
    if contract is None:
        return _error(404, "not_found", "contrato não encontrado")

    if signer.lower() != contract["buyerWallet"].lower():
        logger.info("data-access: assinatura de carteira diferente do buyerWallet")
        return _error(403, "forbidden_wallet", "a assinatura não é da carteira do comprador do contrato")
    if contract["status"] not in _ACCESS_STATUSES:
        return _error(402, "payment_required", "pagamento do contrato não confirmado", {"status": contract["status"]})

    return verify_batch(batch_number, x_access_purpose)
