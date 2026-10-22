"""
Passos de tests/features/venda_dados_prazo_recibo.feature (RED: prazo do recibo fixo e 503 no lugar de 202).

Em processo: `tests/support/load_node_module.py` carrega `coop-a/release_endpoint.py`, que importa por nome
simples `delivery`, `data_contract_endpoint` e `withdraw_endpoint`; os testes chamam `release(ref)`,
`withdraw(did)`, `deliver(...)` e `get_data_contract_payment(ref)` direto e leem o JSONResponse.

Cada cenário implanta um MockUSDT + VendaDados NOVOS no node 8546 (conta nº 0 do Hardhat, que é do node de
dev) com uma cooperativa gerada aqui, e configura o ambiente do coop-a por variáveis (VENDA_DADOS_*), sem
tocar no deployments/local.json. Chaves da cooperativa e do produtor: diretório temporário, apagado ao fim.

`web3.eth.Eth.wait_for_transaction_receipt` é trocado por um espião: registra (timeout, poll_latency) das
chamadas feitas DURANTE a ação (não as da montagem) e delega ao real; nos cenários de timeout, levanta
`TimeExhausted` (com um texto-marcador) depois de a transação já ter sido enviada de verdade ao node, que a
minera na hora.

O RPC configurado leva um sentinela na query (`?apikey=...`): nem a resposta nem os logs podem repeti-lo.
Depende do node 8546, do Fuseki do coop-a e do coop-a (8001, só para criar o Contrato de Dados).
"""
import json
import logging
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from eth_account import Account
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3
from web3.eth import Eth
from web3.exceptions import TimeExhausted

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
import venda_dados_chain as vc  # noqa: E402
from load_node_module import load_node_module  # noqa: E402

scenarios("../features/venda_dados_prazo_recibo.feature")

NODE = "http://127.0.0.1:8546"
SENTINELA = "CHAVE-SINTETICA-" + uuid.uuid4().hex
MARCADOR_EXCECAO = "TEXTO-DA-EXCECAO-" + uuid.uuid4().hex
NOMES_SIMPLES = ("delivery", "data_contract_endpoint", "withdraw_endpoint", "release_endpoint", "shacl_pipeline")
ARTEFATOS = REPO_ROOT / "contracts" / "venda-dados" / "artifacts" / "contracts"
MILILITROS = 5_000_000
PRECO = vc.PRICE

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


@pytest.fixture
def context(monkeypatch, caplog):
    anteriores = {n: sys.modules.get(n) for n in NOMES_SIMPLES}
    for n in NOMES_SIMPLES:
        sys.modules.pop(n, None)
    tmp = Path(tempfile.mkdtemp(prefix="prazo-recibo-"))
    caplog.set_level(logging.INFO)
    ctx = {"mp": monkeypatch, "caplog": caplog, "tmp": tmp, "mods": None, "esperas": [], "gravando": False,
           "modo": "real", "hexes": [], "caminhos": [tmp]}
    original = Eth.wait_for_transaction_receipt

    def espiao(self, transaction_hash, timeout=120, poll_latency=0.1):
        if ctx["gravando"]:
            ctx["esperas"].append((timeout, poll_latency))
            if ctx["modo"] == "timeout":  # a tx JÁ foi enviada (send_raw_transaction vem antes) e minerada
                raise TimeExhausted(f"{MARCADOR_EXCECAO}: sem recibo em {timeout}s")
        return original(self, transaction_hash, timeout=timeout, poll_latency=poll_latency)

    monkeypatch.setattr(Eth, "wait_for_transaction_receipt", espiao)
    yield ctx
    shutil.rmtree(tmp, ignore_errors=True)
    for n, antigo in anteriores.items():
        if antigo is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = antigo


# --- Montagem na cadeia ---

def _w3():
    return Web3(Web3.HTTPProvider(NODE, request_kwargs={"timeout": 30}))


def _envia(w3, conta, fn):
    tx = fn.build_transaction({"from": conta.address, "nonce": w3.eth.get_transaction_count(conta.address, "pending"),
                               "chainId": w3.eth.chain_id})
    raw = conta.sign_transaction(tx)
    raw = getattr(raw, "raw_transaction", None) or raw.rawTransaction
    recibo = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(raw), timeout=30)
    assert recibo.status == 1, f"montagem: transação reverteu: {recibo}"


def _modulos(ctx):
    if ctx["mods"] is None:
        release = load_node_module("coop-a", "release_endpoint.py", "release_prazo_recibo")
        ctx["mods"] = {"release_endpoint": release, **{n: sys.modules[n] for n in
                                                       ("delivery", "data_contract_endpoint", "withdraw_endpoint")}}
    return ctx["mods"]


def _chave(ctx, nome: str) -> tuple[Account, Path]:
    conta = Account.create()
    caminho = ctx["tmp"] / f"{nome}.key"
    caminho.write_text(conta.key.hex().removeprefix("0x"), encoding="utf-8")
    ctx["hexes"].append(conta.key.hex().removeprefix("0x").lower())
    ctx["caminhos"].append(caminho)
    return conta, caminho


def _saldo_sacavel(ctx, conta: str) -> int:
    return ctx["venda"].functions.saldoTotalSacavel(conta).call()


@given("um VendaDados novo no node local, com a cooperativa e um produtor sintético")
def _vendadados_novo(context):
    try:
        w3 = _w3()
        assert w3.eth.chain_id == 31337
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"node do VendaDados (8546) fora do ar ou com chainId inesperado: {type(exc).__name__}")
    banco = w3.eth.accounts[0]  # conta nº 0 do node de dev (destravada), como em deploy-local.js
    coop, coop_path = _chave(context, "coop")
    w3.eth.wait_for_transaction_receipt(w3.eth.send_transaction({"from": banco, "to": coop.address, "value": 10**19}))
    arte = lambda *p: json.loads((ARTEFATOS.joinpath(*p)).read_text())  # noqa: E731
    usdt_art, venda_art = arte("test", "MockUSDT.sol", "MockUSDT.json"), arte("VendaDados.sol", "VendaDados.json")
    tx = w3.eth.contract(abi=usdt_art["abi"], bytecode=usdt_art["bytecode"]).constructor().transact({"from": banco})
    usdt_addr = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    tx = w3.eth.contract(abi=venda_art["abi"], bytecode=venda_art["bytecode"]).constructor(
        usdt_addr, coop.address, 10, 30 * 24 * 3600).transact({"from": banco})
    venda_addr = w3.eth.wait_for_transaction_receipt(tx).contractAddress
    context.update(w3=w3, banco=banco, coop=coop, usdt_addr=usdt_addr, venda_addr=venda_addr,
                   venda=w3.eth.contract(address=venda_addr, abi=_ABI),
                   usdt=w3.eth.contract(address=usdt_addr, abi=vc.ERC20_ABI),
                   url_rpc=f"{NODE}/?apikey={SENTINELA}")
    mp = context["mp"]
    mp.setenv("VENDA_DADOS_RPC_URL", context["url_rpc"])
    mp.setenv("VENDA_DADOS_CHAIN_ID", "31337")
    mp.setenv("VENDA_DADOS_ADDRESS", venda_addr)
    mp.setenv("VENDA_DADOS_COOP_KEY_PATH", str(coop_path))
    mp.setenv("PRODUCERS_KEY_DIR", str(context["tmp"] / "producers"))
    mp.delenv("RECEIPT_TIMEOUT_S", raising=False)
    mp.delenv("RECEIPT_POLL_S", raising=False)
    did = f"did:key:zPrazoRecibo{uuid.uuid4().hex[:12]}"
    context["did"] = did
    context["wallet"] = _modulos(context)["delivery"].get_or_create_wallet(did)
    produtores = context["tmp"] / "producers"
    context["caminhos"] += list(produtores.glob("*.key"))
    for chave in produtores.glob("*.key"):
        context["hexes"].append(chave.read_text(encoding="utf-8").strip().lower())


def _deposito_pago(ctx, ref: bytes, entrega: bool):
    w3, venda, usdt, coop = ctx["w3"], ctx["venda"], ctx["usdt"], ctx["coop"]
    if entrega:
        _envia(w3, coop, venda.functions.registrarEntrega(ctx["wallet"], Web3.keccak(text=uuid.uuid4().hex), MILILITROS))
    comprador = Account.from_key(vc.BUYER_KEY)
    _envia(w3, comprador, usdt.functions.mint(comprador.address, PRECO))
    _envia(w3, comprador, usdt.functions.approve(venda.address, PRECO))
    _envia(w3, comprador, venda.functions.depositar(ref, PRECO))


@given("um Contrato de Dados pago e com entrega registrada")
def _contrato_pago_com_entrega(context):
    ref, _ = vc.post_contract(*vc.window("válida agora"))
    context["ref"] = ref
    _deposito_pago(context, bytes.fromhex(ref[2:]), entrega=True)


@given("a chave da cooperativa configurada não é a do deploy")
def _chave_errada(context):
    outra, caminho = _chave(context, "outra-coop")  # financiada, mas não é a `cooperativa` do contrato: liberar() reverte
    context["w3"].eth.wait_for_transaction_receipt(
        context["w3"].eth.send_transaction({"from": context["banco"], "to": outra.address, "value": 10**18}))
    context["mp"].setenv("VENDA_DADOS_COOP_KEY_PATH", str(caminho))


def _produtor_com_saldo(ctx, gas: bool):
    ref = Web3.keccak(text=uuid.uuid4().hex)
    _deposito_pago(ctx, ref, entrega=True)
    _envia(ctx["w3"], ctx["coop"], ctx["venda"].functions.liberar(ref))  # a venda é liberada: o produtor tem saldo
    if gas:
        ctx["w3"].eth.wait_for_transaction_receipt(
            ctx["w3"].eth.send_transaction({"from": ctx["banco"], "to": ctx["wallet"], "value": 10**17}))
    ctx["esperado"] = _saldo_sacavel(ctx, ctx["wallet"])
    assert ctx["esperado"] > 0, "montagem: o produtor não ficou com saldo sacável"
    ctx["usdt_antes"] = ctx["usdt"].functions.balanceOf(ctx["wallet"]).call()


@given("um produtor com saldo sacável e a carteira com gás")
def _produtor_com_gas(context):
    _produtor_com_saldo(context, gas=True)


@given("um produtor com saldo sacável e a carteira sem gás")
def _produtor_sem_gas(context):
    _produtor_com_saldo(context, gas=False)


# --- Condições ---

@given(parsers.parse('RECEIPT_TIMEOUT_S "{timeout}" e RECEIPT_POLL_S "{poll}"'))
def _env_recibo(context, timeout, poll):
    _define_recibo(context, timeout, poll)


def _define_recibo(ctx, timeout, poll):
    for nome, valor in (("RECEIPT_TIMEOUT_S", timeout), ("RECEIPT_POLL_S", poll)):
        if valor == "ausente":
            ctx["mp"].delenv(nome, raising=False)
        else:
            ctx["mp"].setenv(nome, valor)


@given("a espera do recibo levanta TimeExhausted depois de a transação ser enviada de verdade")
def _modo_timeout(context):
    context["modo_na_acao"] = "timeout"


@given("o node do VendaDados fora do ar")
def _node_fora(context):
    context["mp"].setenv("VENDA_DADOS_RPC_URL", f"http://127.0.0.1:8599/?apikey={SENTINELA}")  # ninguém escuta


@given("o chainId configurado como 11155111")
def _chain_errado(context):
    context["mp"].setenv("VENDA_DADOS_CHAIN_ID", "11155111")


# --- Ações ---

def _gravar(ctx):
    ctx["esperas"].clear()
    ctx["gravando"] = True
    ctx["modo"] = ctx.get("modo_na_acao", "real")


def _resposta(r) -> tuple[int, dict]:
    if isinstance(r, dict):  # o saque devolve o dict direto no sucesso (o FastAPI responde 200)
        return 200, r
    return r.status_code, json.loads(r.body)


@when("a entrega do lote é registrada no VendaDados")
def _entrega(context):
    dele = _modulos(context)["delivery"]
    _gravar(context)
    resultado = dele.deliver(f"MS-2026-ENTREGA-{uuid.uuid4().hex[:12]}", context["did"], 1000)
    context["esperas_por_acao"] = [list(context["esperas"])]
    assert resultado[0] == "sent", resultado


@when(parsers.parse('RECEIPT_TIMEOUT_S passa a "{timeout}" e RECEIPT_POLL_S a "{poll}"'))
def _muda_env(context, timeout, poll):
    _define_recibo(context, timeout, poll)


@when("uma segunda entrega do lote é registrada")
def _segunda_entrega(context):
    dele = _modulos(context)["delivery"]
    context["esperas"].clear()
    resultado = dele.deliver(f"MS-2026-ENTREGA-{uuid.uuid4().hex[:12]}", context["did"], 1000)
    context["esperas_por_acao"].append(list(context["esperas"]))
    assert resultado[0] == "sent", resultado


@when("a liberação do contrato é executada")
def _liberacao(context):
    mods = _modulos(context)
    _gravar(context)
    context["resp"] = mods["release_endpoint"].release(context["ref"])


@when("o saque do produtor é executado")
def _saque(context):
    mods = _modulos(context)
    _gravar(context)
    context["resp"] = mods["withdraw_endpoint"].withdraw(context["did"])


# --- Verificações: espera do recibo ---

@then(parsers.parse("wait_for_transaction_receipt recebeu o prazo {prazo} e o intervalo {intervalo}"))
def _recebeu(context, prazo, intervalo):
    assert context["esperas"], "wait_for_transaction_receipt não foi chamado"
    assert all(e == (float(prazo), float(intervalo)) for e in context["esperas"]), (
        f"recebeu (timeout, poll_latency) = {context['esperas']}; esperado ({float(prazo)}, {float(intervalo)})")


@then(parsers.parse("a primeira espera recebeu o prazo {p1} e o intervalo {i1} e a segunda o prazo {p2} e o intervalo {i2}"))
def _duas_esperas(context, p1, i1, p2, i2):
    primeira, segunda = context["esperas_por_acao"]
    assert primeira == [(float(p1), float(i1))], f"primeira espera: {primeira}"
    assert segunda == [(float(p2), float(i2))], f"segunda espera: {segunda}"


@then(parsers.parse("houve {n:d} esperas de recibo, todas com o prazo {prazo} e o intervalo {intervalo}"))
def _n_esperas(context, n, prazo, intervalo):
    assert context["esperas"] == [(float(prazo), float(intervalo))] * n, f"esperas: {context['esperas']}"


# --- Verificações: respostas ---

@then(parsers.parse('a resposta é 200 com status "{status}"'))
def _200(context, status):
    code, corpo = _resposta(context["resp"])
    assert (code, corpo.get("status")) == (200, status), f"{code} {_sem_marcador(corpo)}"


@then(parsers.parse('a resposta é 202 com status "pending", operation "{operacao}" e o txHash'))
def _202(context, operacao):
    code, corpo = _resposta(context["resp"])
    assert code == 202, f"esperado 202, veio {code} {_sem_marcador(corpo)}"
    assert corpo.get("status") == "pending" and corpo.get("operation") == operacao, corpo
    assert isinstance(corpo.get("txHash"), str) and corpo["txHash"].startswith("0x") and len(corpo["txHash"]) == 66, corpo
    context["tx_hash"] = corpo["txHash"]


@then(parsers.parse("a resposta é 503 com a mensagem fixa {qual}"))
def _503(context, qual):
    mods = context["mods"]
    esperada = {"da liberação": mods["release_endpoint"].SERVICE_UNAVAILABLE_MSG,
                "do saque": mods["withdraw_endpoint"].SERVICE_UNAVAILABLE_MSG}[qual]
    code, corpo = _resposta(context["resp"])
    assert code == 503, f"esperado 503, veio {code} {_sem_marcador(corpo)}"
    assert corpo == {"error": "service_unavailable", "error_description": esperada}, corpo


def _sem_marcador(corpo):
    return str(corpo)[:200]


@then("o txHash existe no node com status 1")
def _tx_existe(context):
    w3 = context["w3"]
    for _ in range(20):
        try:
            assert w3.eth.get_transaction_receipt(context["tx_hash"]).status == 1
            return
        except Exception as exc:  # noqa: BLE001 - TransactionNotFound até minerar
            ultimo = exc
            time.sleep(0.25)
    pytest.fail(f"o txHash não existe no node com status 1: {type(ultimo).__name__}")


@then('a leitura do pagamento passa a "released"')
def _pagamento_released(context):
    resp = context["mods"]["data_contract_endpoint"].get_data_contract_payment(context["ref"])
    corpo = resp if isinstance(resp, dict) else json.loads(resp.body)
    assert corpo.get("status") == "released", corpo


@then('uma segunda liberação responde 200 com status "already_released"')
def _segunda_liberacao(context):
    code, corpo = _resposta(context["mods"]["release_endpoint"].release(context["ref"]))
    assert (code, corpo.get("status")) == (200, "already_released"), f"{code} {corpo}"


@then("o saldo de USDT da carteira custodial subiu o saldo sacável de antes")
def _usdt_subiu(context):
    depois = context["usdt"].functions.balanceOf(context["wallet"]).call()
    assert depois - context["usdt_antes"] == context["esperado"], (
        f"USDT subiu {depois - context['usdt_antes']}; esperado {context['esperado']}")


@then('um segundo saque responde 200 com status "nothing_to_withdraw"')
def _segundo_saque(context):
    code, corpo = _resposta(context["mods"]["withdraw_endpoint"].withdraw(context["did"]))
    assert (code, corpo.get("status")) == (200, "nothing_to_withdraw"), f"{code} {corpo}"


@then("o sacar() não foi enviado")
def _sacar_nao_enviado(context):
    w3 = context["w3"]
    assert w3.eth.get_transaction_count(context["wallet"], "latest") == 0, "a carteira do produtor enviou uma transação"
    assert context["usdt"].functions.balanceOf(context["wallet"]).call() == context["usdt_antes"]
    assert _saldo_sacavel(context, context["wallet"]) == context["esperado"]


# --- Verificações: vazamento ---

def _logs(caplog) -> str:
    fmt = logging.Formatter("%(name)s %(levelname)s %(message)s")
    return "\n".join(fmt.format(r) for r in caplog.records if r.levelno >= logging.INFO)


def _sem_vazamento(context, com_marcador: bool):
    texto = (context["resp"].body.decode("utf-8") + "\n" + _logs(context["caplog"])).lower()
    proibidos = [SENTINELA, "127.0.0.1:8546", "127.0.0.1:8599", *context["hexes"],
                 *[str(c) for c in context["caminhos"]], *[Path(c).name for c in context["caminhos"]]]
    if com_marcador:
        proibidos.append(MARCADOR_EXCECAO)
    for p in proibidos:
        assert p.lower() not in texto, f"a resposta ou os logs contêm {p[:24]}…"


@then("a resposta e os logs não contêm URL, chave nem caminho")
def _sem_url(context):
    _sem_vazamento(context, com_marcador=False)


@then(parsers.parse("a resposta e os logs não contêm URL, chave, caminho nem o texto da exceção, e o log traz o tipo {tipo}"))
def _sem_url_com_tipo(context, tipo):
    _sem_vazamento(context, com_marcador=True)
    assert tipo in _logs(context["caplog"]), f"o log não traz o tipo {tipo}: {_logs(context['caplog'])[:300]!r}"
