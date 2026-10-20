"""
Helpers de cadeia e de Contrato de Dados para os testes da venda de dados que montam estado direto
no VendaDados (node Hardhat 8546, só rede de teste) e no coop-a (POST /data-contract).

Usado por test_venda_dados_acesso.py. (test_venda_dados_liberacao.py e test_venda_dados_saque.py
ainda têm cópias próprias destes helpers; unificar é um refactor à parte.)
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from eth_account import Account
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
COOP_A_URL = "http://127.0.0.1:8001"
DEPLOY_JSON = REPO_ROOT / "contracts" / "venda-dados" / "deployments" / "local.json"
COOP_KEY_PATH = REPO_ROOT / "coop-a" / "keys" / "coop-a-besu.key"
BUYER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # conta nº 1 do Hardhat
OTHER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"  # conta nº 2 do Hardhat
PRICE = 1000 * 10**6

ABI = [
    {"type": "function", "name": "registrarEntrega", "stateMutability": "nonpayable",
     "inputs": [{"name": "produtor", "type": "address"}, {"name": "batchRef", "type": "bytes32"},
                {"name": "mililitros", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "depositar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}, {"name": "valor", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "reembolsar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}], "outputs": []},
    {"type": "function", "name": "saldoTotalSacavel", "stateMutability": "view",
     "inputs": [{"name": "conta", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]
ERC20_ABI = [
    {"type": "function", "name": "mint", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def deploy():
    if not DEPLOY_JSON.exists():
        pytest.fail("contracts/venda-dados/deployments/local.json não existe: rode `npx hardhat node --port 8546` "
                    "e `node scripts/deploy-local.js`")
    return json.loads(DEPLOY_JSON.read_text())


def rpc_open() -> bool:
    try:
        Web3(Web3.HTTPProvider(deploy()["rpcUrl"], request_kwargs={"timeout": 5})).eth.chain_id
        return True
    except Exception:  # noqa: BLE001
        return False


def chain():
    dep = deploy()
    w3 = Web3(Web3.HTTPProvider(dep["rpcUrl"], request_kwargs={"timeout": 15}))
    venda = w3.eth.contract(address=Web3.to_checksum_address(dep["vendaDados"]), abi=ABI)
    usdt = w3.eth.contract(address=Web3.to_checksum_address(dep["usdt"]), abi=ERC20_ABI)
    return w3, venda, usdt


def send(w3, account, fn):
    tx = fn.build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address, "pending"),
                               "chainId": w3.eth.chain_id})
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=30)
    assert receipt.status == 1, f"transação reverteu: {receipt}"


def coop():
    return Account.from_key("0x" + COOP_KEY_PATH.read_text(encoding="utf-8").strip())


def post_contract(valid_from: datetime, valid_until: datetime, price: int = PRICE) -> tuple[str, dict]:
    """POST /data-contract com consumidor sintético ConsumidorTeste-<12 hex> e comprador = conta nº 1."""
    terms = {
        "consumer": f"ConsumidorTeste-{uuid.uuid4().hex[:12]}",
        "purpose": "Compliance EUDR",
        "priceUnits": price,
        "buyerWallet": Account.from_key(BUYER_KEY).address,
        "validFrom": iso(valid_from),
        "validUntil": iso(valid_until),
    }
    resp = requests.post(f"{COOP_A_URL}/data-contract", json=terms, timeout=30)
    assert resp.status_code == 201, f"POST /data-contract: esperado 201, veio {resp.status_code}: {resp.text}"
    return resp.json()["contractRef"], terms


def window(janela: str) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if janela == "válida agora":
        return now - timedelta(days=1), now + timedelta(days=30)
    if janela == "toda no passado":
        return now - timedelta(days=60), now - timedelta(days=30)
    if janela == "toda no futuro":
        return now + timedelta(days=30), now + timedelta(days=60)
    raise AssertionError(f"janela desconhecida: {janela!r}")


def deposit(ref: str, account, valor: int):
    w3, venda, usdt = chain()
    send(w3, account, usdt.functions.mint(account.address, valor))
    send(w3, account, usdt.functions.approve(venda.address, valor))
    send(w3, account, venda.functions.depositar(bytes.fromhex(ref.removeprefix("0x")), valor))
