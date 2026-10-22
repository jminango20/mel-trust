"""
Passos de tests/features/chave_cooperativa_separada.feature (RED: a separação ainda não existe).

Em processo, sem subir serviço: `tests/support/load_node_module.py` carrega `coop-a/delivery.py` e o
teste chama `load_coop_account()`. O ambiente é ajustado ANTES do carregamento (as variáveis de chave e
`VENDA_DADOS_CHAIN_ID` são lidas a cada chamada, mas o helper também executa a importação); ele deixa o
ambiente como estava ao entrar (restaura o snapshot tirado DEPOIS dos nossos ajustes), por isso o
`monkeypatch` é quem desfaz as variáveis no fim.

As chaves são geradas aqui com eth_account, escritas num diretório temporário próprio e apagadas ao
fim; nenhuma chave real (coop-a/keys) é lida. Carregar e chamar ficam no mesmo passo e a exceção é
capturada: uma implementação pode recusar já na importação ou só na chamada.
"""
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from eth_account import Account
from pytest_bdd import given, parsers, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "support"))
from load_node_module import load_node_module  # noqa: E402

scenarios("../features/chave_cooperativa_separada.feature")

VARIAVEIS = ("BESU_KEY_PATH", "VENDA_DADOS_COOP_KEY_PATH", "VENDA_DADOS_CHAIN_ID")
CONTEUDO_INVALIDO = "SEGREDO-INVALIDO-zzz-123"


@pytest.fixture
def context(monkeypatch):
    for nome in VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)
    tmp = Path(tempfile.mkdtemp(prefix="chave-coop-teste-"))
    ctx = {"tmp": tmp, "monkeypatch": monkeypatch, "chaves_hex": [], "caminhos": [tmp], "usadas": [],
           "conta": None, "erro": None}
    yield ctx
    shutil.rmtree(tmp, ignore_errors=True)  # as chaves de teste somem ao fim do cenário


def _nova_chave(ctx, nome: str) -> tuple[Path, str]:
    """Gera uma chave, escreve em <tmp>/<nome>-<uuid>.key (hex sem 0x, como coop-a-besu.key)."""
    hex_key = Account.create().key.hex().removeprefix("0x")
    path = ctx["tmp"] / f"{nome}-{uuid.uuid4().hex[:8]}.key"
    path.write_text(hex_key, encoding="utf-8")
    ctx["chaves_hex"].append(hex_key.lower())
    ctx["caminhos"].append(path)
    return path, hex_key


@given("uma chave da Besu gerada pelo teste")
def _besu(context):
    path, hex_key = _nova_chave(context, "besu")
    context.update(besu_path=path, besu_hex=hex_key)
    context["monkeypatch"].setenv("BESU_KEY_PATH", str(path))


@given("uma chave própria da cooperativa gerada pelo teste, em outro arquivo")
def _propria(context):
    path, hex_key = _nova_chave(context, "propria")
    context.update(propria_path=path, propria_hex=hex_key)


@given(parsers.parse("o chainId do VendaDados configurado como {chain_id:d}"))
def _chain(context, chain_id):
    context["monkeypatch"].setenv("VENDA_DADOS_CHAIN_ID", str(chain_id))


@given("VENDA_DADOS_COOP_KEY_PATH apontando para a chave própria")
def _var_propria(context):
    context["monkeypatch"].setenv("VENDA_DADOS_COOP_KEY_PATH", str(context["propria_path"]))


@given("VENDA_DADOS_COOP_KEY_PATH apontando para o mesmo arquivo da Besu")
def _var_mesmo(context):
    context["monkeypatch"].setenv("VENDA_DADOS_COOP_KEY_PATH", str(context["besu_path"]))


@given("VENDA_DADOS_COOP_KEY_PATH apontando para uma cópia da chave da Besu em outro arquivo")
def _var_copia(context):
    copia = context["tmp"] / f"copia-{uuid.uuid4().hex[:8]}.key"
    copia.write_text(context["besu_hex"], encoding="utf-8")
    context["caminhos"].append(copia)
    context["monkeypatch"].setenv("VENDA_DADOS_COOP_KEY_PATH", str(copia))


@given(parsers.parse("{variavel} apontando para um arquivo {estado}"))
def _var_ruim(context, variavel, estado):
    path = context["tmp"] / f"ruim-{uuid.uuid4().hex[:8]}.key"
    context["caminhos"].append(path)
    if estado == "com conteúdo inválido":
        path.write_text(CONTEUDO_INVALIDO, encoding="utf-8")
    else:
        assert estado == "inexistente", estado
    context["monkeypatch"].setenv(variavel, str(path))
    context["conteudo_invalido"] = CONTEUDO_INVALIDO if estado == "com conteúdo inválido" else None


@when("o delivery.py é carregado e a conta da cooperativa é pedida")
def _carregar_e_pedir(context):
    try:
        delivery = load_node_module("coop-a", "delivery.py", "delivery_chave_separada")
        real = delivery.Account

        class _Espia:  # registra toda chave privada que vira conta; delega ao eth_account real
            @staticmethod
            def from_key(chave):
                context["usadas"].append(str(chave).lower().removeprefix("0x"))
                return real.from_key(chave)

        delivery.Account = _Espia
        context["conta"] = delivery.load_coop_account()
    except Exception as exc:  # noqa: BLE001 - o que importa é se recusou e o que a mensagem diz
        context["erro"] = exc


def _conta_de(hex_key: str) -> str:
    return Account.from_key("0x" + hex_key).address


@then("a conta devolvida é a da chave da Besu")
def _eh_besu(context):
    assert context["erro"] is None, f"recusou: {type(context['erro']).__name__}"
    assert context["conta"].address == _conta_de(context["besu_hex"])


@then("a conta devolvida é a da chave própria")
def _eh_propria(context):
    assert context["erro"] is None, f"recusou: {type(context['erro']).__name__}"
    assert context["conta"].address == _conta_de(context["propria_hex"]), (
        f"devolveu {context['conta'].address}; a da chave própria é {_conta_de(context['propria_hex'])}"
        f" e a da Besu é {_conta_de(context['besu_hex'])}")


@then("a conta devolvida não é a da chave da Besu")
def _nao_besu(context):
    assert context["conta"] is not None
    assert context["conta"].address != _conta_de(context["besu_hex"])


def _mensagem(context) -> str:
    exc = context["erro"]
    return f"{type(exc).__name__}: {exc} {exc.args!r}"


@then(parsers.parse("a conta é recusada com um erro que cita {variavel}"))
def _recusada(context, variavel):
    assert context["erro"] is not None, (
        f"não recusou: devolveu a conta {context['conta'].address if context['conta'] else None}")
    assert variavel in _mensagem(context), f"a mensagem não cita {variavel}: {type(context['erro']).__name__}"


@then("a chave da Besu não foi usada")
def _besu_nao_usada(context):
    assert context["besu_hex"].lower() not in context["usadas"], "a chave da Besu virou conta mesmo recusando"


@then("a mensagem de erro não contém a chave em hex nem o caminho de arquivo algum")
def _sem_vazamento(context):
    assert context["erro"] is not None, "sem erro para examinar (nada foi recusado)"
    msg = _mensagem(context).lower()
    for hex_key in context["chaves_hex"]:
        assert hex_key not in msg, "a mensagem contém uma chave em hex"
    for caminho in context["caminhos"]:
        for forma in (str(caminho), str(caminho).replace("\\", "\\\\"), caminho.as_posix(), caminho.name):
            assert forma.lower() not in msg, f"a mensagem contém um caminho ({caminho.name})"


@then("a mensagem de erro não contém o endereço completo da cooperativa")
def _sem_endereco(context):
    assert context["erro"] is not None, "sem erro para examinar (nada foi recusado)"
    msg = _mensagem(context).lower()
    for hex_key in context["chaves_hex"]:
        endereco = _conta_de(hex_key).lower()
        assert endereco not in msg and endereco.removeprefix("0x") not in msg, "a mensagem contém um endereço completo"


@then("a mensagem de erro não contém o conteúdo do arquivo")
def _sem_conteudo(context):
    assert context["erro"] is not None, "sem erro para examinar (nada foi recusado)"
    if context.get("conteudo_invalido"):
        assert context["conteudo_invalido"].lower() not in _mensagem(context).lower()
