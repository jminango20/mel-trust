"""
Step defs (pytest-bdd) para tests/features/coop_a_claims_isolados.feature.

FASE RED (TDD): escrito e rodado ANTES de qualquer mudança em
coop-a/credential_endpoint.py. Hoje a query SPARQL de lá é DINÂMICA — inclui
`moistureContent`/`hmfContent`/`acidityContent` como claims disclosable SE a
tripla existir no Fuseki, não importa como chegou lá. `diastaseActivity`/
`legalStatus`/`qualityStatus` nunca são sequer consultados pelo coop-a hoje
(sempre ausentes, independente do Fuseki) — só documentado no cenário por
completude/blindagem contra regressão futura, não porque falham agora.

Planta `moistureContent` DIRETO no Fuseki do coop-a via SPARQL Update
(bypass de `/lote`, que nunca escreve esse campo) — simula "por qualquer
motivo futuro" (migração manual, script, edição direta) esse dado coexistir
ali. Removido pela fixture autouse ao final do cenário.
"""
import sys
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from jwcrypto.jwk import JWK
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.test_credential_flow import GRANT_TYPE, b64url_json, build_proof_jwt  # noqa: E402

scenarios("../features/coop_a_claims_isolados.feature")

COOP_A_URL = "http://localhost:8001"
BATCH_NUMBER = "MS-2026-042"
PRE_AUTHORIZED_CODE = "coop-a-demo-pre-auth-code"
PRODUCER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"

_CAMPOS_QUE_NUNCA_DEVEM_SER_CLAIM = (
    "moistureContent",
    "hmfContent",
    "acidityContent",
    "diastaseActivity",
    "legalStatus",
    "qualityStatus",
)


def _lote_uri() -> str:
    return f"https://w3id.org/agrisemantics/AGRITRUST/bee-extension#Lote_{BATCH_NUMBER.replace('-', '_')}"


@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _remove_moisture_plantado():
    yield
    query = f"""{_PREFIX}
    DELETE WHERE {{
      <{_lote_uri()}> :hasMoistureContent ?m .
    }}
    """
    requests.post(
        f"{COOP_A_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    ).raise_for_status()


@given("que o lote MS-2026-042 tem moistureContent no Fuseki do coop-a (dado de medição que pode coexistir ali por qualquer motivo futuro)")
def planta_moisture_no_fuseki(context):
    query = f"""{_PREFIX}
    INSERT DATA {{
      <{_lote_uri()}> :hasMoistureContent "18.5"^^<http://www.w3.org/2001/XMLSchema#decimal> .
    }}
    """
    resp = requests.post(
        f"{COOP_A_FUSEKI_URL}/update",
        data={"update": query},
        auth=("admin", COOP_A_FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    resp.raise_for_status()
    context["batch_number"] = BATCH_NUMBER


@when("POST /credential é chamado no coop-a para esse lote")
def chama_credential(context):
    meta = requests.get(f"{COOP_A_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))

    token_resp = requests.post(
        f"{COOP_A_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": PRE_AUTHORIZED_CODE},
        timeout=10,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    nonce_resp = requests.post(f"{COOP_A_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]

    key = JWK.from_pem(PRODUCER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    resp = requests.post(
        f"{COOP_A_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": context["batch_number"],
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    context["credential_response"] = resp


def _decodifica(context) -> tuple[dict, dict]:
    """Retorna (payload sempre-visível, disclosures) do SD-JWT."""
    sd_jwt = context["credential_response"].json()["credential"]
    parts = sd_jwt.split("~")
    payload = b64url_json(parts[0].split(".")[1])
    disclosures = [b64url_json(p) for p in parts[1:] if p]
    disclosed = {d[1]: d[2] for d in disclosures if len(d) == 3}
    return payload, disclosed


@then("moistureContent NÃO aparece nas claims da credencial (nem em claro, nem como disclosure)")
def moisture_nao_aparece(context):
    payload, disclosed = _decodifica(context)
    assert "moistureContent" not in payload, (
        f"moistureContent apareceu em CLARO na credencial do coop-a: {payload!r}"
    )
    assert "moistureContent" not in disclosed, (
        f"moistureContent apareceu como DISCLOSURE na credencial do coop-a: {disclosed!r}"
    )


@then("o mesmo vale para hmfContent, acidityContent, diastaseActivity, legalStatus, qualityStatus — nenhum desses é claim do coop-a")
def demais_campos_nao_aparecem(context):
    payload, disclosed = _decodifica(context)
    falhas = []
    for campo in _CAMPOS_QUE_NUNCA_DEVEM_SER_CLAIM:
        if campo in payload:
            falhas.append(f"{campo!r} em claro: {payload[campo]!r}")
        if campo in disclosed:
            falhas.append(f"{campo!r} como disclosure: {disclosed[campo]!r}")
    assert not falhas, "Campo(s) de medição/laudo vazaram na credencial do coop-a: " + "; ".join(falhas)
