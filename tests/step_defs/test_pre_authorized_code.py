"""
Step defs (pytest-bdd) para tests/features/pre_authorized_code.feature.

GREEN: implementado em coop-a/pre_auth_codes.py (store em memória,
code -> {batch_number, usado}), coop-a/lote_endpoint.py (gera o código na
criação do lote + POST /lote/{batchNumber}/reemitir-codigo) e
coop-a/token_endpoint.py (consome o código dinâmico no /token; o código
FIXO legado config.PRE_AUTHORIZED_CODE continua aceito como bypass de
dev/teste, nunca consumido — necessário pra lotes inseridos fora de
POST /lote, ex. fixture .ttl de MS-2026-042). Réplica idêntica em coop-b/
(não exercida por este arquivo, que só chama coop-a).

FASE RED original (histórico): antes desta implementação, POST /lote não
devolvia `pre_authorized_code`, POST /token aceitava só o código fixo sem
checagem de uso único, e POST /lote/{batchNumber}/reemitir-codigo não
existia (404) — os 2 cenários falhavam por esses motivos exatos.

1 AJUSTE DE FORMATAÇÃO no .feature (não de conteúdo): o passo "Quando a
cooperativa solicita um novo código... via POST /lote/{batchNumber}/
reemitir-codigo" veio quebrado em 2 linhas físicas no texto colado pelo
usuário — Gherkin exige 1 step por linha física (mesmo bug já encontrado e
corrigido em lab_certifier.feature e rastreabilidade_mel.feature nesta
sessão). Unido em uma linha só, texto idêntico.

1 DESVIO DELIBERADO no Cenário 2 (ver docstring do step
`lote_existente_com_codigo` abaixo): usa um batchNumber sintético novo, não
o "MS-2026-042" literal do texto original — MS-2026-042 é fixture
pré-carregada, sem código dinâmico associado, e não pode ser recriada via
/lote (409, idempotência de negócio).
"""
import sys
import uuid
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from jwcrypto.jwk import JWK
from pytest_bdd import given, parsers, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from scripts.test_credential_flow import GRANT_TYPE, build_proof_jwt  # noqa: E402

scenarios("../features/pre_authorized_code.feature")

COOP_A_URL = "http://127.0.0.1:8001"
PRODUCER_NAME = "João da Silva"
PRODUCER_DID = "did:key:z6MkhssBusXfHjvnvquNJjwQaVYTGsvgY9otdhLhKyCaXJT9"
PRODUCER_KEY_PATH = REPO_ROOT / "fixtures" / "producer-keys" / "producer-coop-a.pem"

BATCH_NUMBER_EXISTENTE = "MS-2026-042"

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
COOP_A_FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
COOP_A_FUSEKI_ADMIN_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")


def _apaga_lote_no_fuseki(batch_number: str) -> None:
    """Remove do Fuseki do coop-a qualquer recurso com este batchNumber —
    limpeza dos lotes SINTÉTICOS (uuid) criados pelos cenários deste
    arquivo, pra não deixar resíduo entre execuções (mesmo cuidado que já
    causou retrabalho manual em turnos anteriores desta sessão)."""
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
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


@pytest.fixture
def context():
    return {}


@pytest.fixture(autouse=True)
def _limpa_lotes_sinteticos(context):
    yield
    for batch_number in context.get("_lotes_criados", []):
        _apaga_lote_no_fuseki(batch_number)


def _cadastra_lote(batch_number: str, floral_origin: str | None = None) -> requests.Response:
    body = {
        "batchNumber": batch_number,
        "productType": "HoneyApis",
        "producerName": PRODUCER_NAME,
        "producerDID": PRODUCER_DID,
    }
    if floral_origin:
        body["floralOrigin"] = floral_origin
    return requests.post(f"{COOP_A_URL}/lote", json=body, timeout=20)


def _pede_token(pre_authorized_code: str) -> requests.Response:
    return requests.post(
        f"{COOP_A_URL}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": pre_authorized_code},
        timeout=10,
    )


def _emite_credencial(batch_number: str, access_token: str) -> requests.Response:
    meta = requests.get(f"{COOP_A_URL}/.well-known/openid-credential-issuer", timeout=10).json()
    config_id = next(iter(meta["credential_configurations_supported"]))

    nonce_resp = requests.post(f"{COOP_A_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]

    key = JWK.from_pem(PRODUCER_KEY_PATH.read_bytes())
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    return requests.post(
        f"{COOP_A_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": batch_number,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )


# --- Cenário 1: código único por entrega -------------------------------------


@given("que o produtor João entrega um novo lote de mel silvestre")
def novo_lote(context):
    # Lote genuinamente NOVO a cada execução (uuid), pra não colidir com
    # nenhum fixture/execução anterior — este cenário testa a GERAÇÃO de um
    # código novo, não a reafirmação de um lote já existente (esse é o
    # Cenário 2).
    batch_number = f"MEL-SILVESTRE-{uuid.uuid4().hex[:8]}"
    context.setdefault("_lotes_criados", []).append(batch_number)
    context["batch_number"] = batch_number


@when("a cooperativa cadastra o lote via POST /lote")
def cadastra_lote(context):
    context["lote_response"] = _cadastra_lote(context["batch_number"], floral_origin="Silvestre")


@then("a resposta inclui um pre_authorized_code único para esse lote")
def confere_codigo_unico(context):
    resp = context["lote_response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "pre_authorized_code" in body, (
        f"resposta de POST /lote não tem campo 'pre_authorized_code' "
        f"(funcionalidade não implementada ainda): {body!r}"
    )
    codigo = body["pre_authorized_code"]
    # "Único para esse lote": não pode ser o código fixo/genérico legado.
    assert codigo != "coop-a-demo-pre-auth-code", (
        f"pre_authorized_code veio igual ao valor fixo legado ({codigo!r}) — "
        f"esperado um código único gerado para este lote específico."
    )
    context["pre_authorized_code"] = codigo


@when("esse código é usado uma vez via /credential")
def usa_codigo_uma_vez(context):
    token_resp = _pede_token(context["pre_authorized_code"])
    assert token_resp.status_code == 200, f"esperado 200 no /token, veio {token_resp.status_code}: {token_resp.text}"
    access_token = token_resp.json()["access_token"]
    context["credencial_response"] = _emite_credencial(context["batch_number"], access_token)


@then("a credencial é emitida com sucesso")
def credencial_emitida(context):
    resp = context["credencial_response"]
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    assert "credential" in resp.json()


@then("uma segunda tentativa de usar o MESMO código é rejeitada")
def segunda_tentativa_rejeitada(context):
    segunda_resp = _pede_token(context["pre_authorized_code"])
    assert segunda_resp.status_code >= 400, (
        f"esperava rejeição (>=400) na segunda troca do MESMO pre_authorized_code "
        f"(uso único) — veio {segunda_resp.status_code}: {segunda_resp.text}"
    )


# --- Cenário 2: reemissão de código -------------------------------------------


@given("que o lote MS-2026-042 tem um pre_authorized_code ainda não utilizado")
def lote_existente_com_codigo(context):
    # DESVIO DELIBERADO do batchNumber literal do texto original
    # ("MS-2026-042"): esse lote já existe como fixture pré-carregada (fora
    # do fluxo POST /lote desta sessão de testes), então nunca teve nenhum
    # pre_authorized_code DINÂMICO associado (pre_auth_codes.py é em
    # memória, por processo, só populado por chamadas reais a POST /lote) —
    # e não dá pra recriá-lo via /lote pra "ganhar" um código, porque isso
    # bate na idempotência de negócio (409, ver lote_idempotente.feature).
    # Pra testar de verdade "lote com código ainda não utilizado", cadastra
    # um lote NOVO (uuid) via POST /lote — mesmo padrão do Cenário 1 deste
    # arquivo — e trata esse lote como o sujeito do cenário. Reportado aqui,
    # não escondido.
    batch_number = f"MS-2026-042-REEMISSAO-{uuid.uuid4().hex[:8]}"
    resp = _cadastra_lote(batch_number)
    assert resp.status_code == 200, f"esperado 200, veio {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "pre_authorized_code" in body, (
        f"resposta de POST /lote não tem campo 'pre_authorized_code' "
        f"(funcionalidade não implementada ainda): {body!r}"
    )
    context.setdefault("_lotes_criados", []).append(batch_number)
    context["batch_number"] = batch_number
    context["codigo_antigo"] = body["pre_authorized_code"]


@when(parsers.parse("a cooperativa solicita um novo código para esse mesmo lote via POST /lote/{batch_number}/reemitir-codigo"))
def solicita_reemissao(context, batch_number):
    context["reemissao_response"] = requests.post(
        f"{COOP_A_URL}/lote/{context['batch_number']}/reemitir-codigo", timeout=20
    )


@then("o código antigo é invalidado (mesmo que nunca tenha sido usado)")
def codigo_antigo_invalidado(context):
    resp = context["reemissao_response"]
    assert resp.status_code == 200, (
        f"esperado 200 em POST /lote/{{batchNumber}}/reemitir-codigo "
        f"(endpoint provavelmente não existe ainda) — veio {resp.status_code}: {resp.text}"
    )
    token_resp = _pede_token(context["codigo_antigo"])
    assert token_resp.status_code >= 400, (
        f"esperava rejeição (>=400) do código antigo após reemissão — "
        f"veio {token_resp.status_code}: {token_resp.text}"
    )


@then("um novo código é gerado, válido para o mesmo lote")
def novo_codigo_valido(context):
    body = context["reemissao_response"].json()
    assert "pre_authorized_code" in body
    novo_codigo = body["pre_authorized_code"]
    assert novo_codigo != context["codigo_antigo"]
    token_resp = _pede_token(novo_codigo)
    assert token_resp.status_code == 200, f"esperado 200, veio {token_resp.status_code}: {token_resp.text}"
    context["codigo_novo"] = novo_codigo


@then("o código antigo, se usado depois, é rejeitado")
def codigo_antigo_rejeitado_de_novo(context):
    token_resp = _pede_token(context["codigo_antigo"])
    assert token_resp.status_code >= 400, (
        f"esperava rejeição (>=400) do código antigo — veio "
        f"{token_resp.status_code}: {token_resp.text}"
    )
