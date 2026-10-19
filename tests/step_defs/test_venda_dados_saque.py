"""
Step defs (pytest-bdd) para tests/features/venda_dados_saque.feature.

FASE RED (TDD): escrito e rodado ANTES de o coop-a ter o saque em nome do produtor. Hoje a rota
não existe (404 do FastAPI). Nada foi implementado.

DECISÕES (contrato proposto, só coop-a; a confirmar antes do GREEN):
- Rota: POST /producers/{producerDID}/withdraw. O coop-a carrega a chave custodial do produtor
  (a mesma de coop-a/keys/producers/<sha256(producerDID)>.key, criada em delivery.py), repõe o GÁS
  NATIVO da carteira se faltar (a cooperativa paga o gás; a carteira custodial nasce com 0) e envia
  `sacar()` assinado pela carteira do produtor. `sacar()` paga a `msg.sender` (VendaDados.sol l.174),
  então o USDT vai para a carteira custodial, não para a cooperativa.
- Respostas: 200 {"status": "withdrawn", "amount": <unidades mínimas do USDT, 6 casas>, "txHash"};
  200 {"status": "nothing_to_withdraw"} (nenhuma transação, nem a de reposição de gás); 404
  {"error": "not_found"} para DID sem carteira (a rota NÃO cria chave: sem get_or_create); 503
  {"error": "service_unavailable"} com mensagem FIXA quando o node cai (o texto da exceção só vai ao
  log). A resposta nunca traz a chave privada nem o caminho do arquivo.
- "Quanto sacar" é `saldoTotalSacavel(carteira)` (view nova do VendaDados, que inclui o pendente que
  o getter público `saldoSacavel` não enxerga). O teste usa a mesma view para saber o valor esperado,
  porque o node é compartilhado (outras cotas no supply mudam a fatia do produtor).
- Montagem do saldo SEM a Besu (escolhida): o teste cria a carteira pelo helper do coop-a
  (`tests/support/load_node_module.py` carregando `coop-a/delivery.py` e chamando
  `get_or_create_wallet`), registra a entrega direto no contrato (`registrarEntrega`, assinado com
  `coop-a/keys/coop-a-besu.key`, a conta da cooperativa no deploy) e faz a venda (mint do MockUSDT,
  `approve`, `depositar` e `liberar`). Assim os cenários são `@sem_besu` e não passam por POST /lote
  (que exigiria o túnel da Besu para ancorar). Dependência declarada: o node 8546 com o deploy
  atual (que tem `saldoTotalSacavel`).
- Carteiras sintéticas: producerDID `did:key:zTesteSaque<uuid>`; a chave custodial criada é apagada
  no fim de cada cenário (fixture autouse). Chaves de outros testes nunca são tocadas.
- Comprador da venda = conta nº 1 do Hardhat (chave pública conhecida, só rede de teste).
- Cooperativa no deploy local: `PERCENTUAL_COOP = 0n` (deploy-local.js l.11), então o produtor
  recebe 100% da venda; "o USDT da cooperativa não muda" vale porque o saque não mexe nele.
- O cenário @node_parado exige o node PARADO e fica fora da suíte padrão (pytest.ini).
"""
import functools
import json
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
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
from load_node_module import load_node_module  # noqa: E402
from venda_dados_deploy import deploy_proprio  # noqa: E402

scenarios("../features/venda_dados_saque.feature")

COOP_A_URL = "http://127.0.0.1:8001"
DEPLOY_JSON = REPO_ROOT / "contracts" / "venda-dados" / "deployments" / "local.json"
COOP_KEY_PATH = REPO_ROOT / "coop-a" / "keys" / "coop-a-besu.key"
BUYER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"  # conta nº 1 do Hardhat
MILILITROS = 5_000_000
VALOR_VENDA = 1000 * 10**6  # 1000 USDT (6 casas)

_ABI = [
    {"type": "function", "name": "registrarEntrega", "stateMutability": "nonpayable",
     "inputs": [{"name": "produtor", "type": "address"}, {"name": "batchRef", "type": "bytes32"},
                {"name": "mililitros", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "depositar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}, {"name": "valor", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "liberar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}], "outputs": []},
    {"type": "function", "name": "saldoTotalSacavel", "stateMutability": "view",
     "inputs": [{"name": "conta", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]
_ERC20_ABI = [
    {"type": "function", "name": "mint", "stateMutability": "nonpayable",
     "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "outputs": []},
    {"type": "function", "name": "approve", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]


@functools.lru_cache(maxsize=1)
def _delivery():
    """coop-a/delivery.py carregado de forma isolada (helper de tests/support): usamos
    `get_or_create_wallet` e `_key_path` para criar a carteira e achar o arquivo da chave."""
    return load_node_module("coop-a", "delivery.py", "delivery")


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
    """Preflight do node 8546 (com o deploy que tem `saldoTotalSacavel`). O cenário @node_parado é o inverso."""
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
    try:
        contrato = w3.eth.contract(address=Web3.to_checksum_address(dep["vendaDados"]), abi=_ABI)
        contrato.functions.saldoTotalSacavel(Web3.to_checksum_address(dep["cooperativa"])).call()
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"o VendaDados implantado não tem saldoTotalSacavel (deploy antigo?): rode o deploy de novo. {exc}")


@pytest.fixture
def context():
    """Dicionário do cenário; ao final apaga a chave custodial sintética que o cenário criou."""
    ctx = {}
    yield ctx
    if "local_json_original" in ctx:  # restaura o deploy padrão que o cenário do deploy próprio trocou
        DEPLOY_JSON.write_bytes(ctx["local_json_original"])  # bytes: restaura sem trocar o fim de linha
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
    return w3, venda, usdt, dep


def _send(w3, account, fn):
    tx = fn.build_transaction({"from": account.address, "nonce": w3.eth.get_transaction_count(account.address, "pending"),
                               "chainId": w3.eth.chain_id})
    signed = account.sign_transaction(tx)
    raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
    receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=30)
    assert receipt.status == 1, f"transação reverteu: {receipt}"


def _coop():
    return Account.from_key("0x" + COOP_KEY_PATH.read_text(encoding="utf-8").strip())


def _novo_did(context):
    context["did"] = f"did:key:zTesteSaque{uuid.uuid4().hex}"


def _post_withdraw(context):
    return requests.post(f"{COOP_A_URL}/producers/{context['did']}/withdraw", timeout=60)


@given("um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário")
def _deploy_proprio(context):
    """Implanta um MockUSDT + VendaDados NOVOS (cooperativa = chave do coop-a, parte 10%, reembolso 30 dias)
    e aponta `deployments/local.json` para eles durante o cenário: o coop-a lê esse arquivo a cada chamada
    (`venda_dados_config`). O arquivo original é restaurado no fim (fixture `context`)."""
    _deploy()  # falha com a instrução certa se o deploy padrão não existe
    context["local_json_original"] = deploy_proprio(10)  # implementação compartilhada em tests/support


@given("o saldo sacável da cooperativa foi anotado")
def _anota_saldo_coop(context):
    _, venda, _, _ = _chain()
    context["coop_total_antes"] = venda.functions.saldoTotalSacavel(_coop().address).call()
    assert context["coop_total_antes"] > 0, "com parte de 10% a cooperativa deveria ter saldo sacável após a venda"


@then("o saldoTotalSacavel da cooperativa não mudou e é maior que 0")
def _coop_total_igual(context):
    _, venda, _, _ = _chain()
    depois = venda.functions.saldoTotalSacavel(_coop().address).call()
    assert depois == context["coop_total_antes"] > 0, f"saldo sacável da cooperativa: {context['coop_total_antes']} -> {depois}"


@given("um producerDID sintético com carteira custodial criada pelo helper do coop-a")
def _did_com_carteira(context):
    _novo_did(context)
    context["wallet"] = _delivery().get_or_create_wallet(context["did"])


@given("um producerDID sintético que não tem carteira custodial")
def _did_sem_carteira(context):
    _novo_did(context)
    assert not _delivery()._key_path(context["did"]).exists()


@given("o produtor tem uma entrega registrada no VendaDados e uma venda liberada")
def _entrega_e_venda(context):
    w3, venda, usdt, _ = _chain()
    coop, buyer = _coop(), Account.from_key(BUYER_KEY)
    _send(w3, coop, venda.functions.registrarEntrega(context["wallet"], Web3.keccak(text=uuid.uuid4().hex), MILILITROS))
    ref = Web3.keccak(text=uuid.uuid4().hex)
    _send(w3, buyer, usdt.functions.mint(buyer.address, VALOR_VENDA))
    _send(w3, buyer, usdt.functions.approve(venda.address, VALOR_VENDA))
    _send(w3, buyer, venda.functions.depositar(ref, VALOR_VENDA))
    _send(w3, coop, venda.functions.liberar(ref))
    # estado ANTES do saque (o node é compartilhado: o valor esperado vem da view, não de uma conta fixa)
    context["esperado"] = venda.functions.saldoTotalSacavel(context["wallet"]).call()
    assert context["esperado"] > 0, "montagem do saldo falhou: o produtor não ficou com saldo sacável"
    context["usdt_wallet_antes"] = usdt.functions.balanceOf(context["wallet"]).call()
    context["usdt_coop_antes"] = usdt.functions.balanceOf(coop.address).call()


@given("os nonces da cooperativa e da carteira do produtor foram anotados")
def _anota_nonces(context):
    w3, *_ = _chain()
    context["nonce_coop"] = w3.eth.get_transaction_count(_coop().address, "pending")
    context["nonce_wallet"] = w3.eth.get_transaction_count(context["wallet"], "pending")


@given("a quantidade de chaves custodiais foi anotada")
def _anota_chaves(context):
    d = _delivery().PRODUCERS_KEY_DIR
    context["chaves_antes"] = sorted(p.name for p in d.glob("*.key")) if d.exists() else []


@given(parsers.parse('o coop-a já recebeu POST /producers/<producerDID>/withdraw com status "{status}"'))
def _primeiro_saque(context, status):
    resp = _post_withdraw(context)
    assert resp.status_code == 200, f"primeiro saque: esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, resp.json()


@given("que o node do VendaDados está parado")
def _node_parado(context):
    assert not _rpc_open(), "este cenário exige o node da 8546 PARADO; pare-o e rode de novo com -m node_parado"


@when("o coop-a recebe POST /producers/<producerDID>/withdraw")
def _saque(context):
    context["response"] = _post_withdraw(context)
    context.setdefault("responses", []).append(context["response"])


@when("o coop-a recebe POST /producers/<producerDID>/withdraw de novo")
def _saque_de_novo(context):
    context["response"] = _post_withdraw(context)
    context.setdefault("responses", []).append(context["response"])


@then("a resposta é 200 com status \"withdrawn\", amount igual ao saldo total sacável de antes e txHash")
def _200_withdrawn(context):
    resp = context["response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "withdrawn", body
    assert body.get("amount") == context["esperado"], f"amount {body.get('amount')} != esperado {context['esperado']}"
    tx = body.get("txHash", "")
    assert len(tx) == 66 and tx.startswith("0x"), f"txHash inválido: {tx!r}"


@then(parsers.parse('a resposta é 200 com status "{status}"'))
def _200_status(context, status):
    resp = context["response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, f"esperado status={status!r}, veio {resp.json()}"


@then("o saldo de USDT da carteira custodial sobe exatamente o amount")
def _usdt_wallet_sobe(context):
    _, _, usdt, _ = _chain()
    depois = usdt.functions.balanceOf(context["wallet"]).call()
    assert depois - context["usdt_wallet_antes"] == context["response"].json()["amount"] == context["esperado"]


@then("o saldoTotalSacavel da carteira custodial fica 0")
def _total_zero(context):
    _, venda, _, _ = _chain()
    assert venda.functions.saldoTotalSacavel(context["wallet"]).call() == 0


@then("o saldo de USDT da cooperativa não muda")
def _usdt_coop_igual(context):
    _, _, usdt, _ = _chain()
    assert usdt.functions.balanceOf(_coop().address).call() == context["usdt_coop_antes"]


@then("o nonce da cooperativa e o da carteira do produtor não mudaram")
def _nonces_iguais(context):
    w3, *_ = _chain()
    assert w3.eth.get_transaction_count(_coop().address, "pending") == context["nonce_coop"], "a cooperativa enviou uma transação"
    assert w3.eth.get_transaction_count(context["wallet"], "pending") == context["nonce_wallet"], "a carteira do produtor enviou uma transação"


@then('a resposta é 404 com error "not_found" no corpo')
def _404_not_found(context):
    resp = context["response"]
    assert resp.status_code == 404, f"esperado 404, veio {resp.status_code}: {resp.text}"
    # Rota inexistente também dá 404 ({"detail": "Not Found"}); a GREEN deve devolver o erro do endpoint.
    assert resp.json().get("error") == "not_found", f"esperado error=not_found no corpo, veio {resp.json()}"


@then("nenhum arquivo novo apareceu em coop-a/keys/producers/")
def _sem_arquivo_novo(context):
    d = _delivery().PRODUCERS_KEY_DIR
    depois = sorted(p.name for p in d.glob("*.key")) if d.exists() else []
    assert depois == context["chaves_antes"], f"apareceram arquivos: {set(depois) - set(context['chaves_antes'])}"
    assert not _delivery()._key_path(context["did"]).exists()


@then("nenhuma das respostas contém a chave privada em hex nem o caminho da chave")
def _sem_vazamento(context):
    key_path = _delivery()._key_path(context["did"])
    key_hex = key_path.read_text(encoding="utf-8").strip().lower().removeprefix("0x")
    assert len(key_hex) == 64
    assert len(context["responses"]) == 2, "o cenário deveria ter feito dois saques"
    for resp in context["responses"]:
        texto = resp.text.lower()
        assert key_hex not in texto and ("0x" + key_hex) not in texto, "a resposta contém a chave privada"
        for fragmento in (str(key_path).lower(), key_path.name.lower(), str(key_path.parent).lower(),
                          "keys/producers", "keys\\\\producers"):
            assert fragmento not in texto, f"a resposta contém o caminho da chave ({fragmento!r}): {resp.text}"
    # sanidade: o primeiro saque de fato aconteceu (senão a verificação acima seria vazia)
    assert context["responses"][0].status_code == 200 and context["responses"][0].json().get("status") == "withdrawn"


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
