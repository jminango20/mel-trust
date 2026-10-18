"""
Step defs (pytest-bdd) para tests/features/lote_idempotente.feature.

FASE RED: hoje POST /lote não confere se o batchNumber já existe antes de
inserir — só monta o grafo (URI determinística, reafirma triplas se a URI já
existe) e insere. Não há checagem "já existe outro recurso com esse
batchNumber" antes disso, então nada devolve 409 hoje.

Conta recursos DISTINTOS com agritrust:batchNumber == "MS-2026-042" direto no
Fuseki do coop-a (não só confia no HTTP status) — é o jeito de confirmar
"nenhum recurso duplicado é criado" de verdade, não só que a API respondeu
algo.
"""
import sys
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/lote_idempotente.feature")

COOP_A_URL = "http://127.0.0.1:8001"
BATCH_NUMBER = "MS-2026-042"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_COUNT_QUERY = f"""
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
SELECT (COUNT(DISTINCT ?lote) AS ?n) WHERE {{
  ?lote agritrust:batchNumber "{BATCH_NUMBER}" .
}}
"""


def _conta_lotes_no_fuseki() -> int:
    resp = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": _COUNT_QUERY},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    resp.raise_for_status()
    return int(resp.json()["results"]["bindings"][0]["n"]["value"])


@pytest.fixture
def context():
    return {}


@given("que o lote MS-2026-042 já existe")
def lote_ja_existe(context):
    # Fato de contexto: MS-2026-042 já está no Fuseki (lote canônico,
    # cadastrado via POST /lote — migrado de pipeline.py/fixture, ver
    # docs/known-issues.md). Só confere e guarda a contagem ANTES da
    # tentativa de recadastro, pra comparar depois.
    count = _conta_lotes_no_fuseki()
    assert count >= 1, f"esperava MS-2026-042 já existir no Fuseki, achou {count} recursos"
    context["count_antes"] = count


@when("a cooperativa tenta cadastrar via POST /lote de novo com o mesmo batchNumber")
def tenta_cadastrar_de_novo(context):
    body = {
        "batchNumber": BATCH_NUMBER,
        "productType": "HoneyApis",
        "producerName": "João da Silva",
        "producerDID": "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9",
    }
    context["lote_response"] = requests.post(f"{COOP_A_URL}/lote", json=body, timeout=20)


@then("retorna 409 e nenhum recurso duplicado é criado")
def confere_409_sem_duplicata(context):
    resp = context["lote_response"]
    assert resp.status_code == 409, f"esperado 409, veio {resp.status_code}: {resp.text}"
    count_depois = _conta_lotes_no_fuseki()
    assert count_depois == context["count_antes"], (
        f"contagem de recursos com batchNumber={BATCH_NUMBER!r} mudou de "
        f"{context['count_antes']} para {count_depois} — recurso duplicado foi criado."
    )
