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

from anchoring import (
    anchor_lab_analysis,
    get_onchain_data_hash,
    is_known_transaction_error,
    parent_exists_onchain,
)
from config import (
    COOP_A_SPARQL_SERVICE_URL,
    COOP_B_SPARQL_SERVICE_URL,
    FUSEKI_ADMIN_PASSWORD,
    FUSEKI_ADMIN_USER,
    FUSEKI_URL,
)
from hash_utils import deterministic_nt_hash
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


# --- HASH DO LAUDO: UM caminho só ------------------------------------------
# Mesmo CONSTRUCT de verifier-demo/onchain_verification.py
# (`_LAUDO_CONSTRUCT_TEMPLATE`), rodado contra o PRÓPRIO Fuseki do lab. É usado
# por POST /analise (depois da inserção) E por POST /anchoring/retry: o hash
# ancorado é, por construção, o que um verificador externo recalcula, e retry
# e POST nunca divergem entre si.
_LAUDO_CONSTRUCT_TEMPLATE = """
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX bee: <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX lab: <https://w3id.org/agrisemantics/AGRITRUST/lab-extension#>
CONSTRUCT {
  ?analise a lab:AnaliseLaboratorial .
  ?analise agritrust:batchNumber ?batchNumber .
  ?analise bee:hasMoistureContent ?moisture .
  ?analise bee:hasHMFContent ?hmf .
  ?analise bee:hasAcidity ?acidity .
  ?analise bee:hasDiastaseActivity ?diastase .
}
WHERE {
  ?analise a lab:AnaliseLaboratorial ;
           agritrust:batchNumber %(batch_number)s ;
           bee:hasMoistureContent ?moisture ;
           bee:hasHMFContent ?hmf ;
           bee:hasAcidity ?acidity ;
           bee:hasDiastaseActivity ?diastase .
  BIND(%(batch_number)s AS ?batchNumber)
}
"""


def compute_laudo_hash(batch_number: str) -> bytes:
    """CONSTRUCT do laudo no Fuseki do lab + `deterministic_nt_hash`. Levanta
    `LookupError` se o laudo não está no Fuseki."""
    query = _LAUDO_CONSTRUCT_TEMPLATE % {"batch_number": _sparql_literal(batch_number)}
    response = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/n-triples"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    response.raise_for_status()
    graph = Graph()
    graph.parse(data=response.text, format="nt")
    if len(graph) == 0:
        raise LookupError(f"laudo do lote {batch_number!r} não encontrado no Fuseki do lab")
    return deterministic_nt_hash(graph)


# --- OUTBOX DE ANCORAGEM DO LAUDO -----------------------------------------
# Mesmo desenho do outbox do lote (coop-a/coop-b/lote_endpoint.py); ver
# tests/features/ancoragem_outbox_lab.feature. DECISÕES:
# 1. A marca (status + txHash) vive num RECURSO SEPARADO no Fuseki do lab,
#    `:AnchoringTask_<slug(batchNumber)>`, NUNCA no `:Analise_<slug>`: o hash é
#    o do CONSTRUCT acima, que seleciona o laudo por padrão fixo; a tarefa fica
#    fora dele, então gravar status não muda o hash nem quebra GET /verify. A
#    tarefa liga ao laudo por `:anchoringBatch`, não por `agritrust:batchNumber`
#    (que casaria com o padrão do laudo).
# 2. assetId do documento = keccak256(batchNumber + "-lab-analysis"); pai =
#    keccak256(batchNumber) (ver anchoring.py).
# 3. Retry manual (POST /anchoring/retry); sem scheduler na PoC.
# 4. Ordem em POST /analise: grava `pending` -> ancora -> grava `anchored`.
# Estados: pending | anchored | mismatch (asset on-chain com hash DIFERENTE do
# recalculado — nunca vira anchored, é reexaminado a cada retry).
_TASK_PREFIXES = f"""PREFIX : <{BEE}>\n"""


def _task_uri(batch_number: str):
    return BEE[f"AnchoringTask_{_slug(batch_number)}"]


def _fuseki_update(update: str) -> None:
    response = requests.post(
        f"{FUSEKI_URL}/update",
        data={"update": update},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    response.raise_for_status()


def write_anchoring_task(
    batch_number: str, status: str, tx_hash: str | None = None, detail: str | None = None
) -> None:
    """Substitui (DELETE + INSERT numa única update) a marca de ancoragem do
    laudo. `detail` guarda o motivo em `mismatch`."""
    task = _task_uri(batch_number)
    extra = ""
    if tx_hash:
        extra += f" ;\n        :anchoringTxHash {_sparql_literal(tx_hash)}"
    if detail:
        extra += f" ;\n        :anchoringDetail {_sparql_literal(detail)}"
    _fuseki_update(f"""{_TASK_PREFIXES}
    DELETE WHERE {{ <{task}> ?p ?o }} ;
    INSERT DATA {{
      <{task}> a :AnchoringTask ;
        :anchoringBatch {_sparql_literal(batch_number)} ;
        :anchoringStatus {_sparql_literal(status)}{extra} .
    }}""")


def _safe_write_task(batch_number: str, status: str, tx_hash: str | None = None) -> None:
    """Escrita de marca em POST /analise: o laudo já está no Fuseki, então uma
    falha aqui só é logada (não quebra a resposta HTTP)."""
    try:
        write_anchoring_task(batch_number, status, tx_hash)
    except Exception as exc:  # noqa: BLE001
        logger.error("POST /analise: falha ao gravar AnchoringTask %s do laudo %r: %s", status, batch_number, exc)


def _pending_batches() -> list[str]:
    """Laudos a processar no retry: `pending` E `mismatch`. `mismatch` é
    REEXAMINADO a cada retry (nunca enviado, só reportado de novo) — não pode
    sumir do relatório depois da primeira vez; se o dado for corrigido e o hash
    passar a bater, vira `anchored`."""
    response = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": f"""{_TASK_PREFIXES}
        SELECT ?b WHERE {{ ?t :anchoringStatus ?s ; :anchoringBatch ?b .
        FILTER(?s IN ("pending", "mismatch")) }} ORDER BY ?b"""},
        headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    response.raise_for_status()
    return [b["b"]["value"] for b in response.json()["results"]["bindings"]]


def _resolve_existing(batch_number: str, data_hash: bytes, onchain_hash: bytes | None, result: dict) -> dict:
    """O asset do laudo JÁ existe on-chain: compara o hash e resolve sem enviar
    nada. Bate => `anchored` (already_onchain). Não bate => `mismatch`."""
    if onchain_hash is not None and onchain_hash == bytes(data_hash):
        write_anchoring_task(batch_number, "anchored")
        return {**result, "outcome": "already_onchain"}
    detail = (
        f"hash on-chain 0x{onchain_hash.hex() if onchain_hash else ''} "
        f"!= hash recalculado 0x{bytes(data_hash).hex()}"
    )
    write_anchoring_task(batch_number, "mismatch", detail=detail)
    logger.error("Retry de ancoragem do laudo: MISMATCH no lote %r: %s", batch_number, detail)
    return {**result, "outcome": "mismatch", "detail": detail}


def _retry_one(batch_number: str) -> dict:
    result = {"batchNumber": batch_number, "txHash": None}

    try:
        data_hash = compute_laudo_hash(batch_number)
    except LookupError:
        # Laudo sumiu do Fuseki: nada a ancorar. Continua pending (visível).
        return {**result, "outcome": "laudo_not_found"}

    # getAsset ANTES de qualquer tx: se levantar (RPC fora), propaga — não
    # reenvia às cegas uma tx que talvez já exista.
    exists, onchain_hash = get_onchain_data_hash(batch_number)
    if exists:
        return _resolve_existing(batch_number, data_hash, onchain_hash, result)

    # O laudo é um documento FILHO: sem o asset do lote on-chain o
    # createDocument reverte. Não envia; espera o pai (a tarefa fica pending).
    if not parent_exists_onchain(batch_number):
        return {**result, "outcome": "waiting_parent",
                "detail": "asset do lote (pai) ainda não existe on-chain; nada enviado"}

    # natureId = productType do lote (consulta federada coop-a/coop-b). Sem ele
    # não há como montar a tx: failed COM O MOTIVO, tarefa segue pending.
    try:
        product_type = _resolve_product_type(batch_number)
    except Exception as exc:  # noqa: BLE001
        return {**result, "outcome": "failed",
                "error": f"natureId indisponível: consulta federada ao coop-a/coop-b falhou "
                         f"({type(exc).__name__}: {exc}); nada enviado"}
    if product_type is None:
        return {**result, "outcome": "failed",
                "error": "natureId indisponível: productType do lote não encontrado em coop-a nem coop-b; nada enviado"}

    success, tx_hash, error = anchor_lab_analysis(batch_number, product_type, data_hash)
    if success:
        write_anchoring_task(batch_number, "anchored", tx_hash)
        return {**result, "outcome": "sent", "txHash": tx_hash}

    # O envio falhou. `getAsset` só enxerga tx JÁ MINERADA: pode ter dito "não
    # existe" com a tx original ainda na fila (ou minerada logo depois).
    # Reconsulta ANTES de classificar o erro:
    #  - asset existe agora      => resolve (already_onchain / mismatch);
    #  - não existe e o erro é "Known transaction" => `in_flight` (tarefa
    #    segue pending, a próxima passada conclui, sem ERROR no log);
    #  - qualquer outro erro (revert do contrato, RPC fora...) => `failed`.
    try:
        exists, onchain_hash = get_onchain_data_hash(batch_number)
    except Exception as exc:  # noqa: BLE001 - sem como decidir; mantém o erro original
        return {**result, "outcome": "failed", "error": f"{error} (reconsulta getAsset falhou: {exc})"}
    if exists:
        return _resolve_existing(batch_number, data_hash, onchain_hash, result)
    if is_known_transaction_error(error):
        return {**result, "outcome": "in_flight",
                "detail": "transação já está na fila do nó (Known transaction); conferida na próxima passada"}
    return {**result, "outcome": "failed", "error": error}


@router.post("/anchoring/retry")
def retry_anchoring():
    """Processa todas as tarefas `pending` (e reexamina as `mismatch`). Sync
    (def) de propósito: faz I/O bloqueante (requests/web3) e o FastAPI roda em
    threadpool."""
    try:
        batches = _pending_batches()
    except Exception as exc:  # noqa: BLE001
        return _error(502, "server_error", f"Erro ao consultar o Fuseki: {exc}")

    results = []
    for batch_number in batches:
        try:
            results.append(_retry_one(batch_number))
        except Exception as exc:  # noqa: BLE001 - uma tarefa com erro não interrompe as outras
            logger.error("Retry de ancoragem do laudo: erro no lote %r: %s", batch_number, exc)
            results.append(
                {"batchNumber": batch_number, "txHash": None, "outcome": "error",
                 "error": f"{type(exc).__name__}: {exc}"}
            )
    return {"processed": len(results), "results": results}


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
    # OUTBOX: grava `pending` ANTES de qualquer tentativa (inclusive a de
    # resolver o productType); depois de ancorar, `anchored`. Se o processo
    # cair no meio, ou a ancoragem falhar, `POST /anchoring/retry` cobre.
    #
    # HASH: UM caminho só — `compute_laudo_hash` (CONSTRUCT contra o Fuseki do
    # lab + deterministic_nt_hash), o MESMO do retry. Substitui o hash do grafo
    # `g` em memória que este endpoint ancorava antes: os dois dão o mesmo valor
    # (o verifier-demo confirmava o laudo), mas ter dois caminhos de hash é o
    # que deixa retry e POST divergirem sem ninguém notar.
    #
    # Precisa do productType do lote (natureId do processo honey-lab-analysis)
    # — resolvido via SPARQL federado contra coop-a/coop-b (ver
    # _resolve_product_type acima). Se o lote não for encontrado em nenhuma
    # das duas, a ancoragem não é tentada agora (a tarefa fica pending e o
    # retry reporta o motivo).
    anchoring_status = "anchoring_skipped"
    tx_hash = None
    anchoring_error = None
    _safe_write_task(batch_number, "pending")
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
            data_hash = compute_laudo_hash(batch_number)
            success, tx_hash, anchoring_error = anchor_lab_analysis(
                batch_number, product_type, data_hash
            )
            if success:
                anchoring_status = "anchored"
                _safe_write_task(batch_number, "anchored", tx_hash)
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
