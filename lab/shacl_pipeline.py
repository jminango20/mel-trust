"""
Gate SHACL do nó lab: validar um rdflib.Graph contra
ontology/lab-analysis-shape.ttl (forma/plausibilidade da análise, não regra
regulatória) e, se conforme, inserir no Fuseki via SPARQL Update.

Cópia adaptada de coop-a/shacl_pipeline.py — MESMA estrutura (run_shacl,
extract_violations, format_violations, insert_into_fuseki), com uma única
mudança central: SHAPES_PATH aponta para lab-analysis-shape.ttl em vez de
validation-shapes.ttl. Ver docstring daquele arquivo (ontology/) para a
justificativa completa da separação.
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
SHAPES_PATH = os.path.join(REPO_ROOT, "ontology", "lab-analysis-shape.ttl")


def run_shacl(data_graph: Graph):
    shapes_graph = Graph()
    shapes_graph.parse(SHAPES_PATH, format="turtle")

    # allow_warnings/allow_infos: aqui todas as shapes de lab-analysis-shape.ttl
    # são sh:Violation (gate de entrada, sem Info/Warning) — mantido por
    # consistência com o pipeline dos outros nós, sem efeito observável hoje.
    validate_kwargs = {"allow_warnings": True}
    if _SUPPORTS_ALLOW_INFOS:
        validate_kwargs["allow_infos"] = True

    conforms, results_graph, results_text = validate(
        data_graph,
        shacl_graph=shapes_graph,
        # inference="none": a shape casa por rdf:type explícito
        # (lab:AnaliseLaboratorial) na instância, sem precisar de reasoning
        # OWL.
        inference="none",
        **validate_kwargs,
    )
    return conforms, results_graph, results_text


def extract_violations(results_graph: Graph) -> list[dict]:
    """(focusNode, severity, message) de cada resultado SHACL, como dict —
    formato consumível tanto pelo terminal (format_violations) quanto pelo
    corpo JSON de uma resposta HTTP (analise_endpoint.py)."""
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
    """Uma linha por violação: "[Severidade] foco: mensagem"."""
    lines = []
    for v in violations:
        msg = v["message"] if v["message"] is not None else "(sem sh:resultMessage)"
        lines.append(f"[{v['severity']}] {v['focusNode']}: {msg}")
    return lines


def insert_into_fuseki(data_graph: Graph) -> None:
    """SPARQL Update INSERT DATA do conteúdo de data_graph no dataset lab."""
    if not FUSEKI_ADMIN_PASSWORD:
        raise RuntimeError(
            "FUSEKI_ADMIN_PASSWORD não definido (configure lab/.env a "
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
