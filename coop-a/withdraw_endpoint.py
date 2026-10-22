"""
POST /producers/{producerDID}/withdraw — o coop-a saca, em nome do produtor, o rendimento dele no
VendaDados. O coop-a guarda a chave custodial do produtor (delivery.py); `sacar()` do contrato paga a
`msg.sender`, então o USDT vai para a CARTEIRA CUSTODIAL do produtor, não para a cooperativa. A
cooperativa só repõe o gás NATIVO da carteira, quando falta.

Fluxo (nunca usa `get_or_create_wallet`: este endpoint NÃO cria chave):
  1. `delivery.load_wallet_account(producerDID)`; sem arquivo de chave => 404 {"error": "not_found"}.
  2. Confere o chainId do node ANTES de assinar qualquer coisa (o node/rede errados viram 503).
  3. `saldoTotalSacavel(carteira)` (view do VendaDados: assentado + pendente); 0 => 200
     {"status": "nothing_to_withdraw"}, sem nenhuma transação (nem a de gás).
  4. Estima o gás de `sacar()`; se o saldo nativo da carteira for menor que
     `gás estimado x gas_price x 1,5`, a cooperativa envia `max(PRODUCER_GAS_TOPUP_WEI, falta)`
     (padrão 1000000000000000 wei = 0,001 ETH; só rede de teste). Senão não envia nada.
  5. A carteira do produtor assina `sacar()`, espera o recibo e responde 200
     {"status": "withdrawn", "amount", "txHash"}. `amount` é o que a carteira REALMENTE recebeu
     (diferença do saldo de USDT antes e depois), não o estimado.

Recibo do `sacar()` que não veio no prazo (RECEIPT_TIMEOUT_S, ver `delivery.receipt_wait`) DEPOIS de a transação
ter sido enviada => 202 {"status": "pending", "operation": "withdraw", "txHash"} (sem `amount`; não prova que
minerou). O timeout na reposição de gás continua 503: o `sacar()` não chega a ser enviado.

Erros: node fora, chainId errado, revert, recibo da reposição de gás em timeout, qualquer falha => 503
{"error": "service_unavailable"} com mensagem FIXA. O log registra só a etapa, o tipo da exceção e o
endereço (público) da carteira: nunca a chave, o caminho dela nem o texto da exceção.

Limite: um lock por processo serializa os saques (dois POST simultâneos do mesmo produtor não correm
juntos); não há lock entre processos.
"""
import logging
import os
import threading

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from web3 import Web3
from web3.exceptions import TimeExhausted

import delivery

logger = logging.getLogger(__name__)
router = APIRouter()

SERVICE_UNAVAILABLE_MSG = "saque indisponível no momento; tente novamente mais tarde"
DEFAULT_GAS_TOPUP_WEI = 1_000_000_000_000_000  # 0,001 ETH (redes de teste)
GAS_MARGIN = 1.5

_lock = threading.Lock()

_VENDA_ABI = [
    {"type": "function", "name": "saldoTotalSacavel", "stateMutability": "view",
     "inputs": [{"name": "conta", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "sacar", "stateMutability": "nonpayable", "inputs": [], "outputs": []},
    {"type": "function", "name": "usdt", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "address"}]},
]
_ERC20_ABI = [
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]


class _Falha(Exception):
    """Falha controlada: leva o nome da etapa (nunca dados sensíveis) para o log."""

    def __init__(self, etapa: str):
        super().__init__(etapa)
        self.etapa = etapa


def _error(status_code: int, error: str, description: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "error_description": description})


def _send(w3: Web3, account, tx: dict):
    """Assina com `account`, envia e espera o recibo (prazo e intervalo de `delivery.receipt_wait()`).
    Levanta `_Falha` se reverteu e `delivery.ReceiptPending(txHash)` se o web3 estourou o prazo (TimeExhausted)
    DEPOIS de a transação ter sido enviada; quem chama decide se isso vira 202 ou 503."""
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    timeout, poll = delivery.receipt_wait()
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout, poll_latency=poll)
    except TimeExhausted:  # o texto da exceção é descartado (pode trazer a URL do RPC)
        raise delivery.ReceiptPending("0x" + tx_hash.hex().removeprefix("0x")) from None
    if receipt.status != 1:
        raise _Falha("transacao_revertida")
    return receipt


def _topup_wei() -> int:
    return int(os.environ.get("PRODUCER_GAS_TOPUP_WEI", str(DEFAULT_GAS_TOPUP_WEI)))


def _withdraw(account) -> dict:
    rpc, chain_id, address = delivery.venda_dados_config()
    if not address:
        raise _Falha("config_sem_endereco")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
    got = w3.eth.chain_id  # levanta se o node estiver fora
    if got != chain_id:  # antes de assinar qualquer coisa
        raise _Falha("chain_id_diferente")
    venda = w3.eth.contract(address=Web3.to_checksum_address(address), abi=_VENDA_ABI)

    if venda.functions.saldoTotalSacavel(account.address).call() == 0:
        return {"status": "nothing_to_withdraw"}

    usdt = w3.eth.contract(address=venda.functions.usdt().call(), abi=_ERC20_ABI)
    usdt_antes = usdt.functions.balanceOf(account.address).call()

    gas_price = w3.eth.gas_price
    gas_estimado = venda.functions.sacar().estimate_gas({"from": account.address})
    necessario = int(gas_estimado * gas_price * GAS_MARGIN)
    saldo_nativo = w3.eth.get_balance(account.address)
    if saldo_nativo < necessario:
        valor = max(_topup_wei(), necessario - saldo_nativo)
        coop = delivery.load_coop_account()
        _send(w3, coop, {
            "to": account.address, "value": valor, "gas": 21000, "gasPrice": gas_price,
            "nonce": w3.eth.get_transaction_count(coop.address, "pending"), "chainId": chain_id,
        })

    tx = venda.functions.sacar().build_transaction({
        "from": account.address, "nonce": w3.eth.get_transaction_count(account.address, "pending"), "chainId": chain_id,
    })
    try:
        receipt = _send(w3, account, tx)
    except delivery.ReceiptPending as pending:  # sacar() enviado, recibo não veio: 202 (a reposição de gás NÃO passa por aqui)
        logger.warning("saque: recibo não veio no prazo (%s) (carteira %s)", delivery.error_type(pending), account.address)
        return JSONResponse(status_code=202, content={"status": "pending", "operation": "withdraw",
                                                       "txHash": pending.tx_hash})
    amount = usdt.functions.balanceOf(account.address).call() - usdt_antes
    return {"status": "withdrawn", "amount": amount, "txHash": "0x" + receipt.transactionHash.hex().removeprefix("0x")}


@router.post("/producers/{producer_did}/withdraw")
def withdraw(producer_did: str):
    account = delivery.load_wallet_account(producer_did)
    if account is None:
        return _error(404, "not_found", "produtor sem carteira custodial")
    try:
        with _lock:
            return _withdraw(account)
    except _Falha as exc:
        logger.error("saque: falha na etapa %s (carteira %s)", exc.etapa, account.address)
    except Exception as exc:  # noqa: BLE001 - node fora, revert, timeout...: 503 fixo; só o TIPO da exceção vai ao log
        logger.error("saque: falha (%s) (carteira %s)", delivery.error_type(exc), account.address)
    return _error(503, "service_unavailable", SERVICE_UNAVAILABLE_MSG)
