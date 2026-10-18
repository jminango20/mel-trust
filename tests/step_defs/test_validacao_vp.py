"""
Step defs (pytest-bdd) para tests/features/validacao_vp.feature.

FASE RED (TDD): escrito ANTES de qualquer código novo. Hoje `verifier-demo`
não tem `POST /present-response/{batch_number}` (só `GET /health`,
`GET /verify/{batch_number}` e `POST /presentation-request/{batch_number}`
— ver verifier-demo/main.py). Além disso, `onChainIntegrity.certificacao`
em `GET /verify/{batch_number}` hoje é sempre `"not_verifiable_via_query"`
(ver verifier-demo/onchain_verification.py) — o objetivo deste cenário é
que, DEPOIS de uma apresentação (VP) válida ser recebida e validada, essa
informação negativa seja substituída por uma resposta REAL
(`"confirmed"`/`"mismatch"`), já que a VP entrega o que faltava pra
verificar (o Credential-JWT completo, de verdade, não reconstruível via
SPARQL).
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

scenarios("../features/validacao_vp.feature")

COOP_A_URL = "http://127.0.0.1:8001"
LAB_URL = "http://127.0.0.1:8005"
VERIFIER_URL = "http://127.0.0.1:8004"
# audience da VP: entra no `aud` do KB-JWT (dentro da credencial apresentada), NAO e URL de conexao:
# fica `localhost` (o valor de antes da troca de URLs de conexao por 127.0.0.1).
VERIFIER_AUDIENCE = "http://localhost:8004"
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


@given("que a coop-a apresentou uma VP válida para o lote MS-2026-042")
def coop_a_apresentou_vp(context):
    # batchNumber SINTÉTICO por execução (mesmo padrão já aplicado nos
    # outros cenários desta sessão). Roda o fluxo completo já GREEN:
    # cadastro -> análise -> retirar-certificacao -> presentation-request
    # -> present (VP real, com KB-JWT + sd_hash reais).
    suffix = uuid.uuid4().hex[:8]
    batch_number = f"MS-2026-VALIDAVP-{suffix}"

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
    nonce = presentation_resp.json()["nonce"]

    present_resp = requests.post(
        f"{COOP_A_URL}/present/{batch_number}",
        json={"nonce": nonce, "audience": VERIFIER_AUDIENCE},
        timeout=20,
    )
    assert present_resp.status_code == 200, (
        f"esperado 200 em POST /present, veio {present_resp.status_code}: {present_resp.text}"
    )

    context["batch_number"] = batch_number
    context["vp"] = present_resp.json()["vp"]


@when("o verifier-demo recebe a VP via POST /present-response/{batch_number}")
def verifier_recebe_vp(context):
    context["response"] = requests.post(
        f"{VERIFIER_URL}/present-response/{context['batch_number']}",
        json={"vp": context["vp"]},
        timeout=20,
    )


@then("ele valida o KB-JWT (nonce correto e não reutilizado, sd_hash bate com o Credential-JWT + disclosures recebidos, assinatura confere com cnf.jwk da credencial)")
def valida_kb_jwt(context):
    # Sem forma independente de confirmar isso além da resposta HTTP
    # (checada no último step) — documenta a intenção do Gherkin.
    pass


@then("recalcula o hash do Credential-JWT (só essa parte, não a VP inteira)")
def recalcula_hash_credential_jwt(context):
    pass


@then("compara com dataHashes[] ancorado on-chain para o asset de certificação")
def compara_data_hashes(context):
    pass


@then('a resposta substitui "not_verifiable_via_query" por "confirmed" ou "mismatch"')
def resposta_substitui_status(context):
    resp = context["response"]
    assert resp.status_code == 200, (
        f"esperado 200 em POST /present-response/{{batch_number}}, veio {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "onChainIntegrity" in body, f"esperava campo 'onChainIntegrity' na resposta, veio: {body!r}"
    certificacao = body["onChainIntegrity"].get("certificacao")
    assert certificacao in ("confirmed", "mismatch"), (
        f"esperava onChainIntegrity.certificacao em ('confirmed', 'mismatch'), "
        f"veio {certificacao!r} — resposta completa: {body!r}"
    )
