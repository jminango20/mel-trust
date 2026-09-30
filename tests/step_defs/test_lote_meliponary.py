"""
Step defs (pytest-bdd) para tests/features/lote_meliponary.feature.

FASE RED (TDD): escrito e rodado ANTES de POST /lote aceitar
meliponaryId/environmentalLicenseNumber. Hoje esses campos são ignorados
(não fazem parte de _REQUIRED_FIELDS nem são lidos do body em
lote_endpoint.py) — nenhuma estrutura :Meliponary/:EnvironmentalLicense é
criada, e :MeliponaryLicenseShape nunca dispara via HTTP.

Testado contra coop-b (mel nativo, :HoneyNativeStinglessBee) — mesmo código
de coop-a, aceito ali também, mas sem cenário de teste dedicado (ver
decisão do usuário: "disponibilidade dos campos depende do productType
declarado, não de qual nó/porta foi chamado").
"""
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/lote_meliponary.feature")

COOP_B_URL = "http://localhost:8002"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6Mkkc7de5XEc1sUGBYxHo8yLWM9yTsnorqCNK7HnE8SZnVT"

_coop_b_env = dotenv_values(REPO_ROOT / "coop-b" / ".env")
COOP_B_FUSEKI_URL = _coop_b_env.get("FUSEKI_URL", "http://127.0.0.1:3031/coop-b")
COOP_B_FUSEKI_ADMIN_PASSWORD = _coop_b_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"


def _apaga_lote_no_fuseki(batch_number: str) -> None:
    """Limpeza dos lotes sintéticos (uuid) criados por este arquivo —
    inclui o :Meliponary/:EnvironmentalLicense ligados via ownedBy ao mesmo
    produtor, identificados pelo meliponaryId sintético (prefixo único por
    execução, ver `_slug` usado no lado do endpoint)."""
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?lote agritrust:batchNumber "{batch_number}" .
      ?lote ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_B_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_B_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


def _apaga_meliponary_no_fuseki(meliponary_id: str) -> None:
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?m agritrust:hasIdentifier "{meliponary_id}" .
      ?m ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_B_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_B_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _limpa_recursos_sinteticos(context):
    yield
    if "batch_number" in context:
        _apaga_lote_no_fuseki(context["batch_number"])
    if "meliponary_id" in context:
        _apaga_meliponary_no_fuseki(context["meliponary_id"])


def _corpo_lote(batch_number: str, meliponary_id: str | None, license_number: str | None) -> dict:
    body = {
        "batchNumber": batch_number,
        "productType": "HoneyNativeStinglessBee",
        "producerName": PRODUCER_NAME,
        "producerDID": PRODUCER_DID,
    }
    if meliponary_id:
        body["meliponaryId"] = meliponary_id
    if license_number:
        body["environmentalLicenseNumber"] = license_number
    return body


# --- Cenário 1: meliponário + licença -> aceito -------------------------------


@given("um novo lote de mel nativo com meliponaryId e environmentalLicenseNumber")
def novo_lote_com_licenca(context):
    suffix = uuid.uuid4().hex[:8]
    context["batch_number"] = f"MN-2026-MELIP-{suffix}"
    context["meliponary_id"] = f"MEL-TESTE-{suffix}"
    context["license_number"] = f"IBRAM-LA-TESTE-{suffix}"


@when("a cooperativa cadastra o lote via POST /lote")
def cadastra_lote(context):
    body = _corpo_lote(context["batch_number"], context["meliponary_id"], context["license_number"])
    context["lote_response"] = requests.post(f"{COOP_B_URL}/lote", json=body, timeout=20)


@then("o gate SHACL aprova e o meliponário fica registrado com a licença vinculada")
def gate_aprova_com_licenca(context):
    resp = context["lote_response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"

    query = f"""{_PREFIX}
    SELECT ?license WHERE {{
      ?m a :Meliponary ;
         agritrust:hasIdentifier "{context['meliponary_id']}" ;
         :hasEnvironmentalLicense ?license .
      ?license :environmentalLicenseNumber "{context['license_number']}" .
    }}
    """
    sparql_resp = requests.post(
        f"{COOP_B_FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", COOP_B_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    sparql_resp.raise_for_status()
    bindings = sparql_resp.json()["results"]["bindings"]
    assert len(bindings) == 1, (
        f"esperava achar :Meliponary com :hasEnvironmentalLicense vinculado no Fuseki, achou {len(bindings)}"
    )


# --- Cenário 2: meliponário sem licença -> rejeitado ---------------------------


@given("um novo lote de mel nativo com meliponaryId mas sem environmentalLicenseNumber")
def novo_lote_sem_licenca(context):
    suffix = uuid.uuid4().hex[:8]
    context["batch_number"] = f"MN-2026-MELIP-SEMLIC-{suffix}"
    context["meliponary_id"] = f"MEL-TESTE-SEMLIC-{suffix}"


@when("a cooperativa tenta cadastrar o lote via POST /lote")
def tenta_cadastrar_sem_licenca(context):
    body = _corpo_lote(context["batch_number"], context["meliponary_id"], None)
    context["lote_response"] = requests.post(f"{COOP_B_URL}/lote", json=body, timeout=20)


@then("retorna 400 com violação de MeliponaryLicenseShape")
def confere_rejeicao_meliponary(context):
    resp = context["lote_response"]
    assert resp.status_code == 400, f"esperado 400, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"] == "shacl_violation", body
    violations_text = " ".join(body.get("violations", []))
    assert "licença" in violations_text.lower() or "license" in violations_text.lower(), (
        f"esperava violação mencionando licença ambiental, veio: {body.get('violations')}"
    )
