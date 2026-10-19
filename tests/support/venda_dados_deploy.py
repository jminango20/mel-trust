"""
Deploy PRÓPRIO do VendaDados para os testes (só rede de teste, node Hardhat da 8546).

Implanta um MockUSDT + VendaDados NOVOS (cooperativa = chave do coop-a, reembolso em 30 dias) e
aponta `contracts/venda-dados/deployments/local.json` para eles: o coop-a lê esse arquivo a cada
chamada (`venda_dados_config`). Devolve os bytes ORIGINAIS do arquivo; quem chamou os restaura no fim
do cenário (bytes: restaura sem trocar o fim de linha).

Compartilhado por test_venda_dados_saque.py e test_venda_dados_liberacao.py.
"""
import json
from pathlib import Path

from eth_account import Account
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_JSON = REPO_ROOT / "contracts" / "venda-dados" / "deployments" / "local.json"
COOP_KEY_PATH = REPO_ROOT / "coop-a" / "keys" / "coop-a-besu.key"
ARTIFACTS = REPO_ROOT / "contracts" / "venda-dados" / "artifacts" / "contracts"


def deploy_proprio(parte_cooperativa_percentual: int = 10) -> bytes:
    dep = json.loads(DEPLOY_JSON.read_text())
    w3 = Web3(Web3.HTTPProvider(dep["rpcUrl"], request_kwargs={"timeout": 30}))
    banco = w3.eth.accounts[0]  # conta nº 0 do node de dev (destravada), como em deploy-local.js
    coop = Account.from_key("0x" + COOP_KEY_PATH.read_text(encoding="utf-8").strip())
    usdt_art = json.loads((ARTIFACTS / "test" / "MockUSDT.sol" / "MockUSDT.json").read_text())
    venda_art = json.loads((ARTIFACTS / "VendaDados.sol" / "VendaDados.json").read_text())
    tx = w3.eth.contract(abi=usdt_art["abi"], bytecode=usdt_art["bytecode"]).constructor().transact({"from": banco})
    usdt_addr = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    tx = w3.eth.contract(abi=venda_art["abi"], bytecode=venda_art["bytecode"]).constructor(
        usdt_addr, coop.address, parte_cooperativa_percentual, 30 * 24 * 60 * 60).transact({"from": banco})
    venda_addr = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    original = DEPLOY_JSON.read_bytes()
    DEPLOY_JSON.write_bytes((json.dumps({**dep, "usdt": usdt_addr, "vendaDados": venda_addr}, indent=2) + "\n").encode("utf-8"))
    return original
