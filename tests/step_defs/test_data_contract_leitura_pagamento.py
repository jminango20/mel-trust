"""
Step defs (pytest-bdd) para tests/features/data_contract_leitura_pagamento.feature.

FASE RED (TDD): escrito e rodado ANTES de o coop-a ter a leitura do pagamento. Hoje a rota
não existe (404 do FastAPI). Nada foi implementado.

DECISÕES (contrato proposto, só coop-a; a confirmar antes do GREEN):
- Rota: GET /data-contract/{contractRef}/payment. Só leitura: lê `depositos(ref)` do
  VendaDados por eth_call, SEM chave. Configuração: as MESMAS variáveis de
  coop-a/delivery.py (VENDA_DADOS_RPC_URL, VENDA_DADOS_ADDRESS, VENDA_DADOS_CHAIN_ID,
  e o fallback contracts/venda-dados/deployments/local.json).
- Respostas: 200 {"contractRef", "status"}; 404 se nenhum DataContract tem esse contractRef
  no Fuseki; 503 {"error": "service_unavailable", "error_description": <mensagem FIXA>}
  quando o node/contrato não responde (o texto da exceção só vai ao log).
- status, em ordem de decisão (comprador/valor do contrato = buyerWallet/priceUnits):
    estado 0 (Nenhum)                                 -> "not_paid"
    estado != 0 e (comprador != buyerWallet ou
                   valor != priceUnits)               -> "mismatch"  (não considera pago)
    estado 2 (Liberado)                               -> "released"
    estado 3 (Reembolsado)                            -> "refunded"
    estado 1 e depositadoEm fora de [validFrom, validUntil] -> "outside_window"
    estado 1 dentro da janela                         -> "paid"
  A janela compara o timestamp do BLOCO do depósito (depositadoEm), não a hora da leitura.
  A checagem de identidade vem antes de released/refunded: um depósito de outra carteira
  nunca vira "released". (A confirmar: é a precedência escolhida aqui.)
- Janela: validFrom <= depositadoEm <= validUntil (limites inclusivos, a confirmar).
- Datas dos testes: ISO com hora e Z, geradas em relação a agora (UTC).
- Higiene: consumidores `ConsumidorTeste-<12 hex>`; a purga do conftest apaga contrato e
  consumidor (allowlist ancorada). O estado do node 8546 é efêmero.
- O cenário @node_parado exige o node PARADO e fica fora da suíte padrão (pytest.ini);
  rodar com -m node_parado. Os demais exigem o node no ar.
- Carteiras: comprador = conta nº 1 do Hardhat; "outra carteira" = conta nº 2 (chaves
  públicas e conhecidas do mnemônico padrão, só redes de teste). A cooperativa usa
  coop-a/keys/coop-a-besu.key só para registrarEntrega/liberar/reembolsar (preparar o
  estado on-chain). A LEITURA do coop-a não usa chave.
"""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from eth_account import Account
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/data_contract_leitura_pagamento.feature")

COOP_A_URL = "http://localhost:8001"
DEPLOY_JSON = REPO_ROOT / "contracts" / "venda-dados" / "deployments" / "local.json"
COOP_KEY_PATH = REPO_ROOT / "coop-a" / "keys" / "coop-a-besu.key"
# Contas nº 1 e nº 2 do node de desenvolvimento do Hardhat (mnemônico público padrão).
BUYER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
OTHER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
PRICE = 2_500_000

_ABI = [
    {"type": "function", "name": "depositar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}, {"name": "valor", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "liberar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}], "outputs": []},
    {"type": "function", "name": "reembolsar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}], "outputs": []},
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


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _deploy():
    if not DEPLOY_JSON.exists():
        pytest.fail("contracts/venda-dados/deployments/local.json não existe: rode "
                    "`npx hardhat node --port 8546` e `node scripts/deploy-local.js`")
    return json.loads(DEPLOY_JSON.read_text())


def _rpc_open() -> bool:
    try:
        Web3(Web3.HTTPProvider(_deploy()["rpcUrl"], request_kwargs={"timeout": 5})).eth.chain_id
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(autouse=True)
def _venda_node_alive(request):
    """Preflight do node 8546. O cenário @node_parado é o inverso e dispensa este check."""
    if request.node.get_closest_marker("node_parado"):
        return
    dep = _deploy()
    try:
        w3 = Web3(Web3.HTTPProvider(dep["rpcUrl"], request_kwargs={"timeout": 10}))
        chain_id, code = w3.eth.chain_id, w3.eth.get_code(Web3.to_checksum_address(dep["vendaDados"]))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"node do VendaDados não respondeu em {dep['rpcUrl']}: {exc}. Suba: "
                    "cd contracts/venda-dados && npx hardhat node --port 8546, depois node scripts/deploy-local.js")
    if chain_id != 31337 or len(code) == 0:
        pytest.fail(f"chainId {chain_id} ou sem contrato em {dep['vendaDados']} (node reiniciado? rode o deploy)")


@pytest.fixture
def context():
    return {}


def _chain():
    dep = _deploy()
    w3 = Web3(Web3.HTTPProvider(dep["rpcUrl"], request_kwargs={"timeout": 15}))
    venda = w3.eth.contract(address=Web3.to_checksum_address(dep["vendaDados"]), abi=_ABI)
    usdt = w3.eth.contract(address=Web3.to_checksum_address(dep["usdt"]), abi=_ERC20_ABI)
    return w3, venda, usdt


def _send(w3, account, fn):
    tx = fn.build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address, "pending"),
                               "chainId": w3.eth.chain_id})
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=30)
    assert receipt.status == 1, f"transação reverteu: {receipt}"


def _coop():
    return Account.from_key("0x" + COOP_KEY_PATH.read_text(encoding="utf-8").strip())


def _post_contract(context, valid_from: datetime, valid_until: datetime):
    _post_contract_raw(context, _iso(valid_from), _iso(valid_until))


def _post_contract_raw(context, valid_from: str, valid_until: str):
    buyer = Account.from_key(BUYER_KEY)
    terms = {
        "consumer": f"ConsumidorTeste-{uuid.uuid4().hex[:12]}",
        "purpose": "Compliance EUDR",
        "priceUnits": PRICE,
        "buyerWallet": buyer.address,
        "validFrom": valid_from,
        "validUntil": valid_until,
    }
    resp = requests.post(f"{COOP_A_URL}/data-contract", json=terms, timeout=30)
    assert resp.status_code == 201, f"POST /data-contract: esperado 201, veio {resp.status_code}: {resp.text}"
    context["terms"] = terms
    context["ref"] = resp.json()["contractRef"]
    context["ref_bytes"] = bytes.fromhex(context["ref"].removeprefix("0x"))


def _deposit(context, account, valor):
    w3, venda, usdt = _chain()
    # oferta de cotas > 0 (SemCotas): entrega de 1 mL para uma carteira aleatória
    _send(w3, _coop(), venda.functions.registrarEntrega(Account.create().address, Web3.keccak(text=uuid.uuid4().hex), 1))
    _send(w3, account, usdt.functions.mint(account.address, valor))
    _send(w3, account, usdt.functions.approve(venda.address, valor))
    _send(w3, account, venda.functions.depositar(context["ref_bytes"], valor))


@given("um contrato de dados válido agora criado via POST /data-contract")
def _contrato_valido(context):
    now = datetime.now(timezone.utc)
    _post_contract(context, now - timedelta(days=1), now + timedelta(days=30))


@given(parsers.parse("um contrato de dados com a janela toda no {janela} criado via POST /data-contract"))
def _contrato_janela(context, janela):
    now = datetime.now(timezone.utc)
    if janela == "passado":
        _post_contract(context, now - timedelta(days=60), now - timedelta(days=30))
    elif janela == "futuro":
        _post_contract(context, now + timedelta(days=30), now + timedelta(days=60))
    else:
        raise AssertionError(f"janela desconhecida: {janela!r}")


@given(parsers.parse("um contrato de dados com {campo} 1 hora {direcao} de agora escrito com o fuso {fuso}"))
def _contrato_fuso(context, campo, direcao, fuso):
    """O instante do limite é 1 h antes/depois de agora, mas ESCRITO no fuso dado. Lido como se o
    relógio escrito fosse UTC, o depósito cairia DENTRO da janela (paid); em UTC de verdade cai fora."""
    now = datetime.now(timezone.utc)
    sign = 1 if fuso.startswith("+") else -1
    hh, mm = fuso[1:].split(":")
    zone = timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
    instant = now - timedelta(hours=1) if direcao == "antes" else now + timedelta(hours=1)
    written = instant.astimezone(zone).isoformat(timespec="seconds")
    assert written.endswith(fuso), written
    if campo == "validUntil":
        _post_contract_raw(context, _iso(now - timedelta(days=2)), written)
    elif campo == "validFrom":
        _post_contract_raw(context, written, _iso(now + timedelta(days=2)))
    else:
        raise AssertionError(f"campo desconhecido: {campo!r}")


@given("o comprador do contrato depositou o priceUnits exato com a ref do contrato")
def _deposito_certo(context):
    _deposit(context, Account.from_key(BUYER_KEY), PRICE)


@given("uma OUTRA carteira depositou o priceUnits exato com a ref do contrato")
def _deposito_outra(context):
    _deposit(context, Account.from_key(OTHER_KEY), PRICE)


@given("o comprador do contrato depositou um valor diferente do priceUnits com a ref do contrato")
def _deposito_valor_errado(context):
    _deposit(context, Account.from_key(BUYER_KEY), PRICE - 1)


@given(parsers.parse("a cooperativa {acao} a ref no VendaDados"))
def _coop_acao(context, acao):
    w3, venda, _ = _chain()
    fn = {"liberou": venda.functions.liberar, "reembolsou": venda.functions.reembolsar}[acao]
    _send(w3, _coop(), fn(context["ref_bytes"]))


@given("um contractRef bytes32 que nenhum contrato do coop-a tem")
def _ref_inexistente(context):
    context["ref"] = "0x" + Web3.keccak(text="inexistente-" + uuid.uuid4().hex).hex().removeprefix("0x")


@given("que o node do VendaDados está parado")
def _node_parado(context):
    assert not _rpc_open(), "este cenário exige o node da 8546 PARADO; pare-o e rode de novo com -m node_parado"


@when("o coop-a lê o pagamento do contrato")
def _le_pagamento(context):
    context["response"] = requests.get(f"{COOP_A_URL}/data-contract/{context['ref']}/payment", timeout=30)


@then(parsers.parse('a resposta é 200 com status "{status}"'))
def _200_status(context, status):
    resp = context["response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, f"esperado status={status!r}, veio {resp.json()}"


@then("a resposta é 404")
def _404(context):
    resp = context["response"]
    assert resp.status_code == 404, f"esperado 404, veio {resp.status_code}: {resp.text}"
    # Rota inexistente também dá 404 ({"detail": "Not Found"}); a GREEN deve devolver o erro do endpoint.
    assert resp.json().get("error") == "not_found", f"esperado error=not_found, veio {resp.json()}"


@then("a resposta é 503 com mensagem fixa e sem texto de exceção")
def _503(context):
    resp = context["response"]
    assert resp.status_code == 503, f"esperado 503, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("error") == "service_unavailable", body
    assert body.get("error_description"), body
    texto = resp.text.lower()
    for vazamento in ("127.0.0.1", "8546", "localhost", "connection", "refused", "httpconnectionpool",
                      "traceback", "max retries", "errno"):
        assert vazamento not in texto, f"a resposta vaza texto de exceção ({vazamento!r}): {resp.text}"
