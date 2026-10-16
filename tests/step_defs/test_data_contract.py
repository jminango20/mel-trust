"""
Step defs (pytest-bdd) para tests/features/data_contract.feature.

FASE RED (TDD): escrito e rodado ANTES de o coop-a ter POST /data-contract. Nada foi
implementado: nem rota, nem shape, nem propriedade nova na ontologia.

DECISÕES (contrato proposto, só coop-a):
- contractRef = keccak256 do JSON CANÔNICO dos termos: objeto com as chaves consumer,
  purpose, priceUnits (int), buyerWallet (EIP-55), validFrom, validUntil; chaves em ordem
  alfabética, separadores (",", ":"), UTF-8 sem escape (ensure_ascii=False), datas como
  strings ISO-8601 "...Z" exatamente como recebidas. Mesmo conteúdo -> mesmo contractRef.
  É o bytes32 que o VendaDados usará como `ref` em depositar/liberar/reembolsar.
- Determinismo: o cenário 1 confere o contractRef contra o keccak calculado AQUI, de forma
  independente; o "mesmo conteúdo, mesmo ref" também é provado no cenário 3 (o 409 devolve
  o contractRef do primeiro POST). Não há um segundo 201 com o mesmo conteúdo: o pedido
  de determinismo e o 409 do cenário 3 só convivem assim.
- Resposta: 201 {"contractRef": "0x<64 hex>"}; 409 {"error": "already_exists",
  "contractRef": "0x..."}; 400 {"error": "shacl_violation", ...} (mesmo formato de
  POST /lote). preço em `priceUnits`: inteiro na menor unidade do USDT (6 casas).
- A regra das datas vai em SHACL (`sh:lessThan`: validFrom < validUntil, estrito), na
  DataContractShape; preço > 0 e inteiro também em SHACL (`sh:minExclusive 0`). Não é
  validação em Python.
- Ontologia: reuso de agritrust:DataContract, validFrom, validUntil, hasPolicyAssignee,
  compensationValue (preço) e allowedUsage (finalidade). Nomes NOVOS propostos (não
  criados): :contractRef (xsd:string, 0x + 64 hex) e :buyerWallet (xsd:string, EIP-55) no
  bee-extension. O consumidor é um recurso (assignee) com rdfs:label = nome informado.
- Higiene: consumidor SINTÉTICO por execução, `ConsumidorTeste-<12 hex>`. O conftest apaga,
  antes e depois de cada teste, só no coop-a, os DataContract (e o recurso do consumidor)
  cujo rdfs:label do assignee casa com a allowlist ANCORADA
  `^ConsumidorTeste-[0-9a-f]{12}$`. Dado real nunca é tocado.
- @sem_besu: nada aqui usa Besu nem VendaDados.
"""
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values
from eth_account import Account
from pytest_bdd import given, parsers, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

scenarios("../features/data_contract.feature")

COOP_A_URL = "http://localhost:8001"
_PREFIX = (
    "PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>\n"
    "PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
)

_coop_a_env = dotenv_values(REPO_ROOT / "coop-a" / ".env")
FUSEKI_URL = _coop_a_env.get("FUSEKI_URL", "http://127.0.0.1:3030/coop-a")
FUSEKI_PASSWORD = _coop_a_env.get("FUSEKI_ADMIN_PASSWORD")

_VALID_FROM = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_ref(terms: dict) -> str:
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "0x" + Web3.keccak(text=canonical).hex().removeprefix("0x")


def _select(query: str) -> list[dict]:
    resp = requests.post(
        f"{FUSEKI_URL}/query",
        data={"query": _PREFIX + query},
        headers={"Accept": "application/sparql-results+json"},
        auth=("admin", FUSEKI_PASSWORD),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["results"]["bindings"]


def _contracts_of(consumer: str) -> list[dict]:
    label = consumer.replace("\\", "\\\\").replace('"', '\\"')
    return _select(
        f'SELECT ?c ?ref WHERE {{ ?c a agritrust:DataContract ; agritrust:hasPolicyAssignee ?a . '
        f'?a rdfs:label "{label}" . OPTIONAL {{ ?c :contractRef ?ref }} }}'
    )


@pytest.fixture
def context():
    return {}


@given("os termos de um contrato de dados válido com um consumidor sintético")
def _termos_validos(context):
    context["terms"] = {
        "consumer": f"ConsumidorTeste-{uuid.uuid4().hex[:12]}",
        "purpose": "Compliance EUDR",
        "priceUnits": 2_500_000,
        "buyerWallet": Account.create().address,  # já em EIP-55
        "validFrom": _iso(_VALID_FROM),
        "validUntil": _iso(_VALID_FROM + timedelta(days=365)),
    }


@given(parsers.parse('o campo "{campo}" alterado para "{valor}"'))
def _altera_campo(context, campo, valor):
    terms = context["terms"]
    if campo == "priceUnits":
        if valor == "ausente":
            del terms["priceUnits"]
        else:
            terms["priceUnits"] = int(valor)
    elif campo == "validUntil":
        if valor == "igual ao validFrom":
            terms["validUntil"] = terms["validFrom"]
        elif valor == "anterior ao validFrom":
            terms["validUntil"] = _iso(_VALID_FROM - timedelta(days=1))
        else:
            raise AssertionError(f"valor desconhecido para validUntil: {valor!r}")
    else:
        raise AssertionError(f"campo desconhecido: {campo!r}")


@given("o coop-a já recebeu POST /data-contract com esses termos")
def _primeiro_post(context):
    resp = requests.post(f"{COOP_A_URL}/data-contract", json=context["terms"], timeout=30)
    assert resp.status_code == 201, f"primeiro POST: esperado 201, veio {resp.status_code}: {resp.text}"
    context["first_ref"] = resp.json()["contractRef"]


@when("o coop-a recebe POST /data-contract com esses termos")
def _post(context):
    context["response"] = requests.post(f"{COOP_A_URL}/data-contract", json=context["terms"], timeout=30)


@then("a resposta é 201 com contractRef em bytes32 hexadecimal")
def _201(context):
    resp = context["response"]
    assert resp.status_code == 201, f"esperado 201, veio {resp.status_code}: {resp.text}"
    ref = resp.json().get("contractRef", "")
    assert len(ref) == 66 and ref.startswith("0x") and int(ref, 16) >= 0, f"contractRef não é bytes32 hex: {ref!r}"
    context["ref"] = ref


@then("o contractRef é o keccak256 do JSON canônico dos termos")
def _ref_canonico(context):
    assert context["ref"] == _canonical_ref(context["terms"])


@then("o Fuseki do coop-a guarda um DataContract com esse contractRef")
def _no_fuseki(context):
    found = _contracts_of(context["terms"]["consumer"])
    assert [b["ref"]["value"] for b in found if "ref" in b] == [context["ref"]], found


@then("a resposta é 400 com error shacl_violation")
def _400(context):
    resp = context["response"]
    assert resp.status_code == 400, f"esperado 400, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("error") == "shacl_violation", f"esperado error=shacl_violation, veio {resp.json()}"


@then("o Fuseki do coop-a não tem DataContract desse consumidor")
def _nada_inserido(context):
    assert _contracts_of(context["terms"]["consumer"]) == []


@then("a resposta é 409 com o contractRef do primeiro POST")
def _409(context):
    resp = context["response"]
    assert resp.status_code == 409, f"esperado 409, veio {resp.status_code}: {resp.text}"
    assert resp.json().get("contractRef") == context["first_ref"], resp.json()


@then(parsers.parse("o Fuseki do coop-a tem exatamente {n:d} DataContract desse consumidor"))
def _exatamente_n(context, n):
    assert len(_contracts_of(context["terms"]["consumer"])) == n
