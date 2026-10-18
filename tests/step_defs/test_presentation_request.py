"""
Step defs (pytest-bdd) para tests/features/presentation_request.feature.

FASE RED (TDD): escrito ANTES de qualquer código novo. Hoje
`verifier-demo` só tem `GET /health` e `GET /verify/{batch_number}` (ver
`verifier-demo/main.py`/`verify_endpoint.py`) — nenhum endpoint
`POST /presentation-request/{batch_number}` existe. Primeiro passo de
OID4VP (apresentação de credencial pelo holder, ver
docs/notas-arquitetura/m5-verificacao-assinatura-adiada.md, que adia essa
verificação pra depois de M5) — este cenário testa só a CRIAÇÃO da
solicitação (nonce único por solicitação), não a apresentação em si.
"""
import sys
from pathlib import Path

import pytest
import requests
from pytest_bdd import given, scenarios, then, when

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/presentation_request.feature")

VERIFIER_URL = "http://127.0.0.1:8004"
BATCH_NUMBER = "MS-2026-042"


@pytest.fixture
def context():
    return {}


@given("que o verifier-demo quer verificar a certificação do lote MS-2026-042")
def verifier_quer_verificar(context):
    context["batch_number"] = BATCH_NUMBER


@when("ele chama POST /presentation-request/{batch_number}")
def chama_presentation_request(context):
    context["response"] = requests.post(
        f"{VERIFIER_URL}/presentation-request/{context['batch_number']}",
        timeout=10,
    )


@then("recebe de volta um nonce único, vinculado a essa solicitação específica")
def recebe_nonce_unico(context):
    resp = context["response"]
    assert resp.status_code == 200, (
        f"esperado 200 em POST /presentation-request/{{batch_number}}, "
        f"veio {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "nonce" in body, f"esperava campo 'nonce' na resposta, veio: {body!r}"
    assert body["nonce"], f"'nonce' presente mas vazio/None: {body['nonce']!r}"
    context["nonce"] = body["nonce"]


@then("esse nonce ainda não foi usado por nenhuma apresentação")
def nonce_nao_usado(context):
    # Sem endpoint de apresentação ainda (fora do escopo deste cenário) —
    # a checagem possível aqui é que o nonce recém-criado não vem marcado
    # como usado no corpo da resposta, se esse campo existir.
    body = context["response"].json()
    assert body.get("used", False) is False, (
        f"esperava o nonce recém-criado como NÃO usado, veio 'used'={body.get('used')!r}"
    )
