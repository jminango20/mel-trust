"""
Carrega um módulo de um nó (ex.: `coop-a/anchoring.py`, `lab/hash_utils.py`) POR CAMINHO,
de dentro do processo do pytest, sem deixar rastro.

Por que existe: os módulos dos nós usam imports "de nó" (`from config import ...`), e os
5 nós têm um `config.py` com o MESMO nome. Fora do diretório do nó, `import config` não
resolve; e, resolvido, três coisas vazariam para o resto da suíte:
  - `sys.path`: o diretório do nó ficaria nele;
  - `sys.modules["config"]`: o segundo nó carregado receberia o config EM CACHE do primeiro
    (o lab leria o FUSEKI_URL do coop-a);
  - `os.environ`: o `config.py` chama `load_dotenv()`, que ESCREVE o `.env` do nó no
    ambiente do pytest, e os subprocessos dos testes (tests/support/run_retry_injected.py)
    herdariam o FUSEKI_URL/senha do primeiro nó carregado (`load_dotenv` não sobrescreve
    variáveis já definidas). Foi exatamente isso que fez 3 testes de `in_flight` falharem.

Esta função isola as três coisas e as restaura no `finally`, inclusive se o import falhar.
O módulo devolvido guarda as constantes já lidas; o ambiente do processo fica como estava.
"""
import importlib.util
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Nomes de módulo que colidem entre os nós (todos têm um `config.py`).
_COLLIDING_MODULES = ("config",)


def load_node_module(node: str, filename: str, alias: str):
    """Executa `<node>/<filename>` com o diretório do nó no `sys.path` e devolve o módulo
    (registrado só sob um nome único, `_<node>_<alias>`, nunca como `config`/`anchoring`)."""
    node_dir = REPO_ROOT / node
    saved_env = dict(os.environ)
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules[name] for name in _COLLIDING_MODULES if name in sys.modules}
    try:
        sys.path.insert(0, str(node_dir))
        for name in _COLLIDING_MODULES:
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(f"_{node.replace('-', '_')}_{alias}", node_dir / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved_path
        for name in _COLLIDING_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        os.environ.clear()
        os.environ.update(saved_env)
