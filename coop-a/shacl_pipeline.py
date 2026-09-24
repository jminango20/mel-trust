"""
Gate SHACL compartilhado: validar um rdflib.Graph contra
ontology/validation-shapes.ttl e, se conforme, inserir no Fuseki via SPARQL
Update. Extraído de pipeline.py (M1.5) para que POST /lote (lote_endpoint.py)
use EXATAMENTE a mesma lógica, chamada via HTTP em vez de CLI — não existem
dois gates diferentes, um só, com duas portas de entrada.

pipeline.py continua funcionando como script CLI (python pipeline.py
arquivo.ttl); ele só passou a importar as funções daqui em vez de as ter
duplicadas. Comportamento observável do CLI não muda.
"""
import inspect
import os

import requests
from pyshacl import validate
from rdflib import Graph

from config import FUSEKI_ADMIN_PASSWORD, FUSEKI_ADMIN_USER, FUSEKI_URL

# nem toda versão de pyshacl tem allow_infos (foi adicionado depois de
# allow_warnings) — checa a assinatura instalada antes de passar o kwarg.
_SUPPORTS_ALLOW_INFOS = "allow_infos" in inspect.signature(validate).parameters

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHAPES_PATH = os.path.join(REPO_ROOT, "ontology", "validation-shapes.ttl")


def run_shacl(data_graph: Graph):
    shapes_graph = Graph()
    shapes_graph.parse(SHAPES_PATH, format="turtle")

    # allow_warnings/allow_infos: por padrão pyshacl derruba conforms em
    # QUALQUER sh:Warning ou sh:Info, não só sh:Violation (diferente do
    # SHACL puro, onde sh:conforms só reage a sh:Violation). Aqui só
    # sh:Violation deve bloquear a inserção; Warning/Info continuam
    # aparecendo no relatório, sem travar o gate.
    validate_kwargs = {"allow_warnings": True}
    if _SUPPORTS_ALLOW_INFOS:
        validate_kwargs["allow_infos"] = True

    conforms, results_graph, results_text = validate(
        data_graph,
        shacl_graph=shapes_graph,
        # inference="none": as shapes casam por rdf:type explícito na
        # instância (ex: :HoneyApis), sem precisar de reasoning OWL sobre
        # bee-extension.ttl para inferir subclasses/superclasses.
        inference="none",
        **validate_kwargs,
    )
    return conforms, results_graph, results_text


def extract_violations(results_graph: Graph) -> list[dict]:
    """(focusNode, severity, message) de cada resultado SHACL, como dict —
    formato consumível tanto pelo terminal (format_violations) quanto pelo
    corpo JSON de uma resposta HTTP (lote_endpoint.py)."""
    query = """
    PREFIX sh: <http://www.w3.org/ns/shacl#>
    SELECT ?focusNode ?severity ?message WHERE {
        ?result a sh:ValidationResult ;
                sh:focusNode ?focusNode ;
                sh:resultSeverity ?severity .
        OPTIONAL { ?result sh:resultMessage ?message }
    }
    """
    violations = []
    for focus_node, severity, message in results_graph.query(query):
        violations.append(
            {
                "focusNode": str(focus_node),
                "severity": str(severity).rsplit("#", 1)[-1],
                "message": str(message) if message is not None else None,
            }
        )
    return violations


def format_violations(violations: list[dict]) -> list[str]:
    """Uma linha por violação, no MESMO formato que pipeline.py já imprime
    no terminal desde M1.5: "[Severidade] foco: mensagem"."""
    lines = []
    for v in violations:
        msg = v["message"] if v["message"] is not None else "(sem sh:resultMessage)"
        lines.append(f"[{v['severity']}] {v['focusNode']}: {msg}")
    return lines


def insert_into_fuseki(data_graph: Graph) -> None:
    """SPARQL Update INSERT DATA do conteúdo de data_graph no dataset coop-a."""
    if not FUSEKI_ADMIN_PASSWORD:
        raise RuntimeError(
            "FUSEKI_ADMIN_PASSWORD não definido (configure coop-a/.env a "
            "partir de .env.example)."
        )

    triples_nt = data_graph.serialize(format="nt")
    update_query = f"INSERT DATA {{\n{triples_nt}\n}}"

    response = requests.post(
        f"{FUSEKI_URL}/update",
        data={"update": update_query},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=30,
    )
    response.raise_for_status()
