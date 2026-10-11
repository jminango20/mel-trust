"""
Checagem do PRÓPRIO helper tests/support/load_node_module.py (script manual, NÃO coletado
pelo pytest — o nome não casa com `test_*.py`, então não entra na contagem da suíte).

Uso (da raiz do repo, com um venv que tenha python-dotenv):
    certifier/.venv/Scripts/python.exe tests/support/check_load_node_module.py

Confirma, no mesmo processo:
  1. o `config.py` do coop-a e depois o do lab trazem, cada um, o FUSEKI_URL do PRÓPRIO nó
     (sem receber o config em cache do primeiro);
  2. `os.environ`, `sys.path` e `sys.modules["config"]` voltam ao estado inicial, depois de
     cada carga;
  3. o mesmo vale quando o import FALHA no meio (arquivo inexistente e módulo que levanta).
"""
import os
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_node_module import load_node_module  # noqa: E402

DEFAULTS = {"coop-a": "http://127.0.0.1:3030/coop-a", "lab": "http://127.0.0.1:3033/lab"}


def snapshot():
    return dict(os.environ), list(sys.path), sys.modules.get("config")


def assert_restored(before, label):
    env, path, cfg = before
    assert dict(os.environ) == env, f"{label}: os.environ mudou"
    assert sys.path == path, f"{label}: sys.path mudou"
    assert sys.modules.get("config") is cfg, f"{label}: sys.modules['config'] mudou"
    print(f"  [ok] {label}: os.environ, sys.path e sys.modules['config'] restaurados")


initial = snapshot()
print(f"estado inicial: FUSEKI_URL no ambiente = {os.environ.get('FUSEKI_URL')!r}, 'config' em sys.modules = {'config' in sys.modules}")

urls = {}
for node in ("coop-a", "lab"):
    cfg = load_node_module(node, "config.py", "cfg")
    expected = dotenv_values(REPO_ROOT / node / ".env").get("FUSEKI_URL", DEFAULTS[node])
    assert cfg.FUSEKI_URL == expected, f"{node}: FUSEKI_URL={cfg.FUSEKI_URL!r}, esperado {expected!r}"
    urls[node] = cfg.FUSEKI_URL
    print(f"  [ok] config do {node}: FUSEKI_URL = {cfg.FUSEKI_URL}")
    assert_restored(initial, f"após carregar {node}/config.py")
assert urls["coop-a"] != urls["lab"], "os dois nós deveriam ter FUSEKI_URL diferentes"

# o módulo que faz `from config import ...` também recebe o config do PRÓPRIO nó
for node in ("coop-a", "lab"):
    anc = load_node_module(node, "anchoring.py", "anchoring")
    assert anc.RPC_URL == "http://localhost:8545" and anc.EXPECTED_CHAIN_ID == 648, node
    print(f"  [ok] {node}/anchoring.py importou RPC_URL/chainId do config do nó")
    assert_restored(initial, f"após carregar {node}/anchoring.py")

# falha de import no meio: arquivo inexistente
try:
    load_node_module("coop-a", "nao_existe.py", "x")
    raise SystemExit("deveria ter falhado")
except FileNotFoundError:
    print("  [ok] arquivo inexistente levantou FileNotFoundError")
assert_restored(initial, "após falha (arquivo inexistente)")

# falha de import no meio: o módulo executa, escreve no ambiente e depois levanta
with tempfile.TemporaryDirectory(dir=REPO_ROOT / "tests" / "support") as tmp:
    node_dir = Path(tmp)
    (node_dir / "boom.py").write_text(
        "import os, sys\nos.environ['FUSEKI_URL'] = 'vazou'\nimport config\nraise RuntimeError('falha no meio do import')\n",
        encoding="utf-8",
    )
    (node_dir / "config.py").write_text("X = 1\n", encoding="utf-8")
    # o helper resolve <REPO_ROOT>/<node>: usa um caminho relativo ao repo para o diretório temporário
    rel = node_dir.relative_to(REPO_ROOT).as_posix()
    try:
        load_node_module(rel, "boom.py", "boom")
        raise SystemExit("deveria ter falhado")
    except RuntimeError as exc:
        print(f"  [ok] módulo que escreve no ambiente e depois levanta -> {exc}")
    assert_restored(initial, "após falha no meio do import (ambiente sujado antes de levantar)")

print("\nTODAS AS CHECAGENS PASSARAM")
