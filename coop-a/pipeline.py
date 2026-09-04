"""
Pipeline de ingestão do coop-a: instância RDF -> gate SHACL -> Fuseki.

Escopo M1.5: só isso. Não lê o Fuseki de volta para emitir credencial (M3/M4),
não emite SD-JWT VC nem ancora nada em blockchain (fora de escopo aqui).

Uso:
    python pipeline.py caminho/para/instancia.ttl

Fluxo (linha a linha do que acontece):
  1. Carrega a instância (arquivo passado por argv) num rdflib.Graph — esse é
     o "dado de domínio" (ex: um lote de mel), ainda sem nenhuma garantia de
     que respeita os limites legais (umidade, HMF, acidez, diástase etc).
  2. Carrega ontology/validation-shapes.ttl (raiz do repo) como shapes graph
     SHACL. pyshacl.validate(data_graph, shacl_graph=shapes_graph) casa cada
     nó da instância contra sh:targetClass das shapes (ex: :HoneyApis) e
     aplica as sh:property (sh:maxInclusive, sh:minCount etc.) definidas lá.
     Isso não precisa de reasoning/import de bee-extension.ttl porque a
     instância já declara o rdf:type completo (:HoneyApis) diretamente.
  3. Se conforms=True: serializa só o data_graph (não as shapes) em N-Triples
     e manda como SPARQL Update "INSERT DATA" via HTTP para o endpoint
     /update do dataset "coop-a" no Fuseki, autenticado com admin:senha.
  4. Se conforms=False: não insere nada; percorre o results_graph (grafo
     SHACL de validação, também RDF) via SPARQL local simples para extrair
     sh:resultMessage + sh:resultSeverity de cada violação e imprime de
     forma legível.

Por que requests (e não SPARQLWrapper) para o passo 3: o dataset "coop-a" do
Fuseki expõe um endpoint HTTP comum (POST /coop-a/update, corpo
"update=<query>", Basic Auth). requests já é suficiente para isso sem
dependência adicional de abstração SPARQL — SPARQLWrapper adicionaria uma
camada só para compor a mesma requisição HTTP que já sabemos montar à mão,
e este pipeline só faz UPDATE (não faz SELECT/CONSTRUCT com resultados
tabulares, que é onde SPARQLWrapper compensa mais). Por isso a dependência
nova é `requests`, não `SPARQLWrapper`.
"""
import inspect
import os
import sys

import requests
from dotenv import load_dotenv
from pyshacl import validate
from rdflib import Graph
from rdflib.namespace import SH

# nem toda versão de pyshacl tem allow_infos (foi adicionado depois de
# allow_warnings) — checa a assinatura instalada antes de passar o kwarg.
_SUPPORTS_ALLOW_INFOS = "allow_infos" in inspect.signature(validate).parameters

load_dotenv()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHAPES_PATH = os.path.join(REPO_ROOT, "ontology", "validation-shapes.ttl")

FUSEKI_URL = os.environ.get("FUSEKI_URL", "http://localhost:3030/coop-a")
FUSEKI_ADMIN_USER = os.environ.get("FUSEKI_ADMIN_USER", "admin")
FUSEKI_ADMIN_PASSWORD = os.environ.get("FUSEKI_ADMIN_PASSWORD")


def load_data_graph(instance_path: str) -> Graph:
    g = Graph()
    g.parse(instance_path, format="turtle")
    return g


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


def print_violations(results_graph: Graph) -> None:
    """Extrai (foco, severidade, mensagem) de cada resultado SHACL e imprime."""
    query = """
    PREFIX sh: <http://www.w3.org/ns/shacl#>
    SELECT ?focusNode ?severity ?message WHERE {
        ?result a sh:ValidationResult ;
                sh:focusNode ?focusNode ;
                sh:resultSeverity ?severity .
        OPTIONAL { ?result sh:resultMessage ?message }
    }
    """
    rows = list(results_graph.query(query))
    if not rows:
        print("  (nenhum detalhe estruturado encontrado no relatório SHACL)")
        return
    for focus_node, severity, message in rows:
        severity_label = str(severity).rsplit("#", 1)[-1]
        msg = message if message is not None else "(sem sh:resultMessage)"
        print(f"  - [{severity_label}] {focus_node}: {msg}")


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


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Uso: python {os.path.basename(__file__)} caminho/para/instancia.ttl")
        sys.exit(2)

    instance_path = sys.argv[1]
    if not os.path.isfile(instance_path):
        print(f"Arquivo não encontrado: {instance_path}")
        sys.exit(2)

    print(f"Carregando instância: {instance_path}")
    data_graph = load_data_graph(instance_path)
    print(f"  {len(data_graph)} triplas carregadas.")

    print(f"Validando contra shapes: {SHAPES_PATH}")
    conforms, results_graph, results_text = run_shacl(data_graph)

    if conforms:
        print("SHACL: conforms=True. Inserindo no Fuseki (dataset coop-a)...")
        try:
            insert_into_fuseki(data_graph)
        except Exception as exc:  # noqa: BLE001 - reporte simples de CLI
            print(f"ERRO ao inserir no Fuseki: {exc}")
            sys.exit(1)
        print("OK: dados inseridos com sucesso.")
    else:
        print("SHACL: conforms=False. Inserção REJEITADA. Violações:")
        print_violations(results_graph)
        sys.exit(1)


if __name__ == "__main__":
    main()
