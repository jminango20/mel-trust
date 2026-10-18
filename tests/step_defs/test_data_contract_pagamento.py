"""
Step defs (pytest-bdd) para tests/features/data_contract_pagamento.feature.

Teste de INTEGRAÇÃO do contractRef entre Python (coop-a) e Node (ethers), só em tests/:
nenhum código de produção nem contrato foi alterado.

- Helper Node (HELPER_JS, abaixo) reimplementa A REGRA DOCUMENTADA no docstring de
  coop-a/data_contract_endpoint.py, sem olhar o código Python: chaves ordenadas,
  separadores "," e ":", UTF-8 sem escape de não-ASCII, datas como recebidas;
  keccak256 do ethers. Recebe os termos por stdin e imprime {canonical, ref}.
- Se a ref do helper divergir da do coop-a, o teste NÃO corrige o endpoint: falha
  mostrando as duas strings canônicas lado a lado com ascii() e o primeiro byte
  divergente. A string do lado Python é a regra reaplicada aqui (json.dumps com os mesmos
  parâmetros do endpoint), então serve para localizar a divergência.
- Nome do consumidor: `ConsumidorTeste-<12 hex>-especial ...` com acento, aspas, barra
  invertida, quebra de linha e emoji fora do plano básico (U+1F36F). O sufixo
  `-especial...` entra na allowlist ANCORADA do conftest (`_TEST_CONSUMER_PATTERN`) com a
  flag "s" (ponto casa quebra de linha); sem o prefixo `ConsumidorTeste-<12 hex>-especial`
  nada é apagado.
- Comprador = conta nº 1 do node de desenvolvimento do Hardhat (chave pública e conhecida,
  só redes de teste; o teste confere que o endereço derivado é o 0x7099...79C8).
- Antes do depósito a cooperativa (chave coop-a-besu.key, a mesma que o coop-a usa)
  registra uma entrega de 1 mL para uma carteira aleatória: `depositar` exige oferta de
  cotas > 0 (SemCotas). O estado do node 8546 é efêmero; nada é limpo lá.
"""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import requests
from eth_account import Account
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/data_contract_pagamento.feature")

COOP_A_URL = "http://127.0.0.1:8001"
VENDA_DIR = REPO_ROOT / "contracts" / "venda-dados"
DEPLOY_JSON = VENDA_DIR / "deployments" / "local.json"
COOP_KEY_PATH = REPO_ROOT / "coop-a" / "keys" / "coop-a-besu.key"
# Conta nº 1 do node de desenvolvimento do Hardhat (mnemônico público padrão).
HARDHAT_ACCOUNT_1_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
HARDHAT_ACCOUNT_1_ADDRESS = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

HELPER_JS = r"""
const { keccak256, toUtf8Bytes } = require("ethers");
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (c) => (raw += c));
process.stdin.on("end", () => {
  const terms = JSON.parse(raw);
  // regra documentada: chaves ordenadas; "," e ":" sem espaço; UTF-8 sem escape de não-ASCII
  // (JSON.stringify de cada string não escapa não-ASCII); datas e demais valores como vieram.
  const canonical = "{" + Object.keys(terms).sort()
    .map((k) => JSON.stringify(k) + ":" + JSON.stringify(terms[k])).join(",") + "}";
  const ref = keccak256(toUtf8Bytes(canonical));
  process.stdout.write(JSON.stringify({ canonical, ref }));
});
"""

_ABI = [
    {"type": "function", "name": "depositos", "stateMutability": "view",
     "inputs": [{"name": "", "type": "bytes32"}],
     "outputs": [{"name": "comprador", "type": "address"}, {"name": "valor", "type": "uint256"},
                 {"name": "depositadoEm", "type": "uint64"}, {"name": "estado", "type": "uint8"}]},
    {"type": "function", "name": "depositar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}, {"name": "valor", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "registrarEntrega", "stateMutability": "nonpayable",
     "inputs": [{"name": "produtor", "type": "address"}, {"name": "batchRef", "type": "bytes32"},
                {"name": "mililitros", "type": "uint256"}], "outputs": []},
]
_ERC20_ABI = [
    {"type": "function", "name": "mint", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]


@pytest.fixture
def context():
    return {}


def _deploy():
    if not DEPLOY_JSON.exists():
        pytest.fail("contracts/venda-dados/deployments/local.json não existe: rode "
                    "`npx hardhat node --port 8546` e `node scripts/deploy-local.js`")
    return json.loads(DEPLOY_JSON.read_text())


def _web3():
    dep = _deploy()
    w3 = Web3(Web3.HTTPProvider(dep["rpcUrl"], request_kwargs={"timeout": 15}))
    try:
        chain_id = w3.eth.chain_id
        code = w3.eth.get_code(Web3.to_checksum_address(dep["vendaDados"]))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"node do VendaDados não respondeu em {dep['rpcUrl']}: {exc}")
    assert chain_id == dep["chainId"] == 31337, f"chainId {chain_id}, esperado 31337 (só redes de teste)"
    assert len(code) > 0, "sem contrato no endereço do deploy (node reiniciado? rode o deploy de novo)"
    return w3, dep


def _send(w3, account, fn):
    tx = fn.build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address, "pending"),
                               "chainId": w3.eth.chain_id})
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=30)
    assert receipt.status == 1, f"transação reverteu: {receipt}"
    return receipt


def _python_canonical(terms: dict) -> str:
    return json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@given("os termos de um contrato de dados com consumidor de nome sintético cheio de caracteres especiais "
       "e a conta de teste nº 1 do Hardhat como comprador")
def _termos(context):
    buyer = Account.from_key(HARDHAT_ACCOUNT_1_KEY)
    assert buyer.address == HARDHAT_ACCOUNT_1_ADDRESS, buyer.address
    context["buyer"] = buyer
    context["terms"] = {
        # acento, aspas, barra invertida, quebra de linha, emoji fora do plano básico (U+1F36F)
        "consumer": f'ConsumidorTeste-{uuid.uuid4().hex[:12]}-especial Cooperativa Ágil "aspas" \\ barra\nnova linha \U0001F36F',
        "purpose": "Compliance EUDR",
        "priceUnits": 2_500_000,
        "buyerWallet": buyer.address,
        "validFrom": "2026-11-01T00:00:00Z",
        "validUntil": "2027-11-01T00:00:00Z",
    }


@when("o coop-a recebe POST /data-contract com esses termos")
def _post(context):
    context["response"] = requests.post(f"{COOP_A_URL}/data-contract", json=context["terms"], timeout=30)


@then("a resposta é 201 com contractRef em bytes32 hexadecimal")
def _201(context):
    resp = context["response"]
    assert resp.status_code == 201, f"esperado 201, veio {resp.status_code}: {resp.text}"
    ref = resp.json().get("contractRef", "")
    assert len(ref) == 66 and ref.startswith("0x") and int(ref, 16) >= 0, f"contractRef não é bytes32 hex: {ref!r}"
    context["ref"] = ref


@then("o helper Node reproduz exatamente o contractRef devolvido pelo coop-a")
def _helper_node(context):
    proc = subprocess.run(
        ["node", "-e", HELPER_JS], input=json.dumps(context["terms"]).encode("utf-8"),
        cwd=VENDA_DIR, capture_output=True, timeout=60,  # cwd: onde 'ethers' está em node_modules
    )
    assert proc.returncode == 0, f"helper Node falhou: {proc.stderr.decode('utf-8', 'replace')}"
    out = json.loads(proc.stdout.decode("utf-8"))
    py_canonical = _python_canonical(context["terms"])
    if out["ref"] != context["ref"]:
        a, b = py_canonical.encode("utf-8"), out["canonical"].encode("utf-8")
        first = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
        pytest.fail(
            "DIVERGÊNCIA entre Python e Node (endpoint NÃO foi alterado)\n"
            f"ref coop-a : {context['ref']}\nref Node   : {out['ref']}\n"
            f"canônica (Python) : {ascii(py_canonical)}\ncanônica (Node)   : {ascii(out['canonical'])}\n"
            f"primeiro byte UTF-8 divergente: índice {first} "
            f"(Python {a[first:first + 1]!r}, Node {b[first:first + 1]!r})"
        )
    # a regra reaplicada em Python também tem que bater com a ref do coop-a e com a string do Node
    assert out["canonical"] == py_canonical
    assert "0x" + Web3.keccak(text=py_canonical).hex().removeprefix("0x") == context["ref"]


@when("o comprador deposita o priceUnits no VendaDados com essa ref")
def _deposita(context):
    w3, dep = _web3()
    venda = w3.eth.contract(address=Web3.to_checksum_address(dep["vendaDados"]), abi=_ABI)
    usdt = w3.eth.contract(address=Web3.to_checksum_address(dep["usdt"]), abi=_ERC20_ABI)
    coop = Account.from_key("0x" + COOP_KEY_PATH.read_text(encoding="utf-8").strip())
    buyer, price = context["buyer"], context["terms"]["priceUnits"]
    # oferta de cotas > 0 (SemCotas): entrega de 1 mL para uma carteira aleatória
    _send(w3, coop, venda.functions.registrarEntrega(Account.create().address, Web3.keccak(text=uuid.uuid4().hex), 1))
    _send(w3, buyer, usdt.functions.mint(buyer.address, price))
    _send(w3, buyer, usdt.functions.approve(venda.address, price))
    ref_bytes = bytes.fromhex(context["ref"].removeprefix("0x"))
    _send(w3, buyer, venda.functions.depositar(ref_bytes, price))
    context.update(w3=w3, venda=venda, ref_bytes=ref_bytes)


@then("depositos da ref devolve o comprador, o priceUnits, um timestamp maior que zero e o estado 1")
def _depositos(context):
    comprador, valor, depositado_em, estado = context["venda"].functions.depositos(context["ref_bytes"]).call()
    assert comprador == context["buyer"].address
    assert valor == context["terms"]["priceUnits"]
    assert depositado_em > 0
    assert estado == 1
