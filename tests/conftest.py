"""
Higiene de testes do outbox de ancoragem.

Vários testes usam batchNumber FIXO (ex.: MN-2026-015) e apagam o lote do
Fuseki no cleanup, mas o asset on-chain persiste. Na rodada seguinte,
POST /lote regrava a tarefa `pending`, a ancoragem reverte (asset já existe) e
a `AnchoringTask` sobra sem lote — o que, em produção, o retry reporta
corretamente como `lote_not_found`, mas em teste suja o "nada a processar" de
tests/features/ancoragem_outbox.feature.

Esta fixture apaga, antes e depois de cada teste, as AnchoringTask que são
ÓRFÃS (nenhum recurso com o mesmo agritrust:batchNumber) E cujo batchNumber é
um dos usados pelos testes (allowlist `_TEST_BATCH_PATTERN`). Tarefas de
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
# Fixos: MN-2026-015, MS-2026-042, MS-2026-042-LAB-REJEITADO. Sintéticos:
# prefixo + sufixo aleatório (ver tests/step_defs/*.py). Ancorado com ^...$.
# Todo teste novo que cria batchNumber sintético precisa adicionar o prefixo
# aqui, senão a AnchoringTask órfã dele não é limpa e quebra o bloco 'nada a
# processar' de ancoragem_outbox.feature.
_TEST_BATCH_PATTERN = (
    r"^(MN-2026-015|MS-2026-042|MS-2026-042-LAB-REJEITADO"
    r"|MS-2026-042-REEMISSAO-.+"
    r"|MN-2026-(MELIP|MELIP-SEMLIC|REUSE1|REUSE2)-.+"
    r"|MS-2026-(ANCCERT|ANCLAUDO|ANCORA|APRESVP|HOLDERVC|VALIDAVP|VERIFYOC|OUTBOX[123])-.+)$"
)
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
    for node, default_url in (("coop-a", "http://127.0.0.1:3030/coop-a"), ("coop-b", "http://127.0.0.1:3031/coop-b")):
        env = dotenv_values(_REPO_ROOT / node / ".env")
        try:
            requests.post(
                f"{env.get('FUSEKI_URL', default_url)}/update",
                data={"update": _ORPHAN_TASKS_UPDATE},
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
