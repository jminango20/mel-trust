"""
Pipeline de ingestão do coop-b: instância RDF -> gate SHACL -> Fuseki.

Escopo M1.5: só isso. Não lê o Fuseki de volta para emitir credencial (M3/M4),
não emite SD-JWT VC nem ancora nada em blockchain (fora de escopo aqui).

Uso:
    python pipeline.py caminho/para/instancia.ttl

Fluxo (linha a linha do que acontece):
  1. Carrega a instância (arquivo passado por argv) num rdflib.Graph — esse é
     o "dado de domínio" (ex: um lote de mel), ainda sem nenhuma garantia de
     que respeita os limites legais (umidade, HMF, acidez, diástase etc).
  2. Valida contra ontology/validation-shapes.ttl via shacl_pipeline.run_shacl
     (pyshacl.validate) — casa cada nó da instância contra sh:targetClass das
     shapes (ex: :HoneyNativeStinglessBee) e aplica as sh:property definidas
     lá.
  3. Se conforms=True: shacl_pipeline.insert_into_fuseki serializa o
     data_graph em N-Triples e manda como SPARQL Update "INSERT DATA" via
     HTTP para o endpoint /update do dataset "coop-b" no Fuseki.
  4. Se conforms=False: não insere nada; imprime cada violação (foco,
     severidade, mensagem) extraída do results_graph.

M5.6 (pós-narrativa): a validação e a inserção (passos 2-3) moveram para
shacl_pipeline.py, reaproveitadas também por POST /lote (lote_endpoint.py) —
mesmo gate, duas portas de entrada (CLI e HTTP), mesmo padrão de coop-a. Uso
e saída no terminal não mudaram.
"""
import os
import sys

from rdflib import Graph

from shacl_pipeline import SHAPES_PATH, extract_violations, format_violations, insert_into_fuseki, run_shacl


def load_data_graph(instance_path: str) -> Graph:
    g = Graph()
    g.parse(instance_path, format="turtle")
    return g


def print_violations(results_graph: Graph) -> None:
    for line in format_violations(extract_violations(results_graph)):
        print(f"  - {line}")


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
        print("SHACL: conforms=True. Inserindo no Fuseki (dataset coop-b)...")
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
