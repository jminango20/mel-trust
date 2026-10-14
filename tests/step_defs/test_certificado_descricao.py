"""
Step defs (pytest-bdd) para tests/features/certificado_descricao.feature.

pyshacl direto contra ontology/validation-shapes.ttl; sem nós nem Fuseki.
"""
from pathlib import Path

import pytest
from pyshacl import validate
from pytest_bdd import given, parsers, scenarios, then, when
from rdflib import Graph

REPO_ROOT = Path(__file__).resolve().parents[2]
SHAPES_PATH = REPO_ROOT / "ontology" / "validation-shapes.ttl"

scenarios("../features/certificado_descricao.feature")

_CERT = """
@prefix : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#> .
@prefix agritrust: <https://w3id.org/agrisemantics/AGRITRUST#> .
@prefix vc: <https://www.w3.org/2018/credentials/v1> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
:Cert_Teste a agritrust:Certificate ;
    vc:issuer :Certificadora_Teste ;
    vc:credentialSubject :Lote_Teste ;
    vc:issuanceDate "2026-10-14"^^xsd:date %s.
"""


@pytest.fixture
def context():
    return {}


@given("um certificado completo, mas sem descrição")
def sem_descricao(context):
    context["data"] = _CERT % ""


@given("um certificado completo, com descrição")
def com_descricao(context):
    context["data"] = _CERT % ';\n    rdfs:comment "Certificado de análise nº 123, hash 0xabc" '


@when("o grafo é validado contra validation-shapes.ttl")
def valida(context):
    data = Graph().parse(data=context["data"], format="turtle")
    shapes = Graph().parse(SHAPES_PATH, format="turtle")
    conforms, _, text = validate(data, shacl_graph=shapes, inference="none")
    context["conforms"], context["report"] = conforms, text


@then(parsers.parse('há violação com a mensagem "{mensagem}"'))
def violacao(context, mensagem):
    assert not context["conforms"], "esperava violação, mas o grafo está conforme"
    assert mensagem in context["report"], f"mensagem ausente do relatório:\n{context['report']}"


@then("o grafo está conforme")
def conforme(context):
    assert context["conforms"], f"esperava conformidade:\n{context['report']}"
