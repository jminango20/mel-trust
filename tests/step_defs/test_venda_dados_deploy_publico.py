"""
Passos de tests/features/venda_dados_deploy_publico.feature (RED: o script deploy-publico.js não existe).

O script é chamado como PROCESSO (`node contracts/venda-dados/scripts/deploy-publico.js`) só com variáveis de
ambiente. As chaves de teste são geradas aqui com eth_account, num diretório temporário apagado ao fim (as duas
chaves PÚBLICAS do Hardhat, usadas nos casos de recusa, também só existem nesse diretório).

Nenhuma rede real:
  - casos de recusa: DEPLOY_RPC_URL aponta para uma porta onde NADA escuta; recusar "antes de conectar" é provado
    por o erro não ser de conexão (nem de módulo ausente) e pela mensagem citar a causa;
  - casos de integração: o PRÓPRIO teste sobe `npx hardhat node --chain-id 424242 --port <livre>` (fixture de
    módulo, ~2 s, encerrado ao fim com taskkill da árvore) e financia a chave de teste a partir da conta nº 0
    DO NODE (só no teste; o script nunca usa essa conta).
Sem Besu, sem Fuseki, sem coop-a. O arquivo deployments/<rede>.json de cada rede de teste é apagado ao fim.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import requests
from eth_account import Account
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDA_DIR = REPO_ROOT / "contracts" / "venda-dados"
SCRIPT = VENDA_DIR / "scripts" / "deploy-publico.js"
DEPLOYMENTS = VENDA_DIR / "deployments"
LOCAL_JSON = DEPLOYMENTS / "local.json"

scenarios("../features/venda_dados_deploy_publico.feature")

SENTINELA = "CHAVE-SINTETICA-" + uuid.uuid4().hex
CHAIN_ID_NODE = 424242
HARDHAT_0 = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"  # chaves PÚBLICAS do node de dev
HARDHAT_1 = "59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
VARS_DO_SCRIPT = ("DEPLOY_REDE", "DEPLOY_RPC_URL", "DEPLOY_CHAIN_ID_ESPERADO", "VENDA_DADOS_COOP_KEY_PATH",
                  "PARTE_COOPERATIVA_PERCENTUAL", "PRAZO_REEMBOLSO_S", "BESU_KEY_PATH")

# o que a mensagem de recusa tem de citar (a causa, sem caminho nem chave)
CITA = {
    "chave igual à conta nº 0 do Hardhat": "hardhat", "chave igual à conta nº 1 do Hardhat": "hardhat",
    "chainId esperado 31337": "31337", "DEPLOY_REDE local": "DEPLOY_REDE", "DEPLOY_REDE com maiúscula": "DEPLOY_REDE",
    "DEPLOY_REDE com barra": "DEPLOY_REDE", "DEPLOY_REDE com ponto": "DEPLOY_REDE",
    "chave ausente": "VENDA_DADOS_COOP_KEY_PATH", "chave inexistente": "VENDA_DADOS_COOP_KEY_PATH",
    "chave com conteúdo inválido": "VENDA_DADOS_COOP_KEY_PATH", "chave com o mesmo endereço da Besu": "BESU_KEY_PATH",
    "percentual 101": "PARTE_COOPERATIVA_PERCENTUAL", "percentual -1": "PARTE_COOPERATIVA_PERCENTUAL",
    "percentual abc": "PARTE_COOPERATIVA_PERCENTUAL", "arquivo da rede já existe": "existe",
}
MARCADORES_DE_CONEXAO = ("econnrefused", "econnreset", "fetch failed", "failed to detect network",
                         "could not detect network", "network_error", "server_error", "getaddrinfo", "timeout")
MARCADORES_DE_MODULO = ("cannot find module", "module_not_found")


# --- Infra ---

def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _formatos(porta: int) -> list[tuple[str, str]]:
    return [("sentinela no caminho", f"http://127.0.0.1:{porta}/v2/{SENTINELA}"),
            ("sentinela na query", f"http://127.0.0.1:{porta}/?apikey={SENTINELA}"),
            ("sentinela na senha", f"http://usuario:{SENTINELA}@127.0.0.1:{porta}/")]


@pytest.fixture(scope="module")
def node_local():
    """Hardhat node em porta livre, chainId 424242. Sobe sob demanda (só os cenários de integração o pedem)."""
    porta = _porta_livre()
    npx = "npx.cmd" if os.name == "nt" else "npx"
    proc = subprocess.Popen([npx, "hardhat", "node", "--chain-id", str(CHAIN_ID_NODE), "--port", str(porta)],
                            cwd=VENDA_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{porta}"
    try:
        for _ in range(60):
            try:
                if requests.post(url, json={"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1},
                                 timeout=2).ok:
                    break
            except requests.RequestException:
                time.sleep(0.5)
        else:
            pytest.fail("o node local de teste (chainId 424242) não subiu em 30 s")
        yield {"porta": porta, "url": url, "w3": Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))}
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.kill()


@pytest.fixture
def context():
    tmp = Path(tempfile.mkdtemp(prefix="deploy-publico-"))
    ctx = {"tmp": tmp, "hexes": [], "caminhos": [tmp], "redes": [], "rodadas": [], "porta_morta": _porta_livre()}
    yield ctx
    shutil.rmtree(tmp, ignore_errors=True)
    for rede in ctx["redes"]:
        (DEPLOYMENTS / f"{rede}.json").unlink(missing_ok=True)


def _chave(ctx, nome: str, hex_key: str | None = None) -> tuple[str, Path]:
    hex_key = hex_key or Account.create().key.hex().removeprefix("0x")
    caminho = ctx["tmp"] / f"{nome}-{uuid.uuid4().hex[:6]}.key"
    caminho.write_text(hex_key, encoding="utf-8")
    ctx["hexes"].append(hex_key.lower())
    ctx["caminhos"].append(caminho)
    return hex_key, caminho


def _rede_nova(ctx) -> str:
    rede = f"teste-{uuid.uuid4().hex[:10]}"
    ctx["redes"].append(rede)
    return rede


def _roda(ctx, env_extra: dict, rpc_url: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in VARS_DO_SCRIPT}
    env.update({k: v for k, v in env_extra.items() if v is not None})
    env["DEPLOY_RPC_URL"] = rpc_url
    proc = subprocess.run(["node", str(SCRIPT)], cwd=VENDA_DIR, env=env, capture_output=True, timeout=180)
    saida = (proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))
    return {"rc": proc.returncode, "stdout": saida[0], "stderr": saida[1], "texto": saida[0] + "\n" + saida[1],
            "rpc_url": rpc_url}


# --- Casos de recusa ---

@given(parsers.parse('o caso de recusa "{caso}"'))
def _caso_recusa(context, caso):
    rede = _rede_nova(context)
    env = {"DEPLOY_REDE": rede, "DEPLOY_CHAIN_ID_ESPERADO": "11155111", "PARTE_COOPERATIVA_PERCENTUAL": "10"}
    valida, caminho_valida = _chave(context, "coop")
    env["VENDA_DADOS_COOP_KEY_PATH"] = str(caminho_valida)
    if caso == "chave igual à conta nº 0 do Hardhat":
        env["VENDA_DADOS_COOP_KEY_PATH"] = str(_chave(context, "hh0", HARDHAT_0)[1])
    elif caso == "chave igual à conta nº 1 do Hardhat":
        env["VENDA_DADOS_COOP_KEY_PATH"] = str(_chave(context, "hh1", HARDHAT_1)[1])
    elif caso == "chainId esperado 31337":
        env["DEPLOY_CHAIN_ID_ESPERADO"] = "31337"
    elif caso.startswith("DEPLOY_REDE "):
        env["DEPLOY_REDE"] = {"local": "local", "com maiúscula": "Teste1", "com barra": "a/b", "com ponto": "a.b"}[
            caso.removeprefix("DEPLOY_REDE ")]
    elif caso == "chave ausente":
        env.pop("VENDA_DADOS_COOP_KEY_PATH")
    elif caso == "chave inexistente":
        env["VENDA_DADOS_COOP_KEY_PATH"] = str(context["tmp"] / "nao-existe.key")
        context["caminhos"].append(context["tmp"] / "nao-existe.key")
    elif caso == "chave com conteúdo inválido":
        ruim = context["tmp"] / "ruim.key"
        ruim.write_text("CONTEUDO-INVALIDO-nao-e-uma-chave", encoding="utf-8")
        context["caminhos"].append(ruim)
        context["conteudo_invalido"] = "CONTEUDO-INVALIDO-nao-e-uma-chave"
        env["VENDA_DADOS_COOP_KEY_PATH"] = str(ruim)
    elif caso == "chave com o mesmo endereço da Besu":
        env["BESU_KEY_PATH"] = str(_chave(context, "besu-copia", valida)[1])  # outro arquivo, mesma chave
    elif caso.startswith("percentual "):
        env["PARTE_COOPERATIVA_PERCENTUAL"] = caso.removeprefix("percentual ")
    elif caso == "arquivo da rede já existe":
        DEPLOYMENTS.mkdir(exist_ok=True)
        existente = DEPLOYMENTS / f"{rede}.json"
        existente.write_bytes(b'{"anterior": true}\n')
        context["existente"] = (existente, b'{"anterior": true}\n')
    else:
        pytest.fail(f"caso desconhecido: {caso}")
    context.update(caso=caso, env=env)


@when("o script de implantação é executado com cada um dos 3 formatos de URL do RPC")
def _roda_3_formatos_recusa(context):
    for nome, url in _formatos(context["porta_morta"]):
        context["rodadas"].append({"formato": nome, **_roda(context, context["env"], url)})


# --- Integração ---

@given("um node local de teste com chainId 424242 em outra porta")
def _node(context, node_local):
    context["node"] = node_local
    context["porta_node"] = node_local["porta"]


def _financia(ctx, wei: int) -> tuple[Account, Path, str]:
    hex_key, caminho = _chave(ctx, "coop")
    conta = Account.from_key("0x" + hex_key)
    w3 = ctx["node"]["w3"]
    if wei:
        w3.eth.wait_for_transaction_receipt(
            w3.eth.send_transaction({"from": w3.eth.accounts[0], "to": conta.address, "value": wei}))
    return conta, caminho, hex_key


def _anota_estado(ctx):
    w3 = ctx["node"]["w3"]
    ctx["antes"] = (w3.eth.get_transaction_count(ctx["conta"].address), w3.eth.block_number)


@given(parsers.parse("uma chave de teste com 10 ETH no node"))
def _chave_10eth(context):
    context["conta"], context["caminho_chave"], _ = _financia(context, 10 * 10**18)
    _anota_estado(context)


@given(parsers.parse("uma chave de teste com {wei:d} wei no node"))
def _chave_wei(context, wei):
    context["conta"], context["caminho_chave"], _ = _financia(context, wei)
    _anota_estado(context)


def _env_integracao(ctx, rede, caminho, chain_esperado=CHAIN_ID_NODE, **extra):
    return {"DEPLOY_REDE": rede, "DEPLOY_CHAIN_ID_ESPERADO": str(chain_esperado),
            "VENDA_DADOS_COOP_KEY_PATH": str(caminho), **extra}


@when(parsers.parse("o script de implantação é executado esperando o chainId {esperado:d}, com cada um dos 3 formatos de URL do RPC"))
def _roda_chain_errado(context, esperado):
    for nome, url in _formatos(context["porta_node"]):
        env = _env_integracao(context, _rede_nova(context), context["caminho_chave"], chain_esperado=esperado)
        context["rodadas"].append({"formato": nome, **_roda(context, env, url)})


@when("o script de implantação é executado com cada um dos 3 formatos de URL do RPC, no chainId 424242")
def _roda_saldo(context):
    for nome, url in _formatos(context["porta_node"]):
        env = _env_integracao(context, _rede_nova(context), context["caminho_chave"])
        context["rodadas"].append({"formato": nome, **_roda(context, env, url)})


@given("o conteúdo atual de deployments/local.json foi anotado")
def _anota_local(context):
    context["local_antes"] = LOCAL_JSON.read_bytes() if LOCAL_JSON.exists() else None


# (percentual, prazo): explícitos, padrão (variáveis ausentes) e extremo
PARAMETROS = [("15", "86400"), (None, None), ("100", "3600")]


@when("o script de implantação é executado com sucesso em 3 redes de nomes novos, nos 3 formatos de URL")
def _roda_sucesso(context):
    for (nome, url), (parte, prazo) in zip(_formatos(context["porta_node"]), PARAMETROS):
        conta, caminho, _ = _financia(context, 10 * 10**18)
        rede = _rede_nova(context)
        env = _env_integracao(context, rede, caminho, PARTE_COOPERATIVA_PERCENTUAL=parte, PRAZO_REEMBOLSO_S=prazo)
        rodada = _roda(context, env, url)
        context["rodadas"].append({"formato": nome, "rede": rede, "conta": conta,
                                   "parte": int(parte) if parte else 0, "prazo": int(prazo) if prazo else 2592000,
                                   **rodada})


@given("o script de implantação foi executado com sucesso")
def _deploy_ok(context):
    conta, caminho, hex_key = _financia(context, 10 * 10**18)
    rede = _rede_nova(context)
    rodada = _roda(context, _env_integracao(context, rede, caminho, PARTE_COOPERATIVA_PERCENTUAL="10"),
                   _formatos(context["porta_node"])[0][1])
    assert rodada["rc"] == 0, f"o deploy de preparação falhou (rc {rodada['rc']}): {rodada['texto'][:300]}"
    dados = json.loads((DEPLOYMENTS / f"{rede}.json").read_text(encoding="utf-8"))
    context.update(conta=conta, deploy=dados, rodadas=[rodada])


_ABI = [
    {"type": "function", "name": "cooperativa", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "parteCooperativaPercentual", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "prazoReembolso", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "usdt", "stateMutability": "view", "inputs": [],
     "outputs": [{"name": "", "type": "address"}]},
    {"type": "function", "name": "liberar", "stateMutability": "nonpayable",
     "inputs": [{"name": "ref", "type": "bytes32"}], "outputs": []},
]


@when("a chave da implantação chama liberar de um ref sem depósito")
def _coop_libera(context):
    context["ref"] = Web3.keccak(text=uuid.uuid4().hex)
    context["erro_coop"] = _seletor_do_revert(context, context["conta"].address)


@when("uma chave diferente chama liberar do mesmo ref")
def _outro_libera(context):
    outro = Account.create()
    context["erro_outro"] = _seletor_do_revert(context, outro.address)


def _seletor_do_revert(ctx, remetente: str) -> str:
    w3 = ctx["node"]["w3"]
    venda = w3.eth.contract(address=Web3.to_checksum_address(ctx["deploy"]["vendaDados"]), abi=_ABI)
    try:
        venda.functions.liberar(ctx["ref"]).call({"from": remetente})
    except Exception as exc:  # noqa: BLE001 - o selector do erro customizado vem em args[0]
        return str(exc.args[0])[:10].lower() if exc.args else ""
    return "sem-revert"


# --- Verificações ---

def _vazamentos(ctx, texto: str) -> list[str]:
    porta = ctx.get("porta_node") or ctx["porta_morta"]
    proibidos = {"o sentinela": SENTINELA, "o host": "127.0.0.1", "a porta do RPC": f":{porta}"}
    for hex_key in ctx["hexes"]:
        proibidos[f"a chave {hex_key[:6]}…"] = hex_key
    for caminho in ctx["caminhos"]:
        proibidos[f"o caminho {Path(caminho).name}"] = str(caminho)
        proibidos[f"o nome {Path(caminho).name}"] = Path(caminho).name
    if ctx.get("conteudo_invalido"):
        proibidos["o conteúdo do arquivo"] = ctx["conteudo_invalido"]
    t = texto.lower()
    return [nome for nome, valor in proibidos.items() if valor.lower() in t]


@then("o script recusa com código de saída diferente de zero e uma mensagem clara")
def _recusa(context):
    assert context["rodadas"], "nenhuma rodada"
    token = CITA.get(context.get("caso"))
    for r in context["rodadas"]:
        assert r["rc"] != 0, f"[{r['formato']}] saiu com 0 (esperado recusar)"
        assert r["texto"].strip(), f"[{r['formato']}] recusou sem mensagem"
        if token and not any(m in r["texto"].lower() for m in MARCADORES_DE_MODULO):
            assert token.lower() in r["texto"].lower(), (
                f"[{r['formato']}] a mensagem não cita {token!r}: {r['texto'][:200]!r}")


@then(parsers.parse('o script recusa com código de saída diferente de zero e uma mensagem que cita "{token}"'))
def _recusa_cita(context, token):
    for r in context["rodadas"]:
        assert r["rc"] != 0, f"[{r['formato']}] saiu com 0 (esperado recusar)"
        assert not any(m in r["texto"].lower() for m in MARCADORES_DE_MODULO), (
            f"[{r['formato']}] o script não existe: {r['texto'][:160]!r}")
        assert token.lower() in r["texto"].lower(), f"[{r['formato']}] a mensagem não cita {token!r}: {r['texto'][:200]!r}"


@then("o erro não é de conexão nem de módulo ausente")
def _nao_e_conexao(context):
    for r in context["rodadas"]:
        baixo = r["texto"].lower()
        for m in MARCADORES_DE_MODULO:
            assert m not in baixo, f"[{r['formato']}] o script não existe (módulo ausente): {r['texto'][:160]!r}"
        for m in MARCADORES_DE_CONEXAO:
            assert m not in baixo, f"[{r['formato']}] o erro é de conexão ({m}): {r['texto'][:200]!r}"
    if "existente" in context:
        arquivo, bytes_antes = context["existente"]
        assert arquivo.read_bytes() == bytes_antes, "o arquivo da rede existente foi sobrescrito"


@then("a saída não contém o sentinela, o host, a chave em hex nem o caminho do arquivo da chave")
def _sem_vazamento(context):
    for r in context["rodadas"]:
        achados = _vazamentos(context, r["texto"])
        assert not achados, f"[{r['formato']}] a saída contém {', '.join(achados)}"


@then("o nonce da chave e o número de blocos do node não mudaram")
def _sem_transacao(context):
    w3 = context["node"]["w3"]
    depois = (w3.eth.get_transaction_count(context["conta"].address), w3.eth.block_number)
    assert depois == context["antes"], f"nonce/blocos antes {context['antes']}, depois {depois}"


@then("o VendaDados de cada implantação tem cooperativa igual ao endereço da chave, o percentual e o prazo das variáveis")
def _contratos(context):
    w3 = context["node"]["w3"]
    assert len(context["rodadas"]) == 3
    for r in context["rodadas"]:
        assert r["rc"] == 0, f"[{r['formato']}] rc {r['rc']}: {r['texto'][:300]!r}"
        dados = json.loads((DEPLOYMENTS / f"{r['rede']}.json").read_text(encoding="utf-8"))
        venda = w3.eth.contract(address=Web3.to_checksum_address(dados["vendaDados"]), abi=_ABI)
        assert venda.functions.cooperativa().call() == r["conta"].address
        assert venda.functions.parteCooperativaPercentual().call() == r["parte"]
        assert venda.functions.prazoReembolso().call() == r["prazo"]
        assert venda.functions.usdt().call() == Web3.to_checksum_address(dados["usdt"])


@then("o arquivo da rede tem rede, chainId, cooperativa, usdt, vendaDados, blocos e hashes das transações e nenhuma URL, host nem chave")
def _arquivo_da_rede(context):
    w3 = context["node"]["w3"]
    for r in context["rodadas"]:
        bruto = (DEPLOYMENTS / f"{r['rede']}.json").read_text(encoding="utf-8")
        dados = json.loads(bruto)
        for campo in ("rede", "chainId", "cooperativa", "usdt", "vendaDados", "blocoUsdt", "blocoVendaDados",
                      "txUsdt", "txVendaDados"):
            assert campo in dados, f"[{r['formato']}] falta o campo {campo} em {sorted(dados)}"
        assert dados["rede"] == r["rede"] and dados["chainId"] == CHAIN_ID_NODE
        assert dados["cooperativa"] == r["conta"].address
        assert w3.eth.get_transaction_receipt(dados["txUsdt"]).contractAddress == dados["usdt"]
        assert w3.eth.get_transaction_receipt(dados["txVendaDados"]).contractAddress == dados["vendaDados"]
        assert w3.eth.get_transaction_receipt(dados["txUsdt"]).blockNumber == dados["blocoUsdt"]
        assert w3.eth.get_transaction_receipt(dados["txVendaDados"]).blockNumber == dados["blocoVendaDados"]
        assert not _vazamentos(context, bruto), f"[{r['formato']}] o arquivo contém {_vazamentos(context, bruto)}"
        assert "http" not in bruto.lower() and "rpc" not in bruto.lower(), "o arquivo da rede contém URL/RPC"


@then("o deployments/local.json tem os mesmos bytes de antes")
def _local_intacto(context):
    atual = LOCAL_JSON.read_bytes() if LOCAL_JSON.exists() else None
    assert atual == context["local_antes"], "o deployments/local.json mudou"


@then("o stdout traz os endereços e o aviso de que o MockUSDT tem mint aberto, só para teste")
def _stdout(context):
    for r in context["rodadas"]:
        dados = json.loads((DEPLOYMENTS / f"{r['rede']}.json").read_text(encoding="utf-8"))
        assert dados["usdt"] in r["stdout"] and dados["vendaDados"] in r["stdout"], f"[{r['formato']}] faltam endereços"
        baixo = r["stdout"].lower()
        assert "mint" in baixo and "mockusdt" in baixo and "teste" in baixo, f"[{r['formato']}] falta o aviso do mint"


def _sel(erro: str) -> str:
    return Web3.keccak(text=erro)[:4].hex().removeprefix("0x").lower()


@then("a chave da implantação reverte com SemDeposito e não com NaoEhCooperativa")
def _coop_reverte_sem_deposito(context):
    erro = context["erro_coop"].removeprefix("0x")
    assert erro != _sel("NaoEhCooperativa()"), "a chave da implantação NÃO é a cooperativa do contrato"
    assert erro == _sel("SemDeposito(bytes32)"), f"selector {erro}"


@then("a chave diferente reverte com NaoEhCooperativa")
def _outro_reverte(context):
    assert context["erro_outro"].removeprefix("0x") == _sel("NaoEhCooperativa()"), context["erro_outro"]
