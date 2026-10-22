"""
Registro da entrega no contrato VendaDados (segunda rede, separada da Besu).

Quando um lote tem quantidade, a cooperativa chama
`VendaDados.registrarEntrega(produtor, batchRef, mililitros)`: o contrato cunha
1 cota por mL para a carteira do produtor.

DECISÕES (ver tests/step_defs/test_venda_dados_entrega.py):
- batchRef = keccak256(batchNumber), o mesmo bytes32 que a Besu usa como assetId.
- Carteira custodial por producerDID: chave secp256k1 em
  keys/producers/<sha256(producerDID) em hex>.key, criada no primeiro uso e
  REUTILIZADA depois. Pasta gitignored. Só redes de TESTE por enquanto.
- A marca de entrega fica num recurso SEPARADO no Fuseki, como a AnchoringTask:
  `:DeliveryTask_<slug(batchNumber)>` com `:deliveryBatch` e `:deliveryStatus`
  (pending | registered | mismatch). Nada disso entra no hash do lote.
- Antes de enviar, SEMPRE consulta `entregas(batchRef)`: se já existe com o mesmo
  produtor e os mesmos mL, só marca registered (recibo perdido); se existe com
  dados DIFERENTES, mismatch (nunca registered, reexaminado a cada retry).
- Config (env): VENDA_DADOS_RPC_URL (default http://127.0.0.1:8546),
  VENDA_DADOS_ADDRESS (senão contracts/venda-dados/deployments/local.json),
  VENDA_DADOS_CHAIN_ID (default 31337). O chainId é conferido ANTES de assinar.
  Chave da cooperativa: VENDA_DADOS_COOP_KEY_PATH (própria). Sem ela, a chave da Besu
  (BESU_KEY_PATH) só vale no chainId 31337 (rede de teste local, onde a cooperativa
  reaproveita a chave da Besu); em outro chainId `load_coop_account` recusa, e também
  recusa se a chave própria for a da Besu (mesmo arquivo ou mesmo endereço).
"""
import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path

import requests
from eth_account import Account
from fastapi import APIRouter
from web3 import Web3

from config import FUSEKI_ADMIN_PASSWORD, FUSEKI_ADMIN_USER, FUSEKI_URL

logger = logging.getLogger(__name__)
router = APIRouter()

BEE = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"
_PREFIXES = f"PREFIX : <{BEE}>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

_THIS_DIR = Path(__file__).resolve().parent
PRODUCERS_KEY_DIR = Path(os.environ.get("PRODUCERS_KEY_DIR", _THIS_DIR / "keys" / "producers"))
_DEFAULT_BESU_KEY_PATH = _THIS_DIR / "keys" / "coop-a-besu.key"
COOP_KEY_VAR = "VENDA_DADOS_COOP_KEY_PATH"
BESU_KEY_VAR = "BESU_KEY_PATH"
LOCAL_TEST_CHAIN_ID = 31337  # única rede em que a chave da Besu pode servir de chave da cooperativa
_DEPLOY_JSON = _THIS_DIR.parent / "contracts" / "venda-dados" / "deployments" / "local.json"

DEFAULT_RECEIPT_TIMEOUT_S = 60.0
DEFAULT_RECEIPT_POLL_S = 0.1  # o padrão do web3 (poll_latency)

# Mensagem fixa nas respostas HTTP: o texto da exceção (URL do RPC, caminhos, etc.) só vai ao log.
DELIVERY_ERROR_MSG = "entrega pendente: rede fora do ar ou transação recusada"

_ABI = [
    {"type": "function", "name": "entregas", "stateMutability": "view",
     "inputs": [{"name": "", "type": "bytes32"}],
     "outputs": [{"name": "produtor", "type": "address"}, {"name": "mililitros", "type": "uint256"}]},
    {"type": "function", "name": "registrarEntrega", "stateMutability": "nonpayable",
     "inputs": [{"name": "produtor", "type": "address"}, {"name": "batchRef", "type": "bytes32"},
                {"name": "mililitros", "type": "uint256"}], "outputs": []},
]


# --- Configuração (lida a cada chamada: o node/deploy pode mudar sem reiniciar o nó) ---

def venda_dados_config() -> tuple[str, int, str | None]:
    rpc = os.environ.get("VENDA_DADOS_RPC_URL", "http://127.0.0.1:8546")
    chain_id = int(os.environ.get("VENDA_DADOS_CHAIN_ID", "31337"))
    address = os.environ.get("VENDA_DADOS_ADDRESS")
    if not address and _DEPLOY_JSON.exists():
        address = json.loads(_DEPLOY_JSON.read_text(encoding="utf-8")).get("vendaDados")
    return rpc, chain_id, address


def _positive_float(name: str) -> float | None:
    """Valor da variável de ambiente como float finito e > 0; ausente, texto, zero, negativo ou nan/inf => None."""
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def receipt_wait() -> tuple[float, float]:
    """(prazo, intervalo) em segundos da espera do recibo, de RECEIPT_TIMEOUT_S e RECEIPT_POLL_S, lidos A CADA
    chamada. Padrão 60 e 0.1. Valor inválido (texto, zero, negativo) cai no padrão; intervalo maior que o prazo
    é incoerente e derruba os DOIS para o padrão."""
    timeout = _positive_float("RECEIPT_TIMEOUT_S") or DEFAULT_RECEIPT_TIMEOUT_S
    poll = _positive_float("RECEIPT_POLL_S") or DEFAULT_RECEIPT_POLL_S
    if poll > timeout:
        return DEFAULT_RECEIPT_TIMEOUT_S, DEFAULT_RECEIPT_POLL_S
    return timeout, poll


class ReceiptPending(RuntimeError):
    """A transação FOI enviada, mas o recibo não veio no prazo (web3 TimeExhausted). Carrega o txHash. Não prova
    que a transação minerou: ela pode ainda entrar, ser substituída ou ser descartada. `cause_type` é o tipo
    que o log registra (`error_type`); o texto da exceção original (que pode trazer a URL) é descartado."""
    cause_type = "TimeExhausted"

    def __init__(self, tx_hash: str):
        super().__init__("recibo não veio no prazo")
        self.tx_hash = tx_hash


class RpcUnavailable(RuntimeError):
    """RPC do VendaDados inalcançável. O texto da exceção da biblioteca (requests/web3) traz a URL do RPC,
    que numa rede pública pode levar chave de API; aqui `str()` traz só o TIPO da falha original."""

    def __init__(self, cause_type: str):
        super().__init__(f"RPC do VendaDados indisponível ({cause_type})")
        self.cause_type = cause_type


def error_type(exc: BaseException) -> str:
    """Nome do tipo para o log: o da falha original quando o RPC estava inalcançável, senão o da exceção."""
    return getattr(exc, "cause_type", None) or type(exc).__name__


def checked_web3(rpc: str, chain_id: int) -> Web3:
    """Web3 ligado ao RPC do VendaDados, com o chainId conferido (antes de qualquer assinatura).
    As mensagens de erro nunca levam o RPC (URL, host, chave): só o tipo da falha e os chainIds."""
    try:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
        got = w3.eth.chain_id  # levanta se o node estiver fora
    except Exception as exc:  # noqa: BLE001 - o texto da exceção carrega a URL: fica só o tipo
        raise RpcUnavailable(type(exc).__name__) from None
    if got != chain_id:
        raise RuntimeError(f"chainId {got} diferente do esperado {chain_id} no RPC do VendaDados")
    return w3


def _contract():
    rpc, chain_id, address = venda_dados_config()
    if not address:
        raise RuntimeError("VENDA_DADOS_ADDRESS não configurado (nem deployments/local.json)")
    w3 = checked_web3(rpc, chain_id)
    return w3, w3.eth.contract(address=Web3.to_checksum_address(address), abi=_ABI)


# --- Carteira custodial do produtor ---

def _key_path(producer_did: str) -> Path:
    return PRODUCERS_KEY_DIR / (hashlib.sha256(producer_did.encode("utf-8")).hexdigest() + ".key")


class CoopKeyError(RuntimeError):
    """Chave da cooperativa recusada ou inutilizável. A mensagem cita só o NOME da variável de
    ambiente: nunca o caminho do arquivo, o conteúdo da chave nem o endereço."""


def _read_key_account(var: str, path: str):
    """Conta da chave no arquivo `path` (vindo da variável `var`). Arquivo inexistente, ilegível e
    conteúdo inválido dão o MESMO erro; o motivo original fica só na causa interna, que a mensagem
    (e `raise ... from None`) não expõe."""
    try:
        return Account.from_key("0x" + Path(path).read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001 - qualquer falha de leitura/parse vira a mesma mensagem fixa
        raise CoopKeyError(f"chave da cooperativa ausente, ilegível ou inválida ({var})") from None


def load_coop_account():
    """Conta (eth_account) da cooperativa no VendaDados. Só LÊ; o ambiente é lido a CADA chamada.

    - `VENDA_DADOS_COOP_KEY_PATH` definida: vale a chave dela. Fora do chainId 31337 ela tem de ser
      SEPARADA da chave da Besu (`BESU_KEY_PATH`): mesmo arquivo, ou chaves que derivam o mesmo
      endereço, são recusados.
    - Não definida: cai na chave da Besu SÓ no chainId 31337 (rede de teste local, como antes);
      em qualquer outro chainId recusa, para a chave da Besu nunca assinar numa rede pública.
    """
    chain_id = venda_dados_config()[1]
    own = os.environ.get(COOP_KEY_VAR)
    besu = os.environ.get(BESU_KEY_VAR) or str(_DEFAULT_BESU_KEY_PATH)

    if not own:
        if chain_id != LOCAL_TEST_CHAIN_ID:
            raise CoopKeyError(f"chainId {chain_id} exige uma chave própria da cooperativa em {COOP_KEY_VAR}; "
                               f"a chave da Besu ({BESU_KEY_VAR}) só vale no chainId {LOCAL_TEST_CHAIN_ID}")
        return _read_key_account(BESU_KEY_VAR, besu)

    if chain_id == LOCAL_TEST_CHAIN_ID:
        return _read_key_account(COOP_KEY_VAR, own)

    mesma_chave = (f"{COOP_KEY_VAR} e {BESU_KEY_VAR} apontam para a mesma chave; "
                   f"fora do chainId {LOCAL_TEST_CHAIN_ID} a chave da cooperativa deve ser separada da Besu")
    if os.path.normcase(os.path.realpath(own)) == os.path.normcase(os.path.realpath(besu)):
        raise CoopKeyError(mesma_chave)  # recusa antes de ler: a chave da Besu nem vira conta
    account = _read_key_account(COOP_KEY_VAR, own)
    try:
        besu_address = _read_key_account(BESU_KEY_VAR, besu).address
    except CoopKeyError:
        return account  # chave da Besu ausente/ilegível: não há com o que conflitar
    if besu_address == account.address:
        raise CoopKeyError(mesma_chave)
    return account


def get_or_create_wallet(producer_did: str) -> str:
    """Endereço da carteira custodial do produtor; cria a chave se ainda não existe
    (criação exclusiva: duas chamadas simultâneas não geram duas chaves)."""
    path = _key_path(producer_did)
    if not path.exists():
        PRODUCERS_KEY_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(Account.create().key.hex().removeprefix("0x"))
        except FileExistsError:
            pass
    return Account.from_key("0x" + path.read_text(encoding="utf-8").strip()).address


def load_wallet_account(producer_did: str):
    """Conta (eth_account) da carteira custodial do produtor, ou None se o arquivo da chave NÃO existir.
    Só LÊ: nunca cria chave (diferente de `get_or_create_wallet`). Usada pelo saque, que não pode
    inventar uma carteira nova para um DID desconhecido."""
    path = _key_path(producer_did)
    if not path.exists():
        return None
    return Account.from_key("0x" + path.read_text(encoding="utf-8").strip())


# --- Marca de entrega (DeliveryTask) no Fuseki ---

def _literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _task_uri(batch_number: str) -> str:
    return BEE + "DeliveryTask_" + re.sub(r"[^A-Za-z0-9_]", "_", batch_number)


def _fuseki(path: str, data: dict, **kwargs):
    r = requests.post(f"{FUSEKI_URL}/{path}", data=data, auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
                      timeout=20, **kwargs)
    r.raise_for_status()
    return r


def write_delivery_task(batch_number: str, status: str, tx_hash: str | None = None, detail: str | None = None) -> None:
    task = _task_uri(batch_number)
    extra = ""
    if tx_hash:
        extra += f" ;\n        :deliveryTxHash {_literal(tx_hash)}"
    if detail:
        extra += f" ;\n        :deliveryDetail {_literal(detail)}"
    _fuseki("update", {"update": f"""{_PREFIXES}
    DELETE WHERE {{ <{task}> ?p ?o }} ;
    INSERT DATA {{
      <{task}> a :DeliveryTask ;
        :deliveryBatch {_literal(batch_number)} ;
        :deliveryStatus {_literal(status)}{extra} .
    }}"""})


def _pending_batches() -> list[str]:
    r = _fuseki("sparql", {"query": f"""{_PREFIXES}
        SELECT ?b WHERE {{ ?t :deliveryStatus ?s ; :deliveryBatch ?b .
        FILTER(?s IN ("pending", "mismatch")) }} ORDER BY ?b"""},
                headers={"Accept": "application/sparql-results+json"})
    return [b["b"]["value"] for b in r.json()["results"]["bindings"]]


def _lote_for_delivery(batch_number: str) -> tuple[str | None, int | None]:
    """(producerDID, quantityMilliliters) do lote no Fuseki; (None, None) se o lote não existe."""
    r = _fuseki("sparql", {"query": f"""{_PREFIXES}
        SELECT ?did ?q WHERE {{ ?l agritrust:batchNumber {_literal(batch_number)} .
          OPTIONAL {{ ?l agritrust:ownedBy ?p . ?p agritrust:hasDID ?did }}
          OPTIONAL {{ ?l :quantityMilliliters ?q }} }} LIMIT 1"""},
                headers={"Accept": "application/sparql-results+json"})
    rows = r.json()["results"]["bindings"]
    if not rows:
        return None, None
    row = rows[0]
    return (row["did"]["value"] if "did" in row else None,
            int(row["q"]["value"]) if "q" in row else None)


# --- Núcleo: consulta antes de enviar ---

def deliver(batch_number: str, producer_did: str, milliliters: int) -> tuple[str, str | None, str | None]:
    """Garante a entrega no VendaDados. Retorna (outcome, txHash, detalhe):
    - "already_registered": entregas(batchRef) já tem o mesmo produtor e mL (sem transação);
    - "mismatch": já existe com dados DIFERENTES (nada é enviado);
    - "sent": não existia; registrarEntrega enviada e confirmada.
    Levanta exceção em falha de rede/revert/timeout (quem chama deixa a tarefa pending)."""
    wallet = get_or_create_wallet(producer_did)
    w3, venda = _contract()
    batch_ref = Web3.keccak(text=batch_number)

    produtor, ml_onchain = venda.functions.entregas(batch_ref).call()
    if produtor != ZERO_ADDRESS:
        if produtor == wallet and ml_onchain == milliliters:
            return "already_registered", None, None
        return "mismatch", None, (f"on-chain: produtor {produtor}, {ml_onchain} mL; "
                                  f"esperado: produtor {wallet}, {milliliters} mL")

    coop = load_coop_account()
    tx = venda.functions.registrarEntrega(wallet, batch_ref, milliliters).build_transaction(
        {"from": coop.address, "nonce": w3.eth.get_transaction_count(coop.address, "pending"), "chainId": w3.eth.chain_id})
    signed = coop.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    tx_hash = w3.eth.send_raw_transaction(raw)
    timeout, poll = receipt_wait()
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout, poll_latency=poll)
    if receipt.status != 1:
        raise RuntimeError(f"registrarEntrega reverteu (tx {tx_hash.hex()})")
    return "sent", "0x" + tx_hash.hex().removeprefix("0x"), None


def deliver_on_post_lote(batch_number: str | None, producer_did: str | None, milliliters: int | None) -> dict:
    """Passo de POST /lote. Nunca levanta: devolve campos para a resposta HTTP.
    - sem quantidade: skipped, sem tarefa;
    - com quantidade: grava pending, entrega, atualiza para registered (ou mismatch);
    - falha de rede/revert: continua pending (o retry resolve)."""
    if milliliters is None:
        return {"deliveryStatus": "skipped"}
    if not batch_number or not producer_did:
        logger.warning("POST /lote: quantidade sem batchNumber/producerDID, entrega pulada")
        return {"deliveryStatus": "skipped"}
    try:
        write_delivery_task(batch_number, "pending")
    except Exception as exc:  # noqa: BLE001
        logger.error("POST /lote: falha ao gravar DeliveryTask do lote %r: %s", batch_number, exc)
        return {"deliveryStatus": "pending", "deliveryError": DELIVERY_ERROR_MSG}
    try:
        outcome, tx_hash, detail = deliver(batch_number, producer_did, milliliters)
        if outcome == "mismatch":
            write_delivery_task(batch_number, "mismatch", detail=detail)
            return {"deliveryStatus": "mismatch", "deliveryError": detail}
        write_delivery_task(batch_number, "registered", tx_hash)
        body = {"deliveryStatus": "registered"}
        if tx_hash:
            body["deliveryTxHash"] = tx_hash
        return body
    except Exception as exc:  # noqa: BLE001 - lote já inserido; a tarefa pending fica para o retry
        logger.error("POST /lote: entrega do lote %r no VendaDados falhou (%s)", batch_number, error_type(exc))
        return {"deliveryStatus": "pending", "deliveryError": DELIVERY_ERROR_MSG}


@router.post("/delivery/retry")
def retry_delivery():
    """Processa as tarefas de entrega `pending` (e reexamina as `mismatch`). Sync (def)
    de propósito: I/O bloqueante, o FastAPI roda em threadpool."""
    try:
        batches = _pending_batches()
    except Exception as exc:  # noqa: BLE001
        from fastapi.responses import JSONResponse
        logger.error("Retry de entrega: erro ao consultar o Fuseki: %s", exc)
        return JSONResponse(status_code=502, content={"error": "server_error",
                                                       "error_description": "Erro ao consultar o Fuseki."})
    results = []
    for batch_number in batches:
        try:
            did, ml = _lote_for_delivery(batch_number)
            if did is None and ml is None:
                results.append({"batchNumber": batch_number, "txHash": None, "outcome": "lote_not_found"})
                continue
            if did is None or ml is None:
                results.append({"batchNumber": batch_number, "txHash": None, "outcome": "failed",
                                "error": "lote sem producerDID ou sem quantidade"})
                continue
            outcome, tx_hash, detail = deliver(batch_number, did, ml)
            if outcome == "mismatch":
                write_delivery_task(batch_number, "mismatch", detail=detail)
                results.append({"batchNumber": batch_number, "txHash": None, "outcome": "mismatch", "detail": detail})
            else:
                write_delivery_task(batch_number, "registered", tx_hash)
                results.append({"batchNumber": batch_number, "txHash": tx_hash, "outcome": outcome})
        except Exception as exc:  # noqa: BLE001 - uma tarefa com erro não interrompe as outras
            logger.error("Retry de entrega: erro no lote %r (%s)", batch_number, error_type(exc))
            results.append({"batchNumber": batch_number, "txHash": None, "outcome": "failed",
                            "error": DELIVERY_ERROR_MSG})
    return {"processed": len(results), "results": results}
