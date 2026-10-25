"""
Testes de scripts/blockchain/gerar_chaves_rede_publica.py. O script não conecta a rede nenhuma: roda como
PROCESSO (`--saida` aponta para um diretório temporário), então nenhuma chave real do projeto é tocada.
`sem_besu`: não usa a Besu nem nenhum nó, dispensa o preflight do túnel.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
from eth_account import Account

pytestmark = pytest.mark.sem_besu

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "blockchain" / "gerar_chaves_rede_publica.py"
ARQUIVOS = ("venda-publica-cooperativa.key", "venda-publica-comprador.key")
HARDHAT_0 = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


def _rodar(saida: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--saida", str(saida)],
                          capture_output=True, text=True, timeout=60)


def _enderecos(stdout: str) -> list[str]:
    return re.findall(r"0x[0-9a-fA-F]{40}", stdout)


def test_cria_os_dois_arquivos_com_64_hex(tmp_path):
    p = _rodar(tmp_path)
    assert p.returncode == 0, p.stderr
    for nome in ARQUIVOS:
        conteudo = (tmp_path / nome).read_text(encoding="utf-8")
        assert re.fullmatch(r"[0-9a-f]{64}", conteudo), nome


def test_endereco_impresso_e_o_derivado_do_arquivo(tmp_path):
    p = _rodar(tmp_path)
    impressos = _enderecos(p.stdout)
    derivados = [Account.from_key((tmp_path / n).read_text(encoding="utf-8")).address for n in ARQUIVOS]
    assert impressos == derivados
    for nome in ARQUIVOS:
        assert nome in p.stdout


def test_saida_sem_chave_nem_caminho_completo(tmp_path):
    p = _rodar(tmp_path)
    saida = p.stdout + p.stderr
    for nome in ARQUIVOS:
        chave = (tmp_path / nome).read_text(encoding="utf-8")
        assert chave not in saida and chave[:16] not in saida
    assert str(tmp_path) not in saida
    assert str(tmp_path.parent) not in saida


def test_segunda_execucao_nao_altera_os_arquivos(tmp_path):
    primeira = _rodar(tmp_path)
    antes = {n: (tmp_path / n).read_bytes() for n in ARQUIVOS}
    segunda = _rodar(tmp_path)
    assert segunda.returncode == 0, segunda.stderr
    assert {n: (tmp_path / n).read_bytes() for n in ARQUIVOS} == antes
    assert _enderecos(segunda.stdout) == _enderecos(primeira.stdout)
    assert (segunda.stdout + segunda.stderr).count("ja existe") == 2


def test_arquivo_existente_nao_impede_gerar_o_outro(tmp_path):
    existente = tmp_path / ARQUIVOS[0]
    existente.write_text("11" * 32, encoding="utf-8")
    p = _rodar(tmp_path)
    assert p.returncode == 0, p.stderr
    assert existente.read_text(encoding="utf-8") == "11" * 32
    assert re.fullmatch(r"[0-9a-f]{64}", (tmp_path / ARQUIVOS[1]).read_text(encoding="utf-8"))


def test_os_dois_enderecos_sao_diferentes(tmp_path):
    a, b = _enderecos(_rodar(tmp_path).stdout)
    assert a != b


def test_recusa_chave_publica_do_hardhat_e_gera_outra(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("gerar_chaves_rede_publica", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    fila = [Account.from_key("0x" + HARDHAT_0)]
    criar_real = Account.create
    monkeypatch.setattr(modulo.Account, "create", lambda *a, **k: fila.pop(0) if fila else criar_real())
    assert modulo.main(["--saida", str(tmp_path)]) == 0
    assert not fila  # a chave do Hardhat foi sorteada e descartada
    for nome in ARQUIVOS:
        assert (tmp_path / nome).read_text(encoding="utf-8") != HARDHAT_0
