"""
Step defs (pytest-bdd) para tests/features/venda_dados_acesso.feature.

FASE RED (TDD): escrito e rodado ANTES de o verifier-demo ter o acesso aos dados com desafio assinado.
Hoje as rotas não existem (404 do FastAPI). Nada foi implementado.

DECISÕES (contrato proposto, só verifier-demo; a confirmar antes do GREEN):
- POST /data-access/challenge/{contractRef} => 200 {"nonce", "message"}. A mensagem contém o
  contractRef, o nonce e "verifier-demo". Contrato desconhecido: 404 {"error": "not_found"} no corpo.
  Nonce de uso único, com TTL, amarrado ao contractRef (store em memória, padrão do presentation-request).
- GET /data-access/{batch_number}: cabeçalhos X-Data-Contract, X-Challenge-Nonce,
  X-Challenge-Signature (EIP-191, `encode_defunct(text=message)`, pela carteira do comprador) e
  X-Access-Purpose. Ordem das respostas: 401 (cabeçalho ausente, nonce desconhecido/usado/de outro
  contrato) -> 403 (assinatura recuperada != buyerWallet) -> 402 {"error": "payment_required",
  "status": <not_paid|mismatch|outside_window|refunded>} -> mesmo corpo do GET /verify.
  "paid" e "released" dão acesso.
- coop-a fora do ar: 503 {"error": "service_unavailable"} com mensagem fixa (o texto da exceção só
  vai ao log). O cenário @coop_a_parado fica fora da suíte padrão (pytest.ini; -m coop_a_parado).
- Estado montado como nos testes da liberação: deploy próprio (10%), entrega direto no contrato,
  Contrato de Dados por POST /data-contract (ConsumidorTeste-<12 hex>), depósito direto, liberação
  pela rota real do coop-a. Comprador = conta nº 1 do Hardhat; outra carteira = nº 2 (só teste).
- O lote é o canônico MS-2026-042 (já cadastrado, fixture fixa da suíte). O contrato de dados não
  amarra o lote: a rota não relaciona contractRef com batch_number (lacuna de desenho, a decidir).
"""
import functools
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
from load_node_module import load_node_module  # noqa: E402
import venda_dados_chain as vc  # noqa: E402
from venda_dados_deploy import deploy_proprio  # noqa: E402

scenarios("../features/venda_dados_acesso.feature")

VERIFIER_URL = "http://127.0.0.1:8004"
LOTE = "MS-2026-042"
PURPOSE = "Compliance EUDR"
MILILITROS = 5_000_000


@functools.lru_cache(maxsize=1)
def _delivery():
    return load_node_module("coop-a", "delivery.py", "delivery")


@pytest.fixture(autouse=True)
def _preflight(request):
    """verifier-demo no ar; node 8546 com deploy (exceto no cenário do coop-a parado)."""
    try:
        assert requests.get(f"{VERIFIER_URL}/health", timeout=5).status_code == 200
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"verifier-demo não respondeu em {VERIFIER_URL}: {exc}")
    if request.node.get_closest_marker("coop_a_parado"):
        return
    dep = vc.deploy()
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
    ctx = {}
    yield ctx
    proc = ctx.get("test_server")
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
    if "local_json_original" in ctx:
        vc.DEPLOY_JSON.write_bytes(ctx["local_json_original"])
    did = ctx.get("did")
    if did:
        try:
            _delivery()._key_path(did).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - limpeza nunca derruba o teste
            pass


def _sign(key: str, message: str) -> str:
    return Account.sign_message(encode_defunct(text=message), private_key=key).signature.hex().removeprefix("0x")


def _challenge(ref: str, base: str = VERIFIER_URL) -> dict:
    resp = requests.post(f"{base}/data-access/challenge/{ref}", timeout=30)
    assert resp.status_code == 200, f"POST challenge: esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert ref in body["message"] and body["nonce"] in body["message"] and "verifier-demo" in body["message"], body
    return body


def _headers(context) -> dict:
    return {"X-Data-Contract": context["header_ref"], "X-Challenge-Nonce": context["nonce"],
            "X-Challenge-Signature": "0x" + context["signature"], "X-Access-Purpose": PURPOSE}


def _get_access(context, headers):
    resp = requests.get(f"{context.get('base_url', VERIFIER_URL)}/data-access/{LOTE}", headers=headers, timeout=60)
    context["response"] = resp
    return resp


@given("um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário")
def _deploy_proprio(context):
    vc.deploy()
    context["local_json_original"] = deploy_proprio(10)


@given("um producerDID sintético com uma entrega registrada direto no contrato")
def _produtor_com_entrega(context):
    context["did"] = f"did:key:zTesteAcesso{uuid.uuid4().hex}"
    wallet = _delivery().get_or_create_wallet(context["did"])
    w3, venda, _ = vc.chain()
    vc.send(w3, vc.coop(), venda.functions.registrarEntrega(wallet, Web3.keccak(text=uuid.uuid4().hex), MILILITROS))


@given(parsers.parse("um Contrato de Dados criado com a janela {janela}"))
def _contrato(context, janela):
    context["ref"], context["terms"] = vc.post_contract(*vc.window(janela))
    context["ref_bytes"] = bytes.fromhex(context["ref"].removeprefix("0x"))


@given("o comprador do contrato depositou o priceUnits exato com a ref do contrato")
def _deposito_correto(context):
    vc.deposit(context["ref"], Account.from_key(vc.BUYER_KEY), vc.PRICE)


@given("outra carteira depositou o priceUnits exato com a ref do contrato")
def _deposito_outra(context):
    vc.deposit(context["ref"], Account.from_key(vc.OTHER_KEY), vc.PRICE)


@given("a cooperativa reembolsou o depósito direto no contrato")
def _reembolso(context):
    w3, venda, _ = vc.chain()
    vc.send(w3, vc.coop(), venda.functions.reembolsar(context["ref_bytes"]))


@given("o coop-a liberou o pagamento por POST /data-contract/<contractRef>/release")
def _liberou(context):
    resp = requests.post(f"{vc.COOP_A_URL}/data-contract/{context['ref']}/release", timeout=60)
    assert resp.status_code == 200 and resp.json().get("status") == "released", f"{resp.status_code}: {resp.text}"


@given("o comprador pediu e assinou o desafio do contrato")
def _desafio_comprador(context):
    ch = _challenge(context["ref"], context.get("base_url", VERIFIER_URL))
    context.update(header_ref=context["ref"], nonce=ch["nonce"], signature=_sign(vc.BUYER_KEY, ch["message"]))


@given("a outra carteira pediu e assinou o desafio do contrato")
def _desafio_outra(context):
    ch = _challenge(context["ref"])
    context.update(header_ref=context["ref"], nonce=ch["nonce"], signature=_sign(vc.OTHER_KEY, ch["message"]))


@given("um segundo Contrato de Dados criado, com o desafio dele pedido e assinado pelo comprador, mas apresentado com o contrato original")
def _desafio_outro_contrato(context):
    ref2, _ = vc.post_contract(*vc.window("válida agora"))
    assert ref2 != context["ref"]
    ch = _challenge(ref2)
    context.update(header_ref=context["ref"], nonce=ch["nonce"], signature=_sign(vc.BUYER_KEY, ch["message"]))


@given("um contractRef bem formado que nenhum Contrato de Dados tem")
def _ref_inexistente(context):
    context["ref"] = "0x" + Web3.keccak(text=f"inexistente-{uuid.uuid4().hex}").hex().removeprefix("0x")


@given("que o coop-a está parado")
def _coop_a_parado(context):
    try:
        requests.get(f"{vc.COOP_A_URL}/docs", timeout=3)
    except requests.ConnectionError:
        return
    raise AssertionError("este cenário exige o coop-a (8001) PARADO; pare-o e rode de novo com -m coop_a_parado")


@given(parsers.parse("o verifier-demo já recebeu GET /data-access/{lote} com o desafio assinado e respondeu 200"))
def _primeiro_acesso(context, lote):
    resp = _get_access(context, _headers(context))
    assert resp.status_code == 200, f"primeiro acesso: esperado 200, veio {resp.status_code}: {resp.text}"


@given("um verifier-demo de teste com CHALLENGE_TTL_SECONDS=1 em outra porta")
def _servidor_ttl_curto(context):
    """Sobe uma instância própria do verifier-demo (porta 8014) com TTL de 1 s; o código é o mesmo da
    8004, só a variável de ambiente muda. Encerrada no fim do cenário (fixture `context`)."""
    node_dir = REPO_ROOT / "verifier-demo"
    python = node_dir / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.Popen(
        [str(python), "-m", "uvicorn", "main:app", "--port", "8014", "--host", "127.0.0.1"],
        cwd=node_dir, env={**os.environ, "CHALLENGE_TTL_SECONDS": "1"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    context["test_server"] = proc
    base = "http://127.0.0.1:8014"
    for _ in range(60):
        try:
            if requests.get(f"{base}/health", timeout=1).status_code == 200:
                context["base_url"] = base
                return
        except requests.RequestException:
            time.sleep(0.5)
    raise AssertionError("o verifier-demo de teste (8014) não subiu em 30 s")


@given(parsers.parse("passaram {segundos:d} segundos"))
def _espera(segundos):
    time.sleep(segundos)


@when("o comprador apresenta, cada uma com um desafio novo, assinaturas não hexadecimais, curtas e longas demais")
def _assinaturas_malformadas(context):
    context["responses"] = []
    for assinatura in ("0x" + "zz" * 65, "0x1234", "0x" + "ab" * 66, "não-é-hex"):
        ch = _challenge(context["ref"])
        headers = {"X-Data-Contract": context["ref"], "X-Challenge-Nonce": ch["nonce"],
                   "X-Challenge-Signature": assinatura, "X-Access-Purpose": PURPOSE}
        context["responses"].append(requests.get(f"{VERIFIER_URL}/data-access/{LOTE}", headers=headers, timeout=60))


@then("todas as respostas são 401 e nenhuma é 500")
def _todas_401(context):
    assert len(context["responses"]) == 4
    for resp in context["responses"]:
        assert resp.status_code != 500, f"500 com assinatura malformada: {resp.text}"
        assert resp.status_code == 401, f"esperado 401, veio {resp.status_code}: {resp.text}"


@when("o verifier-demo recebe POST /data-access/challenge/<contractRef>")
def _pede_desafio(context):
    context["response"] = requests.post(f"{VERIFIER_URL}/data-access/challenge/{context['ref']}", timeout=30)


@when(parsers.parse("o verifier-demo recebe GET /data-access/{lote} sem os cabeçalhos do desafio"))
def _sem_cabecalhos(context, lote):
    _get_access(context, {"X-Access-Purpose": PURPOSE})


@when(parsers.parse("o verifier-demo recebe GET /data-access/{lote} com o desafio assinado"))
def _com_desafio(context, lote):
    _get_access(context, _headers(context))


@when(parsers.parse("o verifier-demo recebe GET /data-access/{lote} com o desafio assinado de novo"))
def _com_desafio_de_novo(context, lote):
    _get_access(context, _headers(context))


@then('a resposta é 404 com error "not_found" no corpo')
def _404_not_found(context):
    resp = context["response"]
    assert resp.status_code == 404, f"esperado 404, veio {resp.status_code}: {resp.text}"
    # Rota inexistente também dá 404 ({"detail": "Not Found"}); a GREEN deve devolver o erro do endpoint.
    assert resp.json().get("error") == "not_found", f"esperado error=not_found no corpo, veio {resp.json()}"


@then(parsers.parse("a resposta é {codigo:d}"))
def _status(context, codigo):
    resp = context["response"]
    assert resp.status_code == codigo, f"esperado {codigo}, veio {resp.status_code}: {resp.text}"


@then(parsers.parse('a resposta é 402 com error "{error}" e status "{status}"'))
def _402(context, error, status):
    resp = context["response"]
    assert resp.status_code == 402, f"esperado 402, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("error") == error and body.get("status") == status, f"esperado {error}/{status}, veio {body}"


@then("o corpo é igual ao do GET /verify de MS-2026-042")
def _corpo_igual(context):
    verify = requests.get(f"{VERIFIER_URL}/verify/{LOTE}", headers={"X-Access-Purpose": PURPOSE}, timeout=60)
    assert verify.status_code == 200, f"GET /verify: esperado 200, veio {verify.status_code}: {verify.text}"
    assert context["response"].json() == verify.json(), "o corpo do acesso difere do GET /verify"


@then("a resposta é 503 com mensagem fixa e sem texto de exceção")
def _503(context):
    resp = context["response"]
    assert resp.status_code == 503, f"esperado 503, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("error") == "service_unavailable" and body.get("error_description"), body
    texto = resp.text.lower()
    for vazamento in ("127.0.0.1", "8001", "localhost", "connection", "refused", "httpconnectionpool",
                      "traceback", "max retries", "errno"):
        assert vazamento not in texto, f"a resposta vaza texto de exceção ({vazamento!r}): {resp.text}"
