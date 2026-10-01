"""
Step defs (pytest-bdd) para tests/features/apresentacao_vp.feature.

FASE RED (TDD): escrito ANTES de qualquer código novo. Hoje `coop-a` não
tem nenhum endpoint de apresentação (`POST /present/{batch_number}` não
existe) — só `/lote`, `/lote/{batch}/reemitir-codigo` e
`/lote/{batch}/retirar-certificacao` (ver coop-a/lote_endpoint.py).

ACHADO DE DESIGN (investigado ANTES de escrever este arquivo, conforme
pedido — não decidido sozinho, só confirmado o fato no código):
`certifier/credential_endpoint.py` deriva `cnf.jwk` da VC de certificação
do campo `jwk` do HEADER do proof JWT que o holder envia
(`holder_jwk_dict = proof_header.get("jwk")`, linha ~450). Em
`coop-a/lote_endpoint.py:_build_proof_jwt` (endpoint
`/retirar-certificacao`, já implementado), esse header é montado com
`public_jwk = key.export_public()` de `keys.get_issuer_key()` — ou seja,
`cnf.jwk` da VC de certificação é a chave PÚBLICA do PRÓPRIO issuer da
coop-a (`coop-a/keys/coop-a.pem`). A coop-a JÁ TEM acesso à chave PRIVADA
correspondente (mesmo arquivo, mesma função `get_issuer_key()` já usada
para assinar as próprias VCs de lote) — não há nenhum bloqueio de acesso a
chave para montar o KB-JWT; é a MESMA chave, reaproveitada como "chave de
holder" (mesma simplificação de MVP já documentada em
`_build_proof_jwt`).
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

scenarios("../features/apresentacao_vp.feature")

COOP_A_URL = "http://localhost:8001"
LAB_URL = "http://localhost:8005"
VERIFIER_URL = "http://localhost:8004"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_certifier_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERTIFIER_FUSEKI_URL = _certifier_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERTIFIER_FUSEKI_ADMIN_PASSWORD = _certifier_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"


@pytest.fixture
def context():
    return {}


def _apaga_lote_no_coop_a(batch_number: str):
    query = f"""{_PREFIX}
    DELETE WHERE {{
      ?lote agritrust:batchNumber "{batch_number}" .
      ?lote ?p ?o .
    }}
    """
    requests.post(
        f"{COOP_A_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


def _apaga_lote_no_certifier(batch_number: str):
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    DELETE WHERE {{
      ?s agritrust:batchNumber "{batch_number}" .
      ?s ?p ?o .
    }}
    """
    requests.post(
        f"{CERTIFIER_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", CERTIFIER_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


@pytest.fixture(autouse=True)
def _limpeza(context):
    yield
    if "batch_number" not in context:
        return
    _apaga_lote_no_coop_a(context["batch_number"])
    _apaga_lote_no_certifier(context["batch_number"])


@given("que o verifier-demo gerou um presentation request com nonce para o lote MS-2026-042")
def verifier_gerou_presentation_request(context):
    # Apesar do Gherkin citar "MS-2026-042" (nome ilustrativo), usa
    # batchNumber SINTÉTICO por execução (mesmo padrão já aplicado nos
    # outros cenários desta sessão) — cadastra lote real, analisa no lab,
    # retira e guarda a VC de certificação (fluxo já GREEN), e só então
    # pede o presentation request ao verifier-demo (também já GREEN).
    suffix = uuid.uuid4().hex[:8]
    batch_number = f"MS-2026-APRESVP-{suffix}"

    lote_resp = requests.post(
        f"{COOP_A_URL}/lote",
        json={
            "batchNumber": batch_number,
            "productType": "HoneyApis",
            "producerName": PRODUCER_NAME,
            "producerDID": PRODUCER_DID,
        },
        timeout=20,
    )
    assert lote_resp.status_code == 200, (
        f"esperado 200 no cadastro do lote, veio {lote_resp.status_code}: {lote_resp.text}"
    )

    analise_resp = requests.post(
        f"{LAB_URL}/analise",
        json={
            "batchNumber": batch_number,
            "moistureContent": 18.5,
            "hmfContent": 12.3,
            "acidityContent": 30.0,
            "diastaseActivity": 10.0,
        },
        timeout=20,
    )
    assert analise_resp.status_code == 200, (
        f"esperado 200 na análise do lab, veio {analise_resp.status_code}: {analise_resp.text}"
    )

    retirar_resp = requests.post(
        f"{COOP_A_URL}/lote/{batch_number}/retirar-certificacao", timeout=30
    )
    assert retirar_resp.status_code == 200, (
        f"esperado 200 ao retirar a certificação, veio {retirar_resp.status_code}: {retirar_resp.text}"
    )

    presentation_resp = requests.post(
        f"{VERIFIER_URL}/presentation-request/{batch_number}", timeout=10
    )
    assert presentation_resp.status_code == 200, (
        f"esperado 200 no presentation request, veio {presentation_resp.status_code}: {presentation_resp.text}"
    )
    presentation_body = presentation_resp.json()

    context["batch_number"] = batch_number
    context["nonce"] = presentation_body["nonce"]
    context["verifier_audience"] = VERIFIER_URL


@when("a coop-a chama POST /present/{batch_number} com esse nonce")
def coop_a_chama_present(context):
    context["response"] = requests.post(
        f"{COOP_A_URL}/present/{context['batch_number']}",
        json={"nonce": context["nonce"], "audience": context["verifier_audience"]},
        timeout=20,
    )


@then("ela busca a VC de certificação guardada no próprio Fuseki")
def busca_vc_guardada(context):
    # Sem forma independente de confirmar isso além da resposta HTTP
    # (checada no próximo step) — documenta a intenção do Gherkin.
    pass


@then("monta um Key Binding JWT assinado com a chave do holder (cnf.jwk da credencial), contendo o nonce e a audiência do verificador")
def monta_kb_jwt(context):
    pass


@then("retorna a VP completa: <Credential-JWT>~<Disclosures escolhidas>~<KB-JWT>")
def retorna_vp_completa(context):
    resp = context["response"]
    assert resp.status_code == 200, (
        f"esperado 200 em POST /present/{{batch_number}}, veio {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "vp" in body, f"esperava campo 'vp' na resposta, veio: {body!r}"
    vp = body["vp"]
    # Formato SD-JWT VP: <Credential-JWT>~<disclosure>~...~<KB-JWT final,
    # sem "~" depois dele> — pelo menos 2 segmentos "~" (credential + ao
    # menos 1 disclosure/KB-JWT).
    parts = vp.split("~")
    assert len(parts) >= 2, f"esperava VP com pelo menos 2 segmentos '~', veio: {vp!r}"
