"""
Step defs (pytest-bdd) para tests/features/shacl_autoridade_validacao.feature.

Cenário 1 (histórico RED->GREEN): escrito ANTES de `_REQUIRED_FIELDS` ser
removida de coop-a/lote_endpoint.py. Na época, batchNumber ausente era
rejeitado com `{"error": "invalid_request", "error_description": "Campos
obrigatórios ausentes: batchNumber."}` — a shape SHACL
`HoneyApisIdentificationShape` (mensagem real: "Lote precisa ter número de
identificação") nunca chegava a rodar. Hoje é GREEN (`_REQUIRED_FIELDS` já
removida).

Cenário 2 (fechamento de cobertura, não RED->GREEN): producerDID/ownedBy
ausente já era rejeitado corretamente pela MESMA shape assim que
`_REQUIRED_FIELDS` caiu (ela tem sh:property tanto pra batchNumber quanto
pra ownedBy) — confirmado manualmente via curl antes deste cenário existir.
Nasce GREEN de propósito; existe só pra dar cobertura automatizada a um
comportamento que já estava correto.
"""
import sys
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/shacl_autoridade_validacao.feature")

COOP_A_URL = "http://localhost:8001"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")


@pytest.fixture
def context():
    return {}


@given("que a requisição POST /lote não inclui batchNumber")
def requisicao_sem_batch_number(context):
    # Corpo do lote SEM batchNumber (única ausência deliberada) — todos os
    # outros campos obrigatórios presentes, pra garantir que, se a rejeição
    # vier, é especificamente por causa do batchNumber ausente.
    context["body"] = {
        "productType": "HoneyApis",
        "producerName": "João da Silva",
        "producerDID": "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9",
    }


@when("ela é processada pelo endpoint")
def processa_requisicao(context):
    context["response"] = requests.post(f"{COOP_A_URL}/lote", json=context["body"], timeout=20)


@then("a rejeição vem do gate SHACL (HoneyApisIdentificationShape, minCount), não de uma checagem de campo obrigatório em Python")
def rejeicao_vem_do_shacl(context):
    resp = context["response"]
    assert resp.status_code == 400, f"esperado 400, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("error") == "shacl_violation", (
        f"esperado error='shacl_violation' (rejeição vinda do gate SHACL), "
        f"veio error={body.get('error')!r} — rejeição veio de outro lugar "
        f"(checagem Python de campo obrigatório, provavelmente): {body!r}"
    )


@then('a mensagem de erro retornada contém o texto da shape SHACL, não uma mensagem genérica de "campo obrigatório ausente"')
def mensagem_contem_texto_da_shape(context):
    body = context["response"].json()
    texto_completo = str(body)
    assert "Lote precisa ter número de identificação" in texto_completo, (
        f"esperava a mensagem exata de HoneyApisIdentificationShape "
        f"('Lote precisa ter número de identificação') na resposta, veio: {body!r}"
    )
    assert "Campos obrigatórios ausentes" not in texto_completo, (
        f"resposta ainda contém a mensagem genérica de campo obrigatório em "
        f"Python, não a mensagem da shape SHACL: {body!r}"
    )


# --- Cenário 2: producerDID/ownedBy ausente ----------------------------------


@given("que a requisição POST /lote não inclui producerDID")
def requisicao_sem_producer_did(context):
    import uuid

    # batchNumber único (uuid) pra confirmar depois, via SPARQL direto no
    # Fuseki, que NENHUM recurso com este batchNumber foi criado — não só
    # confiar no HTTP status.
    context["batch_number"] = f"TESTE-SEM-PRODUCERDID-{uuid.uuid4().hex[:8]}"
    context["body"] = {
        "batchNumber": context["batch_number"],
        "productType": "HoneyApis",
        "producerName": "João da Silva",
    }


@then("a rejeição vem do gate SHACL (HoneyApisIdentificationShape), não de checagem Python")
def rejeicao_vem_do_shacl_producer(context):
    resp = context["response"]
    assert resp.status_code == 400, f"esperado 400, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("error") == "shacl_violation", (
        f"esperado error='shacl_violation', veio error={body.get('error')!r}: {body!r}"
    )
    assert "Lote precisa ter produtor associado" in str(body), (
        f"esperava a mensagem exata de HoneyApisIdentificationShape pro "
        f"campo ownedBy ('Lote precisa ter produtor associado'), veio: {body!r}"
    )


@then("nenhum recurso é inserido no Fuseki (nem órfão, nem parcial)")
def nenhum_recurso_inserido(context):
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    SELECT (COUNT(*) AS ?n) WHERE {{
      ?s agritrust:batchNumber "{context['batch_number']}" .
    }}
    """
    resp = requests.post(
        f"{COOP_A_FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    resp.raise_for_status()
    count = int(resp.json()["results"]["bindings"][0]["n"]["value"])
    assert count == 0, (
        f"esperava 0 recursos no Fuseki com batchNumber={context['batch_number']!r} "
        f"(rejeitado pelo SHACL antes de inserir), achou {count} — recurso "
        f"órfão/parcial foi inserido."
    )
