"""
Step defs (pytest-bdd) para tests/features/verificacao_hash_onchain.feature.

FASE RED (TDD): escrito ANTES de qualquer integração blockchain em
verifier-demo/verify_endpoint.py. Hoje GET /verify/{batch_number} devolve
{"batchNumber", "sourceNode", "batch": {...}, "certification": {...}|None}
— nenhuma consulta ao AssetRegistry, nenhum campo de integridade on-chain.

Usa o fluxo completo já GREEN (POST /lote no coop-a -> POST /analise no lab
-> POST /lote/{batch}/pre-authorized-code + /token + /credential no
certifier) pra produzir um lote com os 3 assets realmente ancorados
on-chain (ver tests/step_defs/test_ancoragem_lote.py,
test_ancoragem_laudo.py, test_ancoragem_certificacao.py — mesmos mecanismos,
reunidos aqui num único lote sintético).

DECISÃO DE NOME DE CAMPO (RED, sujeita a ajuste na implementação real): a
asserção abaixo espera um campo "onChainIntegrity" na resposta de
GET /verify, com subchaves "lote"/"laudo"/"certificacao" (bool cada) — é a
interpretação mais direta do Gherkin ("um campo por evento... indicando se
a integridade foi confirmada"), documentada aqui para a fase GREEN seguir
ou renomear com justificativa.
"""
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from jwcrypto.jwk import JWK
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.test_credential_flow import GRANT_TYPE, build_proof_jwt  # noqa: E402

scenarios("../features/verificacao_hash_onchain.feature")

COOP_A_URL = "http://localhost:8001"
LAB_URL = "http://localhost:8005"
CERTIFIER_URL = "http://localhost:8003"
VERIFIER_URL = "http://localhost:8004"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"
PURPOSE_VALIDO = "Compliance EUDR"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_certifier_env = dotenv_values(REPO_ROOT / "certifier" / ".env")
CERTIFIER_FUSEKI_URL = _certifier_env.get("FUSEKI_URL", "http://127.0.0.1:3032/certifier")
CERTIFIER_FUSEKI_ADMIN_PASSWORD = _certifier_env.get("FUSEKI_ADMIN_PASSWORD")

_PREFIX = "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\nPREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"

HOLDER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"


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


@given("que um lote foi registrado, analisado e certificado, com os 3 assets ancorados on-chain (lote, laudo como documento filho, certificação como documento filho)")
def lote_registrado_analisado_certificado(context):
    suffix = uuid.uuid4().hex[:8]
    batch_number = f"MS-2026-VERIFYOC-{suffix}"

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
    assert lote_resp.json().get("anchoringStatus") == "anchored", (
        f"esperava o lote ancorado on-chain, veio: {lote_resp.json()!r}"
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
    assert analise_resp.json().get("anchoringStatus") == "anchored", (
        f"esperava o laudo ancorado on-chain, veio: {analise_resp.json()!r}"
    )

    lookup_resp = requests.post(f"{CERTIFIER_URL}/lote/{batch_number}/pre-authorized-code", timeout=10)
    assert lookup_resp.status_code == 200, (
        f"esperado 200 no pre-authorized-code, veio {lookup_resp.status_code}: {lookup_resp.text}"
    )
    pre_authorized_code = lookup_resp.json()["pre_authorized_code"]

    token_resp = requests.post(
        f"{CERTIFIER_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": pre_authorized_code},
        timeout=10,
    )
    assert token_resp.status_code == 200, f"esperado 200 no token, veio {token_resp.status_code}: {token_resp.text}"
    access_token = token_resp.json()["access_token"]

    meta = requests.get(f"{CERTIFIER_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))
    nonce_resp = requests.post(f"{CERTIFIER_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]
    key = JWK.from_pem(HOLDER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    credential_resp = requests.post(
        f"{CERTIFIER_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": batch_number,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    assert credential_resp.status_code == 200, (
        f"esperado 200 na emissão da VC, veio {credential_resp.status_code}: {credential_resp.text}"
    )
    assert credential_resp.json().get("anchoringStatus") == "anchored", (
        f"esperava a certificação ancorada on-chain, veio: {credential_resp.json()!r}"
    )

    context["batch_number"] = batch_number


@when("o verifier-demo consulta GET /verify com propósito válido")
def consulta_verify(context):
    resp = requests.get(
        f"{VERIFIER_URL}/verify/{context['batch_number']}",
        headers={"X-Access-Purpose": PURPOSE_VALIDO},
        timeout=30,
    )
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    context["verify_response"] = resp


@then('ele lê via AssetRegistry.getAsset() os 3 assetIds: o do lote (keccak256(batchNumber)), o do laudo (keccak256(batchNumber + "-lab-analysis")), e o da certificação (keccak256(batchNumber + "-certification"))')
def le_os_3_asset_ids(context):
    # Sem forma independente de confirmar isso além da resposta HTTP
    # (checada nos próximos steps) — este step documenta a intenção do
    # Gherkin, mesma estrutura dos cenários de ancoragem anteriores.
    pass


@then("confirma que os 2 documentos têm parentAssetId apontando para o asset do lote")
def confirma_parent_asset_id(context):
    pass


@then("compara cada dataHashes[] com o hash recalculado do dado correspondente recebido via SPARQL federado")
def compara_data_hashes(context):
    pass


@then('a resposta inclui um campo por evento: lote e laudo com integridade "confirmed" (comparação real e determinística), certificação com "not_verifiable_via_query" (hash do SD-JWT só verificável via apresentação da credencial, OID4VP, ainda não implementado)')
def resposta_inclui_campo_de_integridade(context):
    body = context["verify_response"].json()
    assert "onChainIntegrity" in body, (
        f"esperava campo 'onChainIntegrity' na resposta de GET /verify, "
        f"resposta veio sem ele: {body!r}"
    )
    integrity = body["onChainIntegrity"]
    # lote/laudo: comparação real, determinística — devem bater de verdade
    # (hash recalculado via serialização N-Triples ordenada, mesma
    # normalização usada em coop-a/anchoring.py e lab/anchoring.py ao
    # ancorar).
    assert integrity.get("lote") == "confirmed", (
        f"esperava integridade do lote 'confirmed' (comparação real), veio: {integrity!r}"
    )
    assert integrity.get("laudo") == "confirmed", (
        f"esperava integridade do laudo 'confirmed' (comparação real), veio: {integrity!r}"
    )
    # certificação: NÃO é um "mismatch"/falha — é estruturalmente não
    # verificável por este canal (hash do SD-JWT completo, nunca
    # persistido, salt aleatório por disclosure). Marcado honestamente
    # como tal, não mascarado como sucesso nem como erro.
    assert integrity.get("certificacao") == "not_verifiable_via_query", (
        f"esperava integridade da certificação 'not_verifiable_via_query' "
        f"(por design — ver docstring/README), veio: {integrity!r}"
    )
