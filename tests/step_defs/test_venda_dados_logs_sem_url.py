"""
Passos de tests/features/venda_dados_logs_sem_url.feature (RED: logs e respostas ainda carregam a URL do RPC).

Em processo: `tests/support/load_node_module.py` carrega `coop-a/release_endpoint.py`, que importa (por nome
simples) `delivery`, `data_contract_endpoint` e `withdraw_endpoint` do próprio coop-a; os testes usam essas
instâncias. `caplog` captura o logging. O RPC vem de VENDA_DADOS_RPC_URL (lida a cada chamada, via
`delivery.venda_dados_config()`) com um SENTINELA único por execução, em quatro "redes":
  - três formatos de URL com host inexistente (`.invalid`, que nunca resolve): chave no caminho, na query
    e como senha;
  - rede acessível (node 8546 local) com VENDA_DADOS_CHAIN_ID diferente: exercita a mensagem de
    `delivery._contract()` / `_read_deposit` ("chainId X em <rpc>").

Depende de: Fuseki do coop-a e coop-a (8001, para criar o Contrato de Dados por POST /data-contract) no ar;
node 8546 no ar nos cenários de "chainId errado". Nada é enviado à cadeia (a falha é antes de assinar).

Limpeza: o PRODUCERS_KEY_DIR é um diretório temporário (a chave custodial sintética some com ele); o lote
sintético do retry e a DeliveryTask são apagados no fim; o DataContract (consumidor `ConsumidorTeste-*`) e
DeliveryTask órfãs de `MS-2026-ENTREGA-*` também são limpos pela purga do conftest. `sys.modules` volta como
estava (os imports por nome simples do coop-a não ficam no processo do pytest).
"""
import json
import logging
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
import venda_dados_chain as vc  # noqa: E402
from load_node_module import load_node_module  # noqa: E402

scenarios("../features/venda_dados_logs_sem_url.feature")

SENTINELA = "CHAVE-SINTETICA-" + uuid.uuid4().hex  # um por execução do pytest
HOST_INEXISTENTE = "rpc-inexistente.invalid"
NODE_LOCAL = "127.0.0.1:8546"
ENDERECO_FALSO = "0x" + "11" * 20
CHAIN_ID_ERRADO = 11155111
NOMES_SIMPLES = ("delivery", "data_contract_endpoint", "withdraw_endpoint", "release_endpoint", "shacl_pipeline")

REDES = {  # nome no .feature -> (url, chainId configurado, trecho que não pode aparecer além do sentinela)
    "url com chave no caminho": (f"https://{HOST_INEXISTENTE}/v2/{SENTINELA}", 31337, HOST_INEXISTENTE),
    "chave na query": (f"https://{HOST_INEXISTENTE}/?apikey={SENTINELA}", 31337, HOST_INEXISTENTE),
    "senha no usuário": (f"https://usuario:{SENTINELA}@{HOST_INEXISTENTE}/", 31337, HOST_INEXISTENTE),
    "chainId errado": (f"http://{NODE_LOCAL}/?apikey={SENTINELA}", CHAIN_ID_ERRADO, NODE_LOCAL),
}


@pytest.fixture
def context(monkeypatch, caplog):
    anteriores = {n: sys.modules.get(n) for n in NOMES_SIMPLES}
    for n in NOMES_SIMPLES:  # instâncias novas, lidas com o ambiente deste cenário
        sys.modules.pop(n, None)
    tmp = Path(tempfile.mkdtemp(prefix="logs-sem-url-"))
    monkeypatch.setenv("PRODUCERS_KEY_DIR", str(tmp / "producers"))
    monkeypatch.setenv("VENDA_DADOS_ADDRESS", ENDERECO_FALSO)
    caplog.set_level(logging.INFO)
    ctx = {"mp": monkeypatch, "caplog": caplog, "tmp": tmp, "mods": None, "limpar": []}
    yield ctx
    mods = ctx["mods"]
    if mods:  # lote sintético do retry e DeliveryTask: apaga o que este cenário criou
        for update in ctx["limpar"]:
            try:
                mods["delivery"]._fuseki("update", {"update": update})
            except Exception:  # noqa: BLE001 - Fuseki fora: a purga do conftest tenta de novo
                pass
    shutil.rmtree(tmp, ignore_errors=True)
    for n, antigo in anteriores.items():
        if antigo is None:
            sys.modules.pop(n, None)
        else:
            sys.modules[n] = antigo


def _modulos(ctx):
    """Carrega o coop-a (uma vez por cenário) com o ambiente já ajustado. Devolve {nome: módulo}."""
    if ctx["mods"] is None:
        release = load_node_module("coop-a", "release_endpoint.py", "release_logs_sem_url")
        ctx["mods"] = {"release_endpoint": release, **{n: sys.modules[n] for n in
                                                       ("delivery", "data_contract_endpoint", "withdraw_endpoint")}}
    return ctx["mods"]


@given(parsers.parse('o RPC do VendaDados em "{rede}"'))
def _rede(context, rede):
    url, chain_id, host = REDES[rede]
    context["mp"].setenv("VENDA_DADOS_RPC_URL", url)
    context["mp"].setenv("VENDA_DADOS_CHAIN_ID", str(chain_id))
    context["proibidos"] = [SENTINELA, host]
    if chain_id == CHAIN_ID_ERRADO:  # rede acessível: o erro é o RuntimeError do chainId (saque: a etapa)
        context["tipo"], context["tipo_saque"] = "RuntimeError", "chain_id_diferente"
    else:  # o tipo que o web3 levanta para esta URL: o log precisa continuar dizendo isso
        try:
            Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10})).eth.chain_id
        except Exception as exc:  # noqa: BLE001
            context["tipo"] = context["tipo_saque"] = type(exc).__name__
        else:
            pytest.fail("o RPC inexistente respondeu")


@given("o node local do VendaDados no ar e entregas().call() levantando uma exceção bruta com o sentinela e o host")
def _excecao_bruta(context):
    """`_contract()` passa de verdade (node 8546, chainId 31337); a chamada `entregas().call()` é trocada por
    uma ConnectionError com o sentinela e o host no texto, como a de uma lib que ecoa a URL."""
    import requests

    url = "http://" + NODE_LOCAL
    try:
        Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 5})).eth.chain_id
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"node do VendaDados (8546) fora do ar: {type(exc).__name__}")
    context["mp"].setenv("VENDA_DADOS_RPC_URL", url)
    context["mp"].setenv("VENDA_DADOS_CHAIN_ID", "31337")
    context["proibidos"] = [SENTINELA, HOST_INEXISTENTE]
    context["tipo"] = context["tipo_saque"] = "ConnectionError"
    dele = _modulos(context)["delivery"]
    contrato_real = dele._contract

    class _Entregas:
        def __call__(self, *_):
            return self

        def call(self):
            raise requests.exceptions.ConnectionError(
                f"HTTPSConnectionPool(host='{HOST_INEXISTENTE}', port=443): Max retries exceeded with url: "
                f"/v2/{SENTINELA}")

    class _Functions:
        entregas = _Entregas()

    class _Venda:
        functions = _Functions()

    def _contract_com_entregas_quebrada():
        w3, _ = contrato_real()  # o chainId é conferido de verdade
        return w3, _Venda()

    context["mp"].setattr(dele, "_contract", _contract_com_entregas_quebrada)


# --- Dados sintéticos ---

@given("um lote sintético com quantidade e uma tarefa de entrega pendente")
def _lote_pendente(context):
    mods = _modulos(context)
    dele = mods["delivery"]
    hexa = uuid.uuid4().hex[:12]
    batch, did = f"MS-2026-ENTREGA-{hexa}", f"did:key:zLogsSemUrl{hexa}"
    lote, prod = f"urn:test:logs-lote-{hexa}", f"urn:test:logs-prod-{hexa}"
    dele._fuseki("update", {"update": f"""{dele._PREFIXES}
        INSERT DATA {{
          <{lote}> agritrust:batchNumber "{batch}" ; agritrust:ownedBy <{prod}> ; :quantityMilliliters 1000 .
          <{prod}> agritrust:hasDID "{did}" . }}"""})
    context["limpar"] += [f"DELETE WHERE {{ <{lote}> ?p ?o }} ; DELETE WHERE {{ <{prod}> ?p ?o }}",
                          f"DELETE WHERE {{ <{dele._task_uri(batch)}> ?p ?o }}"]
    dele.write_delivery_task(batch, "pending")
    context.update(batch=batch, did=did, identificador=batch)


@given("um Contrato de Dados sintético")
def _contrato(context):
    _modulos(context)
    context["ref"], _ = vc.post_contract(*vc.window("válida agora"))
    context["identificador"] = context["ref"]


@given("um produtor sintético com carteira custodial")
def _produtor(context):
    dele = _modulos(context)["delivery"]
    context["did"] = f"did:key:zLogsSemUrl{uuid.uuid4().hex[:12]}"
    context["identificador"] = dele.get_or_create_wallet(context["did"])  # chave só no diretório temporário


# --- Ações ---

@when("a entrega do POST /lote de um lote sintético é tentada")
def _entrega(context):
    dele = _modulos(context)["delivery"]
    hexa = uuid.uuid4().hex[:12]
    batch, did = f"MS-2026-ENTREGA-{hexa}", f"did:key:zLogsSemUrl{hexa}"
    context["limpar"].append(f"DELETE WHERE {{ <{dele._task_uri(batch)}> ?p ?o }}")
    context.update(batch=batch, identificador=batch)
    context["resposta"] = dele.deliver_on_post_lote(batch, did, 1000)


@when("o retry de entrega é executado")
def _retry(context):
    context["resposta"] = _modulos(context)["delivery"].retry_delivery()


@when("a leitura do pagamento do contrato é executada")
def _pagamento(context):
    context["resposta"] = _modulos(context)["data_contract_endpoint"].get_data_contract_payment(context["ref"])


@when("a liberação do contrato é executada")
def _liberacao(context):
    context["resposta"] = _modulos(context)["release_endpoint"].release(context["ref"])


@when("o saque do produtor é executado")
def _saque(context):
    context["resposta"] = _modulos(context)["withdraw_endpoint"].withdraw(context["did"])


# --- Verificações ---

def _texto_resposta(resposta) -> str:
    if hasattr(resposta, "body"):  # JSONResponse
        return resposta.body.decode("utf-8")
    return json.dumps(resposta, default=str, ensure_ascii=False)


def _texto_logs(caplog) -> str:
    fmt = logging.Formatter("%(name)s %(levelname)s %(message)s")  # inclui o traceback, se houver
    return "\n".join(fmt.format(r) for r in caplog.records if r.levelno >= logging.INFO)


@then("a resposta e os logs em INFO ou acima não contêm o sentinela nem o host")
def _sem_url(context):
    assert context.get("resposta") is not None, "a ação não devolveu resposta"
    resposta, logs = _texto_resposta(context["resposta"]), _texto_logs(context["caplog"])
    for nome, texto in (("a resposta", resposta), ("os logs", logs)):
        for proibido in context["proibidos"]:
            assert proibido.lower() not in texto.lower(), (
                f"{nome} contém {'o sentinela' if proibido == SENTINELA else 'o host ' + proibido}: "
                f"{_trecho(texto, proibido)}")


def _trecho(texto: str, alvo: str) -> str:
    i = texto.lower().index(alvo.lower())
    return repr(texto[max(0, i - 90):i + len(alvo) + 40])


def _diz(context, tipo: str, identificador: str):
    logs = _texto_logs(context["caplog"])
    assert logs.strip(), "nenhum log em INFO ou acima"
    assert tipo in logs, f"o log não diz o tipo da exceção ({tipo}); logs: {logs[:300]!r}"
    assert identificador in logs, f"o log não traz {identificador}; logs: {logs[:300]!r}"


@when(parsers.parse("{funcao} é chamada diretamente"))
def _chamada_direta(context, funcao):
    mods = _modulos(context)
    try:
        if funcao == "delivery._contract()":
            mods["delivery"]._contract()
        else:
            assert funcao == "data_contract_endpoint._read_deposit()", funcao
            mods["data_contract_endpoint"]._read_deposit("0x" + "ab" * 32)
    except Exception as exc:  # noqa: BLE001 - o que importa é o texto da exceção
        context["excecao"] = exc
    else:
        pytest.fail(f"{funcao} não levantou exceção")


@then("a exceção levantada não contém o sentinela nem o host")
def _excecao_sem_url(context):
    exc = context["excecao"]
    texto = f"{exc} {exc!r} {exc.args!r}"
    for proibido in context["proibidos"]:
        assert proibido.lower() not in texto.lower(), (
            f"{type(exc).__name__} contém {'o sentinela' if proibido == SENTINELA else 'o host ' + proibido}: "
            f"{_trecho(texto, proibido)}")


@then("o log diz o tipo da exceção e o batchNumber")
def _diz_batch(context):
    _diz(context, context["tipo"], context["batch"])


@then("o log diz o tipo da exceção e a ref do contrato")
def _diz_ref(context):
    _diz(context, context["tipo"], context["ref"])


@then("o log diz o tipo da exceção ou a etapa e a carteira")
def _diz_carteira(context):
    _diz(context, context["tipo_saque"], context["identificador"])
