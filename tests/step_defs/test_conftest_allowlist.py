"""
Step defs (pytest-bdd) para tests/features/conftest_allowlist.feature.

Protege a allowlist de tests/conftest.py (`_TEST_BATCH_PATTERN`): a limpeza que roda antes e
depois de cada teste (`_purge_orphan_tasks`) apaga AnchoringTask órfã SÓ de batchNumber de
teste. Uma tarefa órfã de batchNumber real (fora da lista) tem de sobreviver — se alguém
afrouxar o filtro, este teste fica vermelho.

O teste planta direto no Fuseki (coop-a e coop-b) duas tarefas órfãs (sem lote com o mesmo
batchNumber): uma com `LOTE-REAL-PROTEGIDO-<uuid>` (fora) e outra com
`MS-2026-OUTBOX1-PROTALLOW-<uuid>` (dentro, casa com OUTBOX[1-5]). Chama a MESMA função da
fixture autouse. No fim remove SÓ a tarefa que plantou fora da lista (o `finally` também cobre
falha no meio) e confirma que não sobrou resíduo de nenhuma das duas.
"""
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pytest_bdd import given, scenarios, then, when

import conftest as suite_conftest  # tests/conftest.py (mesmo módulo que o pytest carregou)

REPO_ROOT = Path(__file__).resolve().parents[2]
BEE_NS = "https://w3id.org/agrisemantics/AGRITRUST/bee-extension#"
_PREFIX = f"PREFIX : <{BEE_NS}>\n"

scenarios("../features/conftest_allowlist.feature")


def _node_config(name: str, default_fuseki: str) -> dict:
    env = dotenv_values(REPO_ROOT / name / ".env")
    return {"name": name, "fuseki": env.get("FUSEKI_URL", default_fuseki),
            "password": env.get("FUSEKI_ADMIN_PASSWORD")}


_NODES = {
    "coop-a": _node_config("coop-a", "http://127.0.0.1:3030/coop-a"),
    "coop-b": _node_config("coop-b", "http://127.0.0.1:3031/coop-b"),
}


@pytest.fixture(params=list(_NODES), autouse=True)
def node(request):
    return _NODES[request.param]


@pytest.fixture
def ctx():
    return {}


def _update(node, query):
    requests.post(f"{node['fuseki']}/update", data={"update": query},
                  auth=("admin", node["password"]), timeout=20).raise_for_status()


def _task_exists(node, task_uri: str) -> bool:
    r = requests.post(f"{node['fuseki']}/sparql", data={"query": f"ASK {{ <{task_uri}> ?p ?o }}"},
                      headers={"Accept": "application/sparql-results+json"},
                      auth=("admin", node["password"]), timeout=20)
    r.raise_for_status()
    return r.json()["boolean"]


def _plant(node, task_uri: str, batch: str):
    _update(node, f"""{_PREFIX}
    DELETE WHERE {{ <{task_uri}> ?p ?o }} ;
    INSERT DATA {{ <{task_uri}> a :AnchoringTask ;
        :anchoringBatch "{batch}" ; :anchoringStatus "pending" . }}""")


def _delete(node, task_uri: str):
    _update(node, f"DELETE WHERE {{ <{task_uri}> ?p ?o }}")


@pytest.fixture
def planted(node, ctx):
    suffix = uuid.uuid4().hex[:10]
    ctx["protected_batch"] = f"LOTE-REAL-PROTEGIDO-{suffix}"
    ctx["allowed_batch"] = f"MS-2026-OUTBOX1-PROTALLOW-{suffix}"
    ctx["protected_uri"] = f"{BEE_NS}AnchoringTask_{ctx['protected_batch'].replace('-', '_')}"
    ctx["allowed_uri"] = f"{BEE_NS}AnchoringTask_{ctx['allowed_batch'].replace('-', '_')}"
    yield ctx
    # garantia de limpeza mesmo se um passo falhar: só o que ESTE teste plantou
    _delete(node, ctx["protected_uri"])
    _delete(node, ctx["allowed_uri"])


@given("uma AnchoringTask órfã com batchNumber fora da allowlist e outra órfã com batchNumber da allowlist, plantadas direto no Fuseki")
def plant_both(node, planted):
    _plant(node, planted["protected_uri"], planted["protected_batch"])
    _plant(node, planted["allowed_uri"], planted["allowed_batch"])
    assert _task_exists(node, planted["protected_uri"]) and _task_exists(node, planted["allowed_uri"])


@when("a limpeza de teste roda")
def run_cleanup(planted):
    suite_conftest._purge_orphan_tasks()


@then("a tarefa fora da allowlist continua no Fuseki e a da allowlist foi apagada")
def check_cleanup(node, planted):
    assert _task_exists(node, planted["protected_uri"]), (
        f"A limpeza APAGOU a tarefa de batchNumber fora da allowlist ({planted['protected_batch']}) "
        f"no {node['name']}: o filtro de tests/conftest.py foi afrouxado"
    )
    assert not _task_exists(node, planted["allowed_uri"]), (
        f"A tarefa órfã da allowlist ({planted['allowed_batch']}) NÃO foi apagada no {node['name']}"
    )


@then("ao final só a tarefa plantada fora da lista é removida e o Fuseki não fica com resíduo")
def final_cleanup(node, planted):
    _delete(node, planted["protected_uri"])
    assert not _task_exists(node, planted["protected_uri"])
    assert not _task_exists(node, planted["allowed_uri"])
    rows = requests.post(
        f"{node['fuseki']}/sparql",
        data={"query": f'{_PREFIX} ASK {{ ?t :anchoringBatch ?b . FILTER(CONTAINS(?b, "{planted["protected_batch"].rsplit("-", 1)[1]}")) }}'},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", node["password"]), timeout=20)
    rows.raise_for_status()
    assert rows.json()["boolean"] is False, "sobrou resíduo com o uuid deste teste"
