"""
Step defs (pytest-bdd) para tests/features/venda_dados_liberacao.feature.

FASE RED (TDD): escrito e rodado ANTES de o coop-a ter a liberação do pagamento. Hoje a rota não
existe (404 do FastAPI). Nada foi implementado.

DECISÕES (contrato proposto, só coop-a; a confirmar antes do GREEN):
- Rota: POST /data-contract/{contractRef}/release, sem corpo. O coop-a lê os termos no Fuseki e o
  depósito no VendaDados, calcula o status do pagamento (a mesma precedência do GET .../payment) e,
  só se for "paid", envia `liberar(ref)` assinado pela chave da cooperativa (coop-a-besu.key).
- Respostas: 200 {"status": "released", "txHash"}; 200 {"status": "already_released"} (estado já
  Liberado e depósito conferido; nenhuma transação); 409 {"error": "not_payable", "status": <not_paid |
  mismatch | outside_window | refunded>} (nenhuma transação); 404 {"error": "not_found"} (nenhum
  contrato com a ref; o CORPO, não o `{"detail": "Not Found"}` do FastAPI); 503 {"error":
  "service_unavailable"} com mensagem FIXA (o texto da exceção só vai ao log).
- "Sem transação" = o nonce (pending) da conta da cooperativa não muda entre antes e depois.
- Todo cenário (exceto o do node parado) implanta um VendaDados PRÓPRIO com parte da cooperativa de
  10% (tests/support/venda_dados_deploy.py, o mesmo do teste do saque): assim a conta do rateio não
  depende de outros testes. Fica só o produtor deste cenário como dono de cotas, então o rateio é
  exatamente 90% do priceUnits e a parte da cooperativa, 10%.
- Montagem sem a Besu: carteira custodial pelo helper do coop-a (`get_or_create_wallet`, apagada no
  fim), entrega direto no contrato (`registrarEntrega`, chave da cooperativa), Contrato de Dados por
  POST /data-contract (consumidor `ConsumidorTeste-<12 hex>`, limpo pela purga do conftest), depósito
  do comprador (conta nº 1 do Hardhat) ou de "outra carteira" (conta nº 2); chaves públicas, só teste.
- Datas dos contratos em relação a agora (UTC), ISO com hora e Z.
- O cenário @node_parado exige o node PARADO e fica fora da suíte padrão (pytest.ini).
"""
import functools
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
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
from load_node_module import load_node_module  # noqa: E402
from venda_dados_deploy import deploy_proprio  # noqa: E402

scenarios("../features/venda_dados_liberacao.feature")

COOP_A_URL = "http://127.0.0.1:8001"
DEPLOY_JSON = REPO_ROOT / "contracts" / "venda-dados" / "deployments" / "local.json"
COOP_KEY_PATH = REPO_ROOT / "coop-a" / "keys" / "coop-a-besu.key"
BUYER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # conta nº 1 do Hardhat
OTHER_KEY = "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"  # conta nº 2 do Hardhat
MILILITROS = 5_000_000
PRICE = 1000 * 10**6  # 1000 USDT (6 casas); 10% e 90% exatos

_ABI = [
    {"type": "function", "name": "registrarEntrega", "stateMutability": "nonpayable",
     "inputs": [{"name": "produtor", "type": "address"}, {"name": "batchRef", "type": "bytes32"},
                {"name": "mililitros", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "depositar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}, {"name": "valor", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "reembolsar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}], "outputs": []},
    {"type": "function", "name": "saldoTotalSacavel", "stateMutability": "view",
     "inputs": [{"name": "conta", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "saldoSacavel", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]
_ERC20_ABI = [
    {"type": "function", "name": "mint", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]


@functools.lru_cache(maxsize=1)
def _delivery():
    """coop-a/delivery.py carregado de forma isolada (helper de tests/support)."""
    return load_node_module("coop-a", "delivery.py", "delivery")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _deploy():
    if not DEPLOY_JSON.exists():
        pytest.fail("contracts/venda-dados/deployments/local.json não existe: rode `npx hardhat node --port 8546` "
                    "e `node scripts/deploy-local.js`")
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
    """Dicionário do cenário; ao final restaura o local.json e apaga a chave custodial sintética."""
    ctx = {}
    yield ctx
    if "local_json_original" in ctx:
        DEPLOY_JSON.write_bytes(ctx["local_json_original"])  # bytes: sem trocar o fim de linha
    did = ctx.get("did")
    if did:
        try:
            _delivery()._key_path(did).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - limpeza nunca derruba o teste
            pass


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


def _coop_nonce():
    w3, *_ = _chain()
    return w3.eth.get_transaction_count(_coop().address, "pending")


def _post_contract(context, valid_from: datetime, valid_until: datetime):
    buyer = Account.from_key(BUYER_KEY)
    terms = {
        "consumer": f"ConsumidorTeste-{uuid.uuid4().hex[:12]}",
        "purpose": "Compliance EUDR",
        "priceUnits": PRICE,
        "buyerWallet": buyer.address,
        "validFrom": _iso(valid_from),
        "validUntil": _iso(valid_until),
    }
    resp = requests.post(f"{COOP_A_URL}/data-contract", json=terms, timeout=30)
    assert resp.status_code == 201, f"POST /data-contract: esperado 201, veio {resp.status_code}: {resp.text}"
    context["ref"] = resp.json()["contractRef"]
    context["ref_bytes"] = bytes.fromhex(context["ref"].removeprefix("0x"))


def _deposit(context, account, valor):
    w3, venda, usdt = _chain()
    _send(w3, account, usdt.functions.mint(account.address, valor))
    _send(w3, account, usdt.functions.approve(venda.address, valor))
    _send(w3, account, venda.functions.depositar(context["ref_bytes"], valor))


def _post_release(context):
    resp = requests.post(f"{COOP_A_URL}/data-contract/{context['ref']}/release", timeout=60)
    context["response"] = resp
    context.setdefault("responses", []).append(resp)


@given("um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário")
def _deploy_proprio(context):
    _deploy()  # falha com a instrução certa se o deploy padrão não existe
    context["local_json_original"] = deploy_proprio(10)


@given("um producerDID sintético com uma entrega registrada direto no contrato")
def _produtor_com_entrega(context):
    context["did"] = f"did:key:zTesteLiberacao{uuid.uuid4().hex}"
    context["wallet"] = _delivery().get_or_create_wallet(context["did"])
    w3, venda, _ = _chain()
    _send(w3, _coop(), venda.functions.registrarEntrega(context["wallet"], Web3.keccak(text=uuid.uuid4().hex), MILILITROS))


@given("um Contrato de Dados criado por POST /data-contract com a janela válida agora")
def _contrato_valido(context):
    now = datetime.now(timezone.utc)
    _post_contract(context, now - timedelta(days=1), now + timedelta(days=30))


@given(parsers.parse("um Contrato de Dados criado por POST /data-contract com a janela toda no {janela}"))
def _contrato_janela(context, janela):
    now = datetime.now(timezone.utc)
    if janela == "passado":
        _post_contract(context, now - timedelta(days=60), now - timedelta(days=30))
    elif janela == "futuro":
        _post_contract(context, now + timedelta(days=30), now + timedelta(days=60))
    else:
        raise AssertionError(f"janela desconhecida: {janela!r}")


@given("o comprador do contrato depositou o priceUnits exato com a ref do contrato")
def _deposito_correto(context):
    _deposit(context, Account.from_key(BUYER_KEY), PRICE)


@given("outra carteira depositou o priceUnits exato com a ref do contrato")
def _deposito_outra_carteira(context):
    _deposit(context, Account.from_key(OTHER_KEY), PRICE)


@given("o comprador do contrato depositou um valor diferente do priceUnits com a ref do contrato")
def _deposito_valor_diferente(context):
    _deposit(context, Account.from_key(BUYER_KEY), PRICE + 1)


@given("a cooperativa reembolsou o depósito direto no contrato")
def _reembolso(context):
    w3, venda, _ = _chain()
    _send(w3, _coop(), venda.functions.reembolsar(context["ref_bytes"]))


@given("um contractRef bem formado que nenhum Contrato de Dados tem")
def _ref_inexistente(context):
    context["ref"] = "0x" + Web3.keccak(text=f"inexistente-{uuid.uuid4().hex}").hex().removeprefix("0x")


@given("o nonce da cooperativa foi anotado")
def _anota_nonce(context):
    context["nonce_coop"] = _coop_nonce()


@given(parsers.parse('o coop-a já recebeu POST /data-contract/<contractRef>/release com status "{status}"'))
def _primeira_liberacao(context, status):
    _post_release(context)
    resp = context["response"]
    assert resp.status_code == 200, f"primeira liberação: esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, resp.json()


@given("que o node do VendaDados está parado")
def _node_parado(context):
    assert not _rpc_open(), "este cenário exige o node da 8546 PARADO; pare-o e rode de novo com -m node_parado"


@when("o coop-a recebe POST /data-contract/<contractRef>/release")
def _liberar(context):
    _post_release(context)


@when("o coop-a recebe POST /data-contract/<contractRef>/release de novo")
def _liberar_de_novo(context):
    _post_release(context)


@then('a resposta é 200 com status "released" e txHash')
def _200_released(context):
    resp = context["response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "released", body
    tx = body.get("txHash", "")
    assert len(tx) == 66 and tx.startswith("0x"), f"txHash inválido: {tx!r}"


@then(parsers.parse('a resposta é 200 com status "{status}"'))
def _200_status(context, status):
    resp = context["response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, f"esperado status={status!r}, veio {resp.json()}"


@then(parsers.parse('GET /data-contract/<contractRef>/payment responde "{status}"'))
def _get_payment(context, status):
    resp = requests.get(f"{COOP_A_URL}/data-contract/{context['ref']}/payment", timeout=30)
    assert resp.status_code == 200, f"GET payment: esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, resp.json()


@then("o saldoTotalSacavel do produtor é igual ao rateio de 90% do priceUnits")
def _rateio_produtor(context):
    _, venda, _ = _chain()
    esperado = PRICE * 90 // 100
    assert venda.functions.saldoTotalSacavel(context["wallet"]).call() == esperado


@then("o saldoSacavel da cooperativa é a parte de 10% do priceUnits")
def _parte_coop(context):
    _, venda, _ = _chain()
    assert venda.functions.saldoSacavel(_coop().address).call() == PRICE * 10 // 100


@then(parsers.parse('a resposta é 409 com error "{error}" e status "{status}"'))
def _409(context, error, status):
    resp = context["response"]
    assert resp.status_code == 409, f"esperado 409, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("error") == error, f"esperado error={error!r}, veio {body}"
    assert body.get("status") == status, f"esperado status={status!r}, veio {body}"


@then("o nonce da cooperativa não mudou")
def _nonce_igual(context):
    assert _coop_nonce() == context["nonce_coop"], "a cooperativa enviou uma transação"


@then('a resposta é 404 com error "not_found" no corpo')
def _404_not_found(context):
    resp = context["response"]
    assert resp.status_code == 404, f"esperado 404, veio {resp.status_code}: {resp.text}"
    # Rota inexistente também dá 404 ({"detail": "Not Found"}); a GREEN deve devolver o erro do endpoint.
    assert resp.json().get("error") == "not_found", f"esperado error=not_found no corpo, veio {resp.json()}"


@then("nenhuma das respostas contém a chave da cooperativa em hex nem o caminho dela")
def _sem_vazamento(context):
    key_hex = COOP_KEY_PATH.read_text(encoding="utf-8").strip().lower().removeprefix("0x")
    assert len(key_hex) == 64
    assert len(context["responses"]) == 2, "o cenário deveria ter feito duas chamadas"
    for resp in context["responses"]:
        texto = resp.text.lower()
        assert key_hex not in texto and ("0x" + key_hex) not in texto, "a resposta contém a chave da cooperativa"
        for fragmento in (str(COOP_KEY_PATH).lower(), COOP_KEY_PATH.name.lower(), "coop-a-besu", str(COOP_KEY_PATH.parent).lower(),
                          "keys/", "keys\\\\"):
            assert fragmento not in texto, f"a resposta contém o caminho da chave ({fragmento!r}): {resp.text}"
    # sanidade: a primeira chamada de fato liberou (senão a verificação acima seria vazia)
    assert context["responses"][0].status_code == 200 and context["responses"][0].json().get("status") == "released"


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
