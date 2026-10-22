"""
POST /data-contract/{contractRef}/release — o coop-a libera o pagamento de um Contrato de Dados:
`liberar(ref)` do VendaDados, assinado pela chave da cooperativa (delivery.load_coop_account).

Ordem (a decisão de "pode liberar?" é o `payment_status` de data_contract_endpoint, a MESMA da
leitura GET .../payment: aqui não há segunda precedência):
  1. contractRef mal formado ou sem contrato no Fuseki => 404 {"error": "not_found"}.
  2. Lê o depósito (`_read_deposit`) e calcula o status.
  3. "released" (comprador e valor certos)              => 200 {"status": "already_released"}, sem transação.
  4. qualquer status diferente de "paid"                => 409 {"error": "not_payable", "status": <status>}, sem transação.
  5. "paid": confere o chainId (`delivery._contract`) e SÓ ENTÃO a cooperativa assina `liberar(ref)`,
     espera o recibo e responde 200 {"status": "released", "txHash"}.

Erros: node fora, chainId errado, revert, recibo em timeout, Fuseki fora => 503 {"error":
"service_unavailable"} com mensagem FIXA. O log registra só a ref (pública) e o TIPO da exceção:
nunca a chave, o caminho dela nem o texto da exceção.

Concorrência: um lock por contractRef (por processo) serializa as liberações do mesmo contrato, e a
leitura do estado fica DENTRO do lock: a 2ª chamada vê "released" e não assina de novo. Não há lock
entre processos (nesse caso o contrato reverte a 2ª tx, que vira 503, sem perda de dinheiro).
"""
import logging
import threading

import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from web3 import Web3

import data_contract_endpoint as dc
import delivery
from withdraw_endpoint import _send

logger = logging.getLogger(__name__)
router = APIRouter()

SERVICE_UNAVAILABLE_MSG = "liberação indisponível no momento; tente novamente mais tarde"

_LIBERAR_ABI = [{"type": "function", "name": "liberar", "stateMutability": "nonpayable",
                 "inputs": [{"name": "ref", "type": "bytes32"}], "outputs": []}]

_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}  # um por contractRef já visto; pequeno, só refs com contrato


def _lock_for(ref: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(ref, threading.Lock())


def _error(status_code: int, error: str, description: str, extra: dict | None = None) -> JSONResponse:
    body = {"error": error, "error_description": description}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _liberar(ref: str, terms: dict) -> JSONResponse:
    comprador, valor, depositado_em, estado = dc._read_deposit(ref)
    status = dc.payment_status(estado, comprador, valor, depositado_em, terms)
    if status == "released":
        return JSONResponse(status_code=200, content={"status": "already_released"})
    if status != "paid":
        return _error(409, "not_payable", f"pagamento não liberável (status {status})", {"status": status})

    w3, _ = delivery._contract()  # confere o chainId (levanta se o node/rede estiver errado) ANTES de assinar
    address = delivery.venda_dados_config()[2]
    venda = w3.eth.contract(address=Web3.to_checksum_address(address), abi=_LIBERAR_ABI)
    coop = delivery.load_coop_account()
    tx = venda.functions.liberar(bytes.fromhex(ref[2:])).build_transaction(
        {"from": coop.address, "nonce": w3.eth.get_transaction_count(coop.address, "pending"), "chainId": w3.eth.chain_id})
    receipt = _send(w3, coop, tx)  # levanta se reverteu ou não minerou
    return JSONResponse(status_code=200, content={"status": "released",
                                                  "txHash": "0x" + receipt.transactionHash.hex().removeprefix("0x")})


@router.post("/data-contract/{contract_ref}/release")
def release(contract_ref: str):
    if not dc._CONTRACT_REF_RE.match(contract_ref):
        return _error(404, "not_found", "contrato não encontrado")
    try:
        with _lock_for(contract_ref):
            terms = dc._load_terms(contract_ref)
            if terms is None:
                return _error(404, "not_found", "contrato não encontrado")
            return _liberar(contract_ref, terms)
    except Exception as exc:  # noqa: BLE001 - Fuseki/node fora, chainId, revert, timeout: 503 fixo; só o TIPO vai ao log
        logger.error("liberação %s: falha (%s)", contract_ref, delivery.error_type(exc))
    return _error(503, "service_unavailable", SERVICE_UNAVAILABLE_MSG)
