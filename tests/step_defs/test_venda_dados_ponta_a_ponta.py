"""
Step defs (pytest-bdd) para tests/features/venda_dados_ponta_a_ponta.feature.

Teste de ponta a ponta da venda de dados, SÓ em tests/: não altera código de produção. Usa as peças
reais: POST /lote (coop-a), POST /data-contract, desafio e acesso (verifier-demo), depósito direto no
VendaDados (o comprador), POST .../release e POST /producers/{did}/withdraw (coop-a).

Pré-condições (nada é subido pelo teste): coop-a (8001) e verifier-demo (8004) no ar, com o código
atual; Fuseki do coop-a (3030) e a federação (coop-b 3031, certifier 3032) para o GET /verify; túnel da
Besu em 127.0.0.1:8545 (POST /lote ancora o lote; o conftest já exige o túnel em todo teste que não é
@sem_besu); node Hardhat do VendaDados em 8546 com o deploy feito (o cenário troca o
deployments/local.json por um deploy próprio com parte da cooperativa de 10% e o restaura no fim).

Contas e dados: comprador = conta nº 1 do Hardhat (chave pública conhecida, só rede de teste);
consumidor `ConsumidorTeste-<12 hex>`; lotes `MS-2026-VENDA-<A|B>-<8 hex>` (prefixo na allowlist do
conftest) e producerDID novo por execução. Limpeza: o teste apaga os lotes, as DeliveryTask e os
produtores do Fuseki do coop-a e as duas chaves custodiais; a purga do conftest remove o Contrato de
Dados e as AnchoringTask/DeliveryTask órfãs. Se um passo falhar, o teste só reporta (sem corrigir).
"""
import functools
import hashlib
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from eth_account import Account
from eth_account.messages import encode_defunct
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
import venda_dados_chain as vc  # noqa: E402
from venda_dados_deploy import deploy_proprio  # noqa: E402

scenarios("../features/venda_dados_ponta_a_ponta.feature")

VERIFIER_URL = "http://127.0.0.1:8004"
PURPOSE = "Compliance EUDR"
PRICE_UNITS = 10_000_000
PRODUCERS_KEY_DIR = REPO_ROOT / "coop-a" / "keys" / "producers"
BEE = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"
_PREFIX = f"PREFIX : <{BEE}>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"
_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
FUSEKI_URL = _env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
FUSEKI_PASSWORD = _env.get("FUSEKI_ADMIN_PASSWORD")


@pytest.fixture(autouse=True)
def _preflight():
    """verifier-demo e coop-a no ar; node 8546 com deploy."""
    for nome, url in (("verifier-demo", VERIFIER_URL), ("coop-a", vc.COOP_A_URL)):
        try:
            assert requests.get(f"{url}/docs" if nome == "coop-a" else f"{url}/health", timeout=5).status_code == 200
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{nome} não respondeu em {url}: {exc}")
    dep = vc.deploy()
    try:
        w3 = Web3(Web3.HTTPProvider(dep["rpcUrl"], request_kwargs={"timeout": 10}))
        chain_id, code = w3.eth.chain_id, w3.eth.get_code(Web3.to_checksum_address(dep["vendaDados"]))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"node do VendaDados não respondeu em {dep['rpcUrl']}: {exc}. Suba: "
                    "cd contracts/venda-dados && npx hardhat node --port 8546, depois node scripts/deploy-local.js")
    if chain_id != 31337 or len(code) == 0:
        pytest.fail(f"chainId {chain_id} ou sem contrato em {dep['vendaDados']} (node reiniciado? rode o deploy)")


def _fuseki(path, data, **kw):
    r = requests.post(f"{FUSEKI_URL}/{path}", data=data, auth=("admin", FUSEKI_PASSWORD), timeout=20, **kw)
    r.raise_for_status()
    return r


def _key_path(did):
    return PRODUCERS_KEY_DIR / (hashlib.sha256(did.encode()).hexdigest() + ".key")


@pytest.fixture
def context():
    ctx = {"lotes": {}, "producers": {}, "usdt_antes": {}}
    yield ctx
    if "local_json_original" in ctx:
        vc.DEPLOY_JSON.write_bytes(ctx["local_json_original"])
    for lote in ctx["lotes"].values():
        _fuseki("update", {"update": f"""{_PREFIX}
        DELETE WHERE {{ ?l agritrust:batchNumber "{lote}" . ?l ?p ?o }} ;
        DELETE WHERE {{ ?t :deliveryBatch "{lote}" . ?t ?p ?o }}"""})
    for did in ctx["producers"].values():
        _fuseki("update", {"update": f"""{_PREFIX}
        DELETE WHERE {{ ?p agritrust:hasDID "{did}" . ?p ?x ?y }}"""})
        _key_path(did).unlink(missing_ok=True)


def _wallet(context, quem):
    did = context["producers"][quem]
    path = _key_path(did)
    assert path.exists(), f"carteira custodial do produtor {quem} não existe em {path}"
    return Account.from_key("0x" + path.read_text().strip()).address


def _challenge_and_get(context, lote):
    """Pede um desafio, assina com o comprador e pede o acesso (o nonce vale uma tentativa)."""
    ref = context["ref"]
    resp = requests.post(f"{VERIFIER_URL}/data-access/challenge/{ref}", timeout=30)
    assert resp.status_code == 200, f"POST challenge: esperado 200, veio {resp.status_code}: {resp.text}"
    ch = resp.json()
    sig = Account.sign_message(encode_defunct(text=ch["message"]), private_key=vc.BUYER_KEY).signature.hex().removeprefix("0x")
    headers = {"X-Data-Contract": ref, "X-Challenge-Nonce": ch["nonce"],
               "X-Challenge-Signature": "0x" + sig, "X-Access-Purpose": PURPOSE}
    context["response"] = requests.get(f"{VERIFIER_URL}/data-access/{lote}", headers=headers, timeout=60)


@given("um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário")
def _deploy_proprio(context):
    context["local_json_original"] = deploy_proprio(10)


@given(parsers.parse("o produtor {quem} entregou um lote sintético de {litros} litros por POST /lote"))
def _entrega(context, quem, litros):
    suffix = uuid.uuid4().hex[:8]
    lote, did = f"MS-2026-VENDA-{quem}-{suffix}", f"did:key:zTesteVenda{quem}{suffix}"
    context["lotes"][quem], context["producers"][quem] = lote, did
    body = {"batchNumber": lote, "productType": "HoneyApis", "producerName": f"Produtor Teste Venda {quem}",
            "producerDID": did, "quantityLiters": float(litros)}
    resp = requests.post(f"{vc.COOP_A_URL}/lote", json=body, timeout=90)
    assert resp.status_code == 200, f"POST /lote ({quem}): esperado 200, veio {resp.status_code}: {resp.text}"
    out = resp.json()
    assert out.get("anchoringStatus") == "anchored", f"POST /lote ({quem}): anchoringStatus: {out}"
    assert out.get("deliveryStatus") == "registered", f"POST /lote ({quem}): deliveryStatus: {out}"


@given(parsers.parse("um Contrato de Dados de {preco:d} unidades criado por POST /data-contract"))
def _contrato(context, preco):
    assert preco == PRICE_UNITS
    context["ref"], _ = vc.post_contract(*vc.window("válida agora"), price=preco)
    context["ref_bytes"] = bytes.fromhex(context["ref"].removeprefix("0x"))


@given("o comprador deposita o preço exato com a ref do contrato")
@when("o comprador deposita o preço exato com a ref do contrato")
def _deposito(context):
    vc.deposit(context["ref"], Account.from_key(vc.BUYER_KEY), PRICE_UNITS)


@when("a cooperativa reembolsa o depósito direto no contrato")
def _reembolso(context):
    w3, venda, _ = vc.chain()
    vc.send(w3, vc.coop(), venda.functions.reembolsar(context["ref_bytes"]))


@when("o comprador pede e assina um desafio e pede o acesso ao lote do produtor A")
def _acesso_lote_a(context):
    _challenge_and_get(context, context["lotes"]["A"])


@when("o coop-a recebe POST /data-contract/<contractRef>/release")
def _liberar(context):
    context["response"] = requests.post(f"{vc.COOP_A_URL}/data-contract/{context['ref']}/release", timeout=60)


@when(parsers.parse("o coop-a recebe POST /producers/<producerDID>/withdraw do produtor {quem}"))
def _sacar(context, quem):
    _, _, usdt = vc.chain()
    context["usdt_antes"][quem] = usdt.functions.balanceOf(_wallet(context, quem)).call()
    context["response"] = requests.post(f"{vc.COOP_A_URL}/producers/{context['producers'][quem]}/withdraw", timeout=90)


@then(parsers.parse("a resposta é {codigo:d}"))
def _status(context, codigo):
    resp = context["response"]
    assert resp.status_code == codigo, f"esperado {codigo}, veio {resp.status_code}: {resp.text}"


@then(parsers.parse('a resposta é 200 com status "{status}"'))
def _200_status(context, status):
    resp = context["response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, f"esperado status={status!r}, veio {resp.json()}"


@then(parsers.parse('a resposta é 200 com status "withdrawn" e amount {amount:d}'))
def _withdrawn(context, amount):
    resp = context["response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("status") == "withdrawn" and body.get("amount") == amount, f"esperado withdrawn/{amount}, veio {body}"
    assert len(body.get("txHash", "")) == 66, body


@then(parsers.parse('a resposta é 402 com error "{error}" e status "{status}"'))
def _402(context, error, status):
    resp = context["response"]
    assert resp.status_code == 402, f"esperado 402, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("error") == error and body.get("status") == status, f"esperado {error}/{status}, veio {body}"


@then(parsers.parse('GET /data-contract/<contractRef>/payment responde "{status}"'))
def _get_payment(context, status):
    resp = requests.get(f"{vc.COOP_A_URL}/data-contract/{context['ref']}/payment", timeout=30)
    assert resp.status_code == 200, f"GET payment: esperado 200, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("status") == status, resp.json()


@then("o corpo é igual ao do GET /verify do lote do produtor A")
def _corpo_igual(context):
    lote = context["lotes"]["A"]
    verify = requests.get(f"{VERIFIER_URL}/verify/{lote}", headers={"X-Access-Purpose": PURPOSE}, timeout=60)
    assert verify.status_code == 200, f"GET /verify: esperado 200, veio {verify.status_code}: {verify.text}"
    assert context["response"].json() == verify.json(), "o corpo do acesso difere do GET /verify"


def _total(conta):
    _, venda, _ = vc.chain()
    return venda.functions.saldoTotalSacavel(conta).call()


@then(parsers.parse("o saldoTotalSacavel do produtor A é {a:d} e o do produtor B é {b:d}"))
def _saldos_produtores(context, a, b):
    got = (_total(_wallet(context, "A")), _total(_wallet(context, "B")))
    assert got == (a, b), f"saldoTotalSacavel A/B = {got}, esperado {(a, b)}"


@then(parsers.parse("o saldoTotalSacavel da cooperativa é {coop:d} e a soma dos três é {soma:d}"))
def _saldo_coop_e_soma(context, coop, soma):
    a, b, c = _total(_wallet(context, "A")), _total(_wallet(context, "B")), _total(vc.coop().address)
    assert c == coop, f"saldoTotalSacavel da cooperativa = {c}, esperado {coop}"
    assert a + b + c == soma, f"soma {a}+{b}+{c} = {a + b + c}, esperado {soma}"


@then(parsers.parse("o saldo de USDT da carteira custodial do produtor {quem} sobe exatamente {amount:d}"))
def _usdt_sobe(context, quem, amount):
    _, _, usdt = vc.chain()
    depois = usdt.functions.balanceOf(_wallet(context, quem)).call()
    assert depois - context["usdt_antes"][quem] == amount, \
        f"USDT do produtor {quem}: {context['usdt_antes'][quem]} -> {depois}, esperado +{amount}"
