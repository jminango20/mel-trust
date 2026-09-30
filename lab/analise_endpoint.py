"""
POST /analise — recebe uma análise de laboratório e grava no Fuseki do nó
lab, depois de passar pelo gate SHACL (ontology/lab-analysis-shape.ttl).

HISTÓRICO: este endpoint chegou a ser renomeado para POST /avaliar e perder
a persistência (lab virou stateless por um turno) — REVERTIDO. Voltou a se
chamar /analise e a persistir no Fuseki do lab, como era originalmente. Ver
docs/known-issues.md/CHANGELOG.md.

Este nó NÃO emite credencial nem participa do fluxo OID4VCI — quem consome
esse dado é o certifier, via SPARQL federado (SERVICE contra o Fuseki deste
nó), tarefa separada. Aqui só existe entrada + validação + persistência.

Campos obrigatórios no corpo JSON:
  - batchNumber (string)
  - moistureContent, hmfContent, acidityContent, diastaseActivity (number:
    int ou float)

Abordagem de validação escolhida (documentada por exigência do escopo):
os 4 campos numéricos são checados em Python (isinstance int/float, não
bool) ANTES de montar o grafo RDF — não deixamos passar string para o SHACL
pegar via sh:datatype. Razão: um erro de tipo (`"moistureContent": "dezoito"`)
é um erro de formato de REQUISIÇÃO, não uma questão de conteúdo semântico —
faz mais sentido reportar como `invalid_request` (400, mesmo contrato de
`lote_endpoint.py` dos outros nós) do que rodar o pipeline SHACL inteiro só
para descobrir isso. O gate SHACL fica exclusivamente para o caso em que o
JSON está bem formado mas o campo está ausente/vazio, ou para qualquer outra
regra de forma que venha a ser adicionada em lab-analysis-shape.ttl no
futuro sem precisar tocar neste endpoint.

URI determinística por batchNumber (não blank node): reenviar a mesma
análise não deve criar um recurso novo a cada chamada, e sim reafirmar as
mesmas triplas (INSERT DATA é idempotente para triplas idênticas) — mesma
razão da correção de duplicação de geo:Point já aplicada em coop-a/coop-b
(ver CHANGELOG.md).
"""
import logging
import re

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rdflib import Graph, Literal, Namespace
from rdflib.namespace import RDF, XSD
from web3 import Web3

from anchoring import anchor_lab_analysis
from config import (
    COOP_A_SPARQL_SERVICE_URL,
    COOP_B_SPARQL_SERVICE_URL,
    FUSEKI_ADMIN_PASSWORD,
    FUSEKI_ADMIN_USER,
    FUSEKI_URL,
)
from shacl_pipeline import extract_violations, format_violations, insert_into_fuseki, run_shacl

router = APIRouter()
logger = logging.getLogger(__name__)

LAB = Namespace("https://w3id.org/agrisemantics/AGRITRUST/lab-extension#")
BEE = Namespace("https://w3id.org/agrisemantics/AGRITRUST/bee-extension#")
AGRITRUST = Namespace("https://w3id.org/agrisemantics/AGRITRUST#")

_REQUIRED_NUMERIC_FIELDS = (
    "moistureContent",
    "hmfContent",
    "acidityContent",
    "diastaseActivity",
)


def _error(status_code: int, error: str, description: str, extra: dict | None = None) -> JSONResponse:
    body = {"error": error, "error_description": description}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _slug(value: str) -> str:
    """Sanitiza um valor para usar como segmento de URI (local name) — mesma
    função de coop-a/lote_endpoint.py."""
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _is_number(value) -> bool:
    # bool é subclasse de int em Python — excluído explicitamente para não
    # aceitar `"moistureContent": true` como número válido.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --- Resolução do productType do lote, para a ancoragem on-chain (M8/M9) ---
#
# POST /analise só recebe batchNumber — não o productType
# ("HoneyApis"/"HoneyNativeStinglessBee") —, mas anchoring.py precisa dele
# como natureId do TransactionRequest. Mesmo padrão de
# certifier/credential_endpoint.py:_query_federated (SERVICE UNION
# coop-a/coop-b), só que aqui buscando SÓ productType, rodada contra o
# PRÓPRIO Fuseki do lab (nó de entrada desta query federada).
_PRODUCT_TYPE_QUERY_TEMPLATE = """
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
SELECT ?batch ?productType WHERE {
  {
    SERVICE <%(coop_a_service_url)s> {
      ?batch agritrust:batchNumber %(batch_number)s ;
             a ?productType .
    }
  }
  UNION
  {
    SERVICE <%(coop_b_service_url)s> {
      ?batch agritrust:batchNumber %(batch_number)s ;
             a ?productType .
    }
  }
}
ORDER BY ?batch
LIMIT 1
"""


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Mesmo nível de escape simples de certifier/credential_endpoint.py (não
    é um escapador SPARQL genérico e completo) — ver docs/known-issues.md.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _resolve_product_type(batch_number: str) -> str | None:
    """Roda a query federada (UNION coop-a/coop-b) contra o Fuseki de
    ENTRADA (o próprio lab) para descobrir o productType do lote.

    Retorna None se o lote não foi encontrado em nenhuma das duas
    cooperativas — o chamador decide o que fazer (aqui, pular a ancoragem
    com anchoringStatus="anchoring_skipped"). Pode levantar
    `requests.RequestException` se algum Fuseki estiver fora do ar — o
    chamador (`registrar_analise`) envolve a chamada num try/except amplo,
    então isso também vira anchoring_skipped/anchoring_failed, nunca quebra
    a resposta HTTP.
    """
    query = _PRODUCT_TYPE_QUERY_TEMPLATE % {
        "batch_number": _sparql_literal(batch_number),
        "coop_a_service_url": COOP_A_SPARQL_SERVICE_URL,
        "coop_b_service_url": COOP_B_SPARQL_SERVICE_URL,
    }

    response = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=30,
    )
    response.raise_for_status()

    bindings = response.json().get("results", {}).get("bindings", [])
    if not bindings:
        return None

    product_type_cell = bindings[0].get("productType")
    if not product_type_cell:
        return None

    # productType vem como URI completa (ex.:
    # ".../bee-extension#HoneyApis") — só o local name interessa como
    # natureId, mesmo formato ("HoneyApis") usado em coop-a/anchoring.py e
    # nos processos honey-lab-analysis já registrados no ProcessRegistry.
    return product_type_cell["value"].rsplit("#", 1)[-1]


@router.post("/analise")
async def registrar_analise(request: Request):
    """Registra uma análise laboratorial. Ver docstring do módulo para o
    contrato completo (campos aceitos, gate SHACL, o que valida onde)."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_request", "Corpo da requisição não é JSON válido.")

    if not isinstance(body, dict):
        return _error(400, "invalid_request", "Corpo da requisição deve ser um objeto JSON.")

    batch_number = body.get("batchNumber")
    if not batch_number or not isinstance(batch_number, str):
        return _error(400, "invalid_request", "Campo obrigatório ausente ou inválido: batchNumber (string).")

    missing = [f for f in _REQUIRED_NUMERIC_FIELDS if f not in body]
    if missing:
        return _error(
            400,
            "invalid_request",
            f"Campos obrigatórios ausentes: {', '.join(missing)}.",
        )

    invalid_type = [f for f in _REQUIRED_NUMERIC_FIELDS if not _is_number(body.get(f))]
    if invalid_type:
        return _error(
            400,
            "invalid_request",
            f"Campos que precisam ser numéricos (int ou float): {', '.join(invalid_type)}.",
        )

    moisture_content = body["moistureContent"]
    hmf_content = body["hmfContent"]
    acidity_content = body["acidityContent"]
    diastase_activity = body["diastaseActivity"]

    # --- 1. Monta o grafo RDF ---
    g = Graph()
    g.bind("agritrust", AGRITRUST)

    analise_uri = BEE[f"Analise_{_slug(batch_number)}"]

    # Literal(str(valor), datatype=XSD.decimal), não Literal(valor, ...): rdflib
    # constrói o literal a partir do valor Python (float) e marca `ill_typed`
    # como None (não confirmado) em vez de False (confirmado bem-tipado) —
    # pyshacl's DatatypeConstraintComponent trata `None` como reprovado.
    # Passando a string já formatada, rdflib usa o construtor lexical padrão
    # de xsd:decimal e o literal fica corretamente `ill_typed=False`. Achado
    # real ao rodar os testes deste endpoint, não hipotético.
    g.add((analise_uri, RDF.type, LAB.AnaliseLaboratorial))
    g.add((analise_uri, AGRITRUST.batchNumber, Literal(batch_number)))
    g.add((analise_uri, BEE.hasMoistureContent, Literal(str(moisture_content), datatype=XSD.decimal)))
    g.add((analise_uri, BEE.hasHMFContent, Literal(str(hmf_content), datatype=XSD.decimal)))
    g.add((analise_uri, BEE.hasAcidity, Literal(str(acidity_content), datatype=XSD.decimal)))
    g.add((analise_uri, BEE.hasDiastaseActivity, Literal(str(diastase_activity), datatype=XSD.decimal)))

    # --- 2. Gate SHACL (ontology/lab-analysis-shape.ttl) ---
    conforms, results_graph, _ = run_shacl(g)
    if not conforms:
        violations = extract_violations(results_graph)
        return _error(
            400,
            "shacl_violation",
            "Análise não passou na validação SHACL. Nada foi inserido.",
            extra={"violations": format_violations(violations)},
        )

    # --- 3. Inserção no Fuseki ---
    try:
        insert_into_fuseki(g)
    except Exception as exc:  # noqa: BLE001 - mesmo nível de reporte dos outros nós
        return _error(502, "server_error", f"Erro ao inserir no Fuseki: {exc}")

    # --- 4. Ancoragem on-chain do LAUDO (M8/M9) — melhor esforço, NUNCA
    # reverte a inserção no Fuseki (já commitada no passo 3 acima) nem
    # quebra esta resposta HTTP: sucesso ou falha só mudam
    # `anchoringStatus`/campos associados, sempre presentes explicitamente
    # (mesma decisão do usuário aplicada em coop-a/coop-b, ver
    # tests/features/ancoragem_laudo.feature).
    #
    # Precisa do productType do lote (natureId do processo
    # honey-lab-analysis) — resolvido via SPARQL federado contra
    # coop-a/coop-b (ver _resolve_product_type acima). Se o lote não for
    # encontrado em nenhuma das duas, a ancoragem não é tentada.
    #
    # `dataHashes` usa o keccak256 do MESMO objeto `g` (rdflib.Graph) em
    # memória, serializado como N-Triples — não uma nova consulta SPARQL ao
    # Fuseki — para que o hash ancorado corresponda exatamente ao grafo que
    # este endpoint acabou de montar e inserir.
    anchoring_status = "anchoring_skipped"
    tx_hash = None
    anchoring_error = None
    try:
        product_type = _resolve_product_type(batch_number)
        if product_type is None:
            anchoring_error = (
                f"productType do lote {batch_number!r} não encontrado em coop-a "
                f"nem coop-b — ancoragem do laudo pulada."
            )
            logger.info(
                "POST /analise: %s (anchoringStatus=anchoring_skipped).",
                anchoring_error,
            )
        else:
            data_hash = Web3.keccak(g.serialize(format="nt").encode("utf-8"))
            success, tx_hash, anchoring_error = anchor_lab_analysis(
                batch_number, product_type, data_hash
            )
            if success:
                anchoring_status = "anchored"
                logger.info(
                    "POST /analise: laudo do lote %r ancorado on-chain, txHash=%s",
                    batch_number, tx_hash,
                )
            else:
                anchoring_status = "anchoring_failed"
                logger.error(
                    "POST /analise: falha ao ancorar laudo do lote %r on-chain: %s",
                    batch_number, anchoring_error,
                )
    except Exception as exc:  # noqa: BLE001 - análise já inserida, nunca quebra a resposta HTTP
        anchoring_status = "anchoring_failed"
        anchoring_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "POST /analise: erro inesperado ao tentar ancorar laudo do lote %r: %s",
            batch_number, anchoring_error,
        )

    response_body = {
        "status": "inserted",
        "batchNumber": batch_number,
        "analise": str(analise_uri),
        "anchoringStatus": anchoring_status,
    }
    if tx_hash is not None:
        response_body["txHash"] = tx_hash
    if anchoring_error is not None:
        response_body["anchoringError"] = anchoring_error

    return response_body
