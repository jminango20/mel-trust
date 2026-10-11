"""
Higiene de testes do outbox de ancoragem.

Vários testes usam batchNumber FIXO (ex.: MN-2026-015) e apagam o lote do
Fuseki no cleanup, mas o asset on-chain persiste. Na rodada seguinte,
POST /lote regrava a tarefa `pending`, a ancoragem reverte (asset já existe) e
a `AnchoringTask` sobra sem lote — o que, em produção, o retry reporta
corretamente como `lote_not_found`, mas em teste suja o "nada a processar" de
tests/features/ancoragem_outbox.feature.

Esta fixture apaga, antes e depois de cada teste, AnchoringTask cujo
batchNumber é um dos usados pelos testes (allowlist `_TEST_BATCH_PATTERN`):
- coop-a e coop-b: só as ÓRFÃS (nenhum recurso do Fuseki com o mesmo
  agritrust:batchNumber, ou seja, o lote já foi apagado);
- LAB: TODAS as da allowlist, órfãs ou não. Motivo: testes antigos de
  batchNumber fixo (MS-2026-042, MN-2026-015, MS-2026-042-LAB-REJEITADO)
  repostam o laudo a cada rodada e nunca o apagam do lab; a ancoragem reverte
  (asset já existe) e a tarefa sobra pending/mismatch COM o laudo presente, logo
  não-órfã. No lab essas tarefas são dados de teste; sem limpá-las o "nada a
  processar" de ancoragem_outbox_lab.feature não vale para o Fuseki inteiro. Tarefas de
lotes existentes, ou de batchNumbers fora da allowlist (dados reais), nunca
são tocadas. Se um teste novo usar um batchNumber fora da allowlist, a
tarefa órfã dele sobra e o "nada a processar" do outbox falha — sinal para
estender a allowlist, não para afrouxar o filtro.
"""
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Fixos: MN-2026-015, MS-2026-042, MS-2026-042-LAB-REJEITADO. Sintéticos
# (inclui MEL-SILVESTRE-*, de test_pre_authorized_code.py):
# prefixo + sufixo aleatório (ver tests/step_defs/*.py). Ancorado com ^...$.
# Todo teste novo que cria batchNumber sintético precisa adicionar o prefixo
# aqui, senão a AnchoringTask dele não é limpa (órfã em coop-a/coop-b; órfã ou
# não no lab) e quebra o bloco 'nada a processar' de ancoragem_outbox.feature /
# ancoragem_outbox_lab.feature. (OUTBOX[1-5] = tests de coop-a/coop-b;
# LABOUT[1-6] = tests do lab.)
_TEST_BATCH_PATTERN = (
    r"^(MN-2026-015|MS-2026-042|MS-2026-042-LAB-REJEITADO"
    r"|MS-2026-042-REEMISSAO-.+|MEL-SILVESTRE-.+"
    r"|MN-2026-(MELIP|MELIP-SEMLIC|REUSE1|REUSE2)-.+"
    r"|MS-2026-(ANCCERT|ANCLAUDO|ANCORA|APRESVP|HOLDERVC|VALIDAVP|VERIFYOC|OUTBOX[1-5]|LABOUT[1-6])-.+)$"
)
# LAB: tarefas da allowlist, órfãs ou não (ver docstring do módulo).
_ALLOWLISTED_TASKS_UPDATE = """
PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
DELETE { ?t ?p ?o }
WHERE {
  ?t :anchoringBatch ?b ; ?p ?o .
  FILTER REGEX(STR(?b), "%s")
}
""" % _TEST_BATCH_PATTERN
# coop-a / coop-b: só as órfãs da allowlist.
_ORPHAN_TASKS_UPDATE = """
PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
DELETE { ?t ?p ?o }
WHERE {
  ?t :anchoringBatch ?b ; ?p ?o .
  FILTER NOT EXISTS { ?lote agritrust:batchNumber ?b }
  FILTER REGEX(STR(?b), "%s")
}
""" % _TEST_BATCH_PATTERN


def _purge_orphan_tasks():
    for node, default_url, update in (
        ("coop-a", "http://127.0.0.1:3030/coop-a", _ORPHAN_TASKS_UPDATE),
        ("coop-b", "http://127.0.0.1:3031/coop-b", _ORPHAN_TASKS_UPDATE),
        ("lab", "http://127.0.0.1:3033/lab", _ALLOWLISTED_TASKS_UPDATE),
    ):
        env = dotenv_values(_REPO_ROOT / node / ".env")
        try:
            requests.post(
                f"{env.get('FUSEKI_URL', default_url)}/update",
                data={"update": update},
                auth=("admin", env.get("FUSEKI_ADMIN_PASSWORD")),
                timeout=10,
            )
        except requests.RequestException:
            pass  # Fuseki fora: o próprio teste vai falhar com a causa real


@pytest.fixture(autouse=True)
def _purge_orphan_anchoring_tasks():
    _purge_orphan_tasks()
    yield
    _purge_orphan_tasks()


# 127.0.0.1, não `localhost`: no Windows `localhost` tenta ::1 e depois IPv4, e cada
# tentativa recusada custa ~2s. Medido (eth_chainId, 4 amostras por caso): porta
# fechada 4,1s (localhost) vs 2,05s (127.0.0.1); porta que não responde 7,07s vs
# 5,02s; túnel de pé, igual nos dois (~0,15s).
_RPC_URL = "http://127.0.0.1:8545"


@pytest.fixture(autouse=True)
def _rpc_tunnel_alive():
    """Se o RPC do Besu (túnel SSH) não responde, ENCERRA a suíte inteira
    (`pytest.exit`) na primeira falha, com mensagem clara — sem arrastar os
    demais testes (cada um falharia igual, ~2-5s de espera por teste) e sem
    deixar nenhum pendurado ou falhando com um "unavailable" genérico lá na
    frente. Roda antes de CADA teste: se o túnel cair no meio da suíte (ou a
    máquina suspender), o próximo teste aponta a causa. Custo com o túnel de
    pé: ~0,15s por teste."""
    try:
        r = requests.post(
            _RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
            timeout=5,
        )
        ok = r.status_code == 200 and r.json().get("result") == "0x288"  # chain 648
    except (requests.RequestException, ValueError):
        ok = False
    if not ok:
        pytest.exit(
            f"RPC/túnel não respondeu em {_RPC_URL} (eth_chainId, timeout 5s) — "
            "túnel SSH do Besu fora ou máquina suspensa? Suba o túnel e rode de novo.",
            returncode=3,
        )
    yield
