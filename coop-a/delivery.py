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
  VENDA_DADOS_CHAIN_ID (default 31337). O chainId é conferido ANTES de assinar:
  a cooperativa usa a MESMA chave da Besu, então enviar para a rede errada seria
  um erro silencioso.
"""
import hashlib
import json
import logging
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
COOP_KEY_PATH = Path(os.environ.get("BESU_KEY_PATH", _THIS_DIR / "keys" / "coop-a-besu.key"))
_DEPLOY_JSON = _THIS_DIR.parent / "contracts" / "venda-dados" / "deployments" / "local.json"

RECEIPT_TIMEOUT_S = 60

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


def _contract():
    rpc, chain_id, address = venda_dados_config()
    if not address:
        raise RuntimeError("VENDA_DADOS_ADDRESS não configurado (nem deployments/local.json)")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
    got = w3.eth.chain_id  # levanta se o node estiver fora
    if got != chain_id:
        raise RuntimeError(f"chainId {got} em {rpc}, esperado {chain_id}")
    return w3, w3.eth.contract(address=Web3.to_checksum_address(address), abi=_ABI)


# --- Carteira custodial do produtor ---

def _key_path(producer_did: str) -> Path:
    return PRODUCERS_KEY_DIR / (hashlib.sha256(producer_did.encode("utf-8")).hexdigest() + ".key")


def load_coop_account():
    """Conta (eth_account) da cooperativa, lida de COOP_KEY_PATH (coop-a-besu.key). Só LÊ."""
    return Account.from_key("0x" + COOP_KEY_PATH.read_text(encoding="utf-8").strip())


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
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT_S)
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
        logger.error("POST /lote: entrega do lote %r no VendaDados falhou: %s", batch_number, exc)
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
            logger.error("Retry de entrega: erro no lote %r: %s", batch_number, exc)
            results.append({"batchNumber": batch_number, "txHash": None, "outcome": "failed",
                            "error": DELIVERY_ERROR_MSG})
    return {"processed": len(results), "results": results}
