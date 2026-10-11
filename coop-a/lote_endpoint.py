"""
POST /lote — cadastro de lote via HTTP, substituindo a necessidade de editar
data/sample-instance.ttl manualmente e rodar pipeline.py via terminal.

Faz EXATAMENTE o que pipeline.py já faz: monta um grafo RDF, valida com
pyshacl contra ontology/validation-shapes.ttl (shacl_pipeline.run_shacl) e só
insere no Fuseki (shacl_pipeline.insert_into_fuseki) se conforms=True. Não é
um segundo gate — é o MESMO gate de pipeline.py, chamado via HTTP em vez de
`python pipeline.py arquivo.ttl`. pipeline.py continua funcionando como
script CLI, sem mudança de comportamento; fixtures/testes existentes
continuam válidos.

Escopo dos campos aceitos (JSON): TODOS opcionais — `batchNumber`,
`productType`, `producerName`, `producerDID`, `floralOrigin`,
`productionDate`, `apiaryLocation` ({"lat":..., "lon":...}), `meliponaryId`
e `environmentalLicenseNumber`. Só entram no grafo se vierem preenchidos
(mesma cautela de credential_endpoint.py).

SHACL, NÃO PYTHON, é a autoridade de validação de negócio (ver
tests/features/shacl_autoridade_validacao.feature e docs/known-issues.md,
"POST /lote: gate SHACL, na prática, sempre passa"). Python só valida FORMA
(JSON bem formado; tipos corretos onde presentes — ex.: apiaryLocation.lat/lon
numéricos, productType como nome de classe válido). `batchNumber`/`ownedBy`
ausentes não são mais barrados em Python — o grafo é montado com o que vier
e o gate SHACL (`HoneyApisIdentificationShape`, `sh:minCount`) decide,
devolvendo a mensagem da PRÓPRIA shape em `shacl_violation`, não uma
mensagem genérica de "campo obrigatório ausente".

`meliponaryId`/`environmentalLicenseNumber`: aceitos independente de
`productType` (não há razão técnica pra proibir num produtor com operação
mista Apis + nativa), mas só fazem sentido regulatório pra mel nativo hoje
(:MeliponaryLicenseShape, Art. 68 da Portaria 795/2023 — ver
tests/features/lote_meliponary.feature). `environmentalLicenseNumber` sem
`meliponaryId` é erro (400): licença pertence a um meliponário. Só
`meliponaryId`, sem licença, é aceito pelo Python — e REJEITADO pelo gate
SHACL logo depois (minCount 1 em `:hasEnvironmentalLicense`).

DELIBERADAMENTE DE FORA: `moistureContent`, `hmfContent`, `acidityContent`,
`diastaseActivity`, `legalStatus`, `qualityStatus`. São medição/conclusão de
laboratório, não dado do produtor — continuam exclusivos do certifier, sem
mudança nessa separação (ver docs/notas-arquitetura/narrativa-como-criterio-mvp.md
e docs/guia-ontologia/06-classes-e-atores.md sobre por que essa linha existe).

RESOLUÇÃO DE PRODUTOR EXISTENTE: antes de mintar uma URI nova para o
produtor, o endpoint consulta o Fuseki (`find_existing_producer`) por um
recurso que já tenha `agritrust:hasDID` == `producerDID` recebido. Se
existir (ex.: `:Apicultor_Joao_Cerrado`, já em data/sample-instance.ttl),
o novo lote é vinculado a ELE via `ownedBy` — não se cria um segundo
recurso de produtor para o mesmo DID. Só se não existir nenhum é que uma
URI nova é gerada deterministicamente a partir do `producerDID`
(`_slug`), do mesmo jeito que antes.
"""
import base64
import hashlib
import json
import logging
import re
import time
import uuid

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jwcrypto.jws import JWS
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import FOAF, RDF, XSD

from anchoring import anchor_batch, get_onchain_data_hash, is_known_transaction_error
from config import CERTIFIER_URL, FUSEKI_ADMIN_PASSWORD, FUSEKI_ADMIN_USER, FUSEKI_URL
from hash_utils import deterministic_nt_hash
from keys import get_issuer_key
from pre_auth_codes import generate_for_batch, reissue_for_batch
from shacl_pipeline import extract_violations, format_violations, insert_into_fuseki, run_shacl

logger = logging.getLogger(__name__)

router = APIRouter()

# Mesmos namespaces de coop-a/data/sample-instance.ttl.
BEE = Namespace("https://w3id.org/agrisemantics/AGRITRUST/bee-extension#")
AGRITRUST = Namespace("https://w3id.org/agrisemantics/AGRITRUST#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

# Local name de URI/classe: letra inicial + letras/dígitos/underscore. Guarda
# mínima contra IRI malformada (ex.: productType com espaço) — NÃO é uma
# whitelist dos nomes de classe válidos da ontologia (isso ficaria acoplado
# demais a bee-extension.ttl; qualquer nome sintaticamente válido é aceito,
# igual ao pipeline.py aceitar qualquer rdf:type escrito num .ttl à mão).
_LOCAL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _slug(value: str) -> str:
    """Sanitiza um valor para usar como segmento de URI (local name):
    troca qualquer caractere fora de [A-Za-z0-9_] por "_". Determinístico
    (não blank node) de propósito — mesma razão da correção de duplicação de
    geo:Point em coop-a/data/sample-instance.ttl (ver CHANGELOG.md): reenviar
    o mesmo batchNumber/producerDID não deve criar um recurso novo a cada
    chamada, e sim reafirmar as mesmas triplas (INSERT DATA é idempotente
    para triplas idênticas)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _error(status_code: int, error: str, description: str, extra: dict | None = None) -> JSONResponse:
    body = {"error": error, "error_description": description}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Mesmo nível de escape simples usado em coop-a/credential_endpoint.py e
    demais nós — não é um escapador SPARQL genérico e completo (ver
    docs/known-issues.md).
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# --- CONSTRUCT usado para reconstruir, a partir do Fuseki, EXATAMENTE o
# mesmo conjunto de triplas que `verifier-demo/onchain_verification.py`
# reconstrói para recalcular o hash (réplica EXATA de
# `_LOTE_CONSTRUCT_TEMPLATE` lá). Ver ACHADO DE INTEGRIDADE abaixo (passo 5)
# para o motivo de existir aqui: não é redundante com o grafo `g` em
# memória, é a correção do bug de divergência produtor-reaproveitado.
_LOTE_CONSTRUCT_TEMPLATE = """
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
CONSTRUCT {
  ?producer a agritrust:DataProducer .
  ?producer foaf:name ?producerName .
  ?producer agritrust:hasDID ?producerDID .
  ?lote a ?productType .
  ?lote agritrust:ownedBy ?producer .
  ?lote agritrust:batchNumber ?batchNumber .
}
WHERE {
  ?lote agritrust:batchNumber %(batch_number)s ;
        a ?productType ;
        agritrust:ownedBy ?producer .
  BIND(%(batch_number)s AS ?batchNumber)
  ?producer a agritrust:DataProducer ;
            agritrust:hasDID ?producerDID .
  OPTIONAL { ?producer foaf:name ?producerName }
}
"""


def _construct_lote_graph_for_hash(batch_number: str) -> Graph:
    """Roda, contra o PRÓPRIO Fuseki deste nó, o MESMO CONSTRUCT que
    `verifier-demo/onchain_verification.py` roda para recalcular o hash de
    integridade — ver ACHADO DE INTEGRIDADE na docstring do passo 5 de
    `cadastrar_lote` para o motivo de ancorar o hash deste grafo
    reconstruído, e não do grafo `g` em memória."""
    query = _LOTE_CONSTRUCT_TEMPLATE % {"batch_number": _sparql_literal(batch_number)}
    response = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/n-triples"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    response.raise_for_status()
    reconstructed = Graph()
    reconstructed.parse(data=response.text, format="nt")
    return reconstructed


def find_existing_lote(batch_number: str) -> str | None:
    """Procura no Fuseki QUALQUER recurso com `agritrust:batchNumber` ==
    batch_number, seja qual for sua URI. Diferente de reafirmar a MESMA URI
    determinística (`Lote_{slug(batchNumber)}`) — isso já era idempotente em
    tripla —, esta checagem cobre também recursos com URI DIFERENTE mas
    mesmo batchNumber (ex.: fixture legado `:Lote_Mel_Silvestre_2026_042`
    vs. a URI que este endpoint mintaria, `:Lote_MS_2026_042` — foi
    exatamente essa duplicata, criada e limpa manualmente 2x durante o
    desenvolvimento de tests/features/lote_idempotente.feature, que motivou
    esta checagem; ver docs/known-issues.md). Retorna a URI (string) do
    primeiro achado, ou None se batchNumber ainda não existe."""
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    SELECT ?lote WHERE {{
      ?lote agritrust:batchNumber {_sparql_literal(batch_number)} .
    }}
    LIMIT 1
    """
    response = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    response.raise_for_status()

    bindings = response.json().get("results", {}).get("bindings", [])
    if not bindings:
        return None
    return bindings[0]["lote"]["value"]


def find_existing_producer(producer_did: str) -> str | None:
    """Procura no Fuseki um recurso com `agritrust:hasDID` == producer_did.

    Retorna a URI (string) do primeiro achado, ou None se não existir. É o
    que evita criar um SEGUNDO recurso de produtor pro mesmo DID quando o
    mesmo apicultor já está cadastrado (ex.: `:Apicultor_Joao_Cerrado` em
    data/sample-instance.ttl) — ver docstring do módulo.
    """
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    SELECT ?producer WHERE {{
      ?producer agritrust:hasDID {_sparql_literal(producer_did)} .
    }}
    LIMIT 1
    """
    response = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    response.raise_for_status()

    bindings = response.json().get("results", {}).get("bindings", [])
    if not bindings:
        return None
    return bindings[0]["producer"]["value"]


# --- OUTBOX DE ANCORAGEM -------------------------------------------------
# DECISÕES DE DESIGN (ver tests/features/ancoragem_outbox.feature):
# 1. A marca de ancoragem (status + txHash) vive num RECURSO SEPARADO,
#    `:AnchoringTask_<slug(batchNumber)>`, NUNCA no recurso do lote. O hash
#    ancorado é keccak do grafo de `_LOTE_CONSTRUCT_TEMPLATE`, que seleciona
#    triplas do lote por padrão fixo; a tarefa fica fora de qualquer padrão
#    desse CONSTRUCT, então gravar/atualizar status NÃO muda o hash e não
#    quebra GET /verify (verificacao_hash_onchain.feature).
#    A tarefa liga ao lote por `:anchoringBatch`, NÃO por
#    `agritrust:batchNumber`: `find_existing_lote` casa qualquer recurso com
#    esse predicado e trataria a tarefa como lote duplicado.
# 2. Retry é manual (POST /anchoring/retry); sem scheduler na PoC.
# 3. Ordem em POST /lote: grava `pending` -> ancora -> grava `anchored`.
#    Se o processo cair entre a tx e a atualização, a tarefa fica `pending`
#    com o asset já on-chain; o retry resolve via getAsset (sem reenviar).
# Estados: pending | anchored | mismatch (asset on-chain com hash DIFERENTE
# do recalculado — nunca vira anchored; exige investigação humana).
_TASK_PREFIXES = f"""PREFIX : <{BEE}>\n"""


def _task_uri(batch_number: str) -> URIRef:
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
    lote. `detail` guarda o motivo em `mismatch`."""
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
    """Escrita de marca em POST /lote: lote já está no Fuseki, então falha
    aqui só é logada (não quebra a resposta HTTP)."""
    try:
        write_anchoring_task(batch_number, status, tx_hash)
    except Exception as exc:  # noqa: BLE001
        logger.error("POST /lote: falha ao gravar AnchoringTask %s do lote %r: %s", status, batch_number, exc)


def _pending_batches() -> list[str]:
    """Lotes a processar no retry: `pending` E `mismatch`. `mismatch` é
    REEXAMINADO a cada retry (nunca enviado, só reportado de novo) — um
    mismatch não pode sumir do relatório depois da primeira vez; se o dado
    for corrigido e o hash passar a bater, vira `anchored`."""
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


def _nature_id_from_graph(graph: Graph) -> str:
    """productType (local name) do lote = rdf:type que não é DataProducer."""
    for _, _, cls in graph.triples((None, RDF.type, None)):
        if cls != AGRITRUST.DataProducer:
            return str(cls).rsplit("#", 1)[-1]
    return ""


def _resolve_existing(batch_number: str, data_hash: bytes, onchain_hash: bytes | None, result: dict) -> dict:
    """O asset JÁ existe on-chain: compara o hash e resolve sem enviar nada.
    Bate => `anchored` (already_onchain). Não bate => `mismatch` (nunca anchored)."""
    if onchain_hash is not None and onchain_hash == bytes(data_hash):
        write_anchoring_task(batch_number, "anchored")
        return {**result, "outcome": "already_onchain"}
    detail = (
        f"hash on-chain 0x{onchain_hash.hex() if onchain_hash else ''} "
        f"!= hash recalculado 0x{bytes(data_hash).hex()}"
    )
    write_anchoring_task(batch_number, "mismatch", detail=detail)
    logger.error("Retry de ancoragem: MISMATCH no lote %r: %s", batch_number, detail)
    return {**result, "outcome": "mismatch", "detail": detail}


def _retry_one(batch_number: str) -> dict:
    result = {"batchNumber": batch_number, "txHash": None}

    graph = _construct_lote_graph_for_hash(batch_number)
    if len(graph) == 0:
        # Lote sumiu do Fuseki: nada a ancorar. Continua pending (visível).
        return {**result, "outcome": "lote_not_found"}
    data_hash = deterministic_nt_hash(graph)

    # getAsset ANTES de qualquer tx: se levantar (RPC fora), propaga — não
    # reenvia às cegas uma tx que talvez já exista.
    exists, onchain_hash = get_onchain_data_hash(batch_number)
    if exists:
        return _resolve_existing(batch_number, data_hash, onchain_hash, result)

    success, tx_hash, error = anchor_batch(batch_number, _nature_id_from_graph(graph), data_hash)
    if success:
        write_anchoring_task(batch_number, "anchored", tx_hash)
        return {**result, "outcome": "sent", "txHash": tx_hash}

    # O envio falhou. `getAsset` só enxerga tx JÁ MINERADA, então ele pode ter
    # dito "não existe" enquanto a tx original ainda estava na fila (ou foi
    # minerada logo depois). Reconsulta ANTES de classificar o erro:
    #  - asset existe agora      => resolve (already_onchain / mismatch);
    #  - não existe e o erro é "Known transaction" (mesma tx já na fila do
    #    nó) => `in_flight`: não é falha, a tarefa fica `pending` e a próxima
    #    passada conclui; sem ERROR no log;
    #  - qualquer outro erro (revert do contrato, RPC fora...) => `failed`.
    # Um revert real nunca vira in_flight: só "Known transaction" o faz.
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
    """Processa todas as tarefas `pending` (e reexamina as `mismatch`). Sync (def) de propósito: faz I/O
    bloqueante (requests/web3) e o FastAPI roda em threadpool."""
    try:
        batches = _pending_batches()
    except Exception as exc:  # noqa: BLE001
        return _error(502, "server_error", f"Erro ao consultar o Fuseki: {exc}")

    results = []
    for batch_number in batches:
        try:
            results.append(_retry_one(batch_number))
        except Exception as exc:  # noqa: BLE001 - uma tarefa com erro não interrompe as outras
            logger.error("Retry de ancoragem: erro no lote %r: %s", batch_number, exc)
            results.append(
                {"batchNumber": batch_number, "txHash": None, "outcome": "error",
                 "error": f"{type(exc).__name__}: {exc}"}
            )
    return {"processed": len(results), "results": results}


@router.post("/lote")
async def cadastrar_lote(request: Request):
    """Cadastra um lote (dado do produtor) direto via HTTP, sem editar .ttl
    nem rodar pipeline.py manualmente. Ver docstring do módulo para o
    contrato completo (campos aceitos, gate SHACL, o que fica de fora)."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_request", "Corpo da requisição não é JSON válido.")

    if not isinstance(body, dict):
        return _error(400, "invalid_request", "Corpo da requisição deve ser um objeto JSON.")

    # --- Validação de FORMA apenas (não de presença/negócio — isso é com o
    # SHACL, ver docstring do módulo). Cada campo é lido com .get() e só
    # vira string se vier preenchido; nenhum é obrigatório aqui. ---
    batch_number = body.get("batchNumber")
    batch_number = str(batch_number) if batch_number else None
    product_type = body.get("productType")
    product_type = str(product_type) if product_type else None
    producer_name = body.get("producerName")
    producer_name = str(producer_name) if producer_name else None
    producer_did = body.get("producerDID")
    producer_did = str(producer_did) if producer_did else None
    floral_origin = body.get("floralOrigin")
    production_date = body.get("productionDate")
    apiary_location = body.get("apiaryLocation")
    meliponary_id = body.get("meliponaryId")
    environmental_license_number = body.get("environmentalLicenseNumber")

    if environmental_license_number and not meliponary_id:
        return _error(
            400,
            "invalid_request",
            "environmentalLicenseNumber requer meliponaryId (licença pertence a um meliponário).",
        )

    if product_type is not None and not _LOCAL_NAME_RE.match(product_type):
        return _error(
            400,
            "invalid_request",
            f"productType inválido: {product_type!r}. Esperado um nome de "
            f"classe da ontologia (ex.: 'HoneyApis'), só letras/dígitos/underscore.",
        )

    # --- 0a. Rejeita batchNumber já cadastrado (idempotência de negócio,
    # não só de tripla — ver docstring de find_existing_lote). Só faz
    # sentido se batchNumber veio; sem ele, não há o que checar aqui — o
    # SHACL vai rejeitar a ausência mais adiante. ---
    if batch_number is not None:
        try:
            if find_existing_lote(batch_number) is not None:
                return _error(
                    409,
                    "lote_ja_cadastrado",
                    f"Lote {batch_number!r} já está cadastrado.",
                )
        except Exception as exc:  # noqa: BLE001 - mesmo nível de reporte do pipeline.py
            return _error(502, "server_error", f"Erro ao consultar o Fuseki: {exc}")

    # --- 0b. Resolve produtor existente por DID (UMA consulta, resultado
    # reusado abaixo pra decidir se é reuso ou produtor novo). Só roda se
    # producerDID veio — sem ele, não há produtor a montar (o lote fica sem
    # ownedBy, e o SHACL rejeita isso mais adiante). ---
    producer_uri = None
    is_new_producer = False
    if producer_did is not None:
        try:
            existing_producer = find_existing_producer(producer_did)
        except Exception as exc:  # noqa: BLE001 - mesmo nível de reporte do pipeline.py
            return _error(502, "server_error", f"Erro ao consultar o Fuseki: {exc}")

        if existing_producer is not None:
            # Reusa o recurso já cadastrado (mesmo agritrust:hasDID) — não
            # reinserimos rdf:type/foaf:name/hasDID: essas triplas já
            # existem para esse produtor, reafirmá-las não é errado (RDF é
            # um conjunto), mas não é necessário.
            producer_uri = URIRef(existing_producer)
        else:
            producer_uri = BEE[f"Produtor_{_slug(producer_did)}"]
            is_new_producer = True

    # --- 1. Monta o grafo RDF (mesma "forma" de data/sample-instance.ttl) ---
    g = Graph()
    g.bind("agritrust", AGRITRUST)
    g.bind("geo", GEO)
    g.bind("foaf", FOAF)

    # batchNumber ausente não impede o recurso de existir: usa um segmento
    # opaco (uuid) só pra dar identidade URI ao nó, pro SHACL rodar sobre
    # ele e reportar a ausência via HoneyApisIdentificationShape — não é
    # usado como identificador de negócio em lugar nenhum.
    lote_uri = BEE[f"Lote_{_slug(batch_number)}"] if batch_number else BEE[f"Lote_SemBatchNumber_{uuid.uuid4().hex[:12]}"]

    if is_new_producer:
        g.add((producer_uri, RDF.type, AGRITRUST.DataProducer))
        if producer_name:
            g.add((producer_uri, FOAF.name, Literal(producer_name)))
        g.add((producer_uri, AGRITRUST.hasDID, Literal(producer_did)))

    if product_type is not None:
        g.add((lote_uri, RDF.type, BEE[product_type]))
    if producer_uri is not None:
        g.add((lote_uri, AGRITRUST.ownedBy, producer_uri))
    if batch_number is not None:
        g.add((lote_uri, AGRITRUST.batchNumber, Literal(batch_number)))
    if floral_origin:
        # Dado declarado pelo produtor na colheita (ver docstring do módulo,
        # e docs/notas-arquitetura/narrativa-como-criterio-mvp.md) — mesmo
        # claim que coop-a/credential_endpoint.py lê de volta como
        # disclosable via `:hasFloralOrigin`.
        g.add((lote_uri, BEE.hasFloralOrigin, Literal(str(floral_origin), lang="pt")))
    if production_date:
        g.add((lote_uri, AGRITRUST.productionDate, Literal(str(production_date), datatype=XSD.date)))

    if apiary_location is not None:
        if not isinstance(apiary_location, dict) or "lat" not in apiary_location or "lon" not in apiary_location:
            return _error(
                400,
                "invalid_request",
                'apiaryLocation deve ser um objeto {"lat": <número>, "lon": <número>}.',
            )
        try:
            lat = float(apiary_location["lat"])
            lon = float(apiary_location["lon"])
        except (TypeError, ValueError):
            return _error(400, "invalid_request", "apiaryLocation.lat/lon devem ser números.")

        # URIs determinísticas por batchNumber (não blank node) — mesma
        # razão do _slug acima. Sem batchNumber, usa o mesmo segmento
        # opaco do lote_uri (lote_uri já resolve pra um segmento válido em
        # ambos os casos — ver acima).
        geo_slug = _slug(batch_number) if batch_number else lote_uri.split("Lote_", 1)[-1]
        geo_feature_uri = BEE[f"Apiario_{geo_slug}_Geo"]
        geo_point_uri = BEE[f"Apiario_{geo_slug}_Point"]
        if producer_uri is not None:
            g.add((producer_uri, AGRITRUST.hasOperatingLocation, geo_feature_uri))
        g.add((geo_feature_uri, RDF.type, GEO.Feature))
        g.add((geo_feature_uri, GEO.hasGeometry, geo_point_uri))
        g.add((geo_point_uri, RDF.type, GEO.Point))
        # Convenção do projeto: POINT(lon lat), não POINT(lat lon) — ver
        # coop-a/data/sample-instance.ttl e ontology/instances.ttl.
        g.add((geo_point_uri, GEO.asWKT, Literal(f"POINT({lon} {lat})", datatype=GEO.wktLiteral)))

    if meliponary_id:
        # Meliponário de origem — aceito independente de productType (um
        # produtor pode ter operação de Apis e nativa ao mesmo tempo), mas
        # só :MeliponaryLicenseShape (targetClass :Meliponary) valida isso
        # hoje, e nenhuma shape regulatória exige o campo para HoneyApis.
        # Estrutura idêntica ao fixture legado (coop-b/data/sample-instance.ttl,
        # migrado — ver CHANGELOG.md): :Meliponary + agritrust:hasIdentifier
        # (+ :hasEnvironmentalLicense -> :EnvironmentalLicense, se a licença
        # vier). Sem propriedade explícita ligando o LOTE ao meliponário —
        # a ontologia não define uma (mesma ausência no fixture original);
        # pyshacl valida :Meliponary pelo targetClass, não por alcançabilidade
        # a partir do lote.
        meliponary_uri = BEE[f"Meliponario_{_slug(meliponary_id)}"]
        g.add((meliponary_uri, RDF.type, BEE.Meliponary))
        if producer_uri is not None:
            g.add((meliponary_uri, AGRITRUST.ownedBy, producer_uri))
        g.add((meliponary_uri, AGRITRUST.hasIdentifier, Literal(meliponary_id)))
        if environmental_license_number:
            license_uri = BEE[f"Licenca_{_slug(meliponary_id)}"]
            g.add((meliponary_uri, BEE.hasEnvironmentalLicense, license_uri))
            g.add((license_uri, RDF.type, BEE.EnvironmentalLicense))
            g.add((license_uri, BEE.environmentalLicenseNumber, Literal(environmental_license_number)))

    # --- 2. Gate SHACL — mesma função que pipeline.py usa ---
    conforms, results_graph, _ = run_shacl(g)
    if not conforms:
        violations = extract_violations(results_graph)
        return _error(
            400,
            "shacl_violation",
            "Dado do lote não passou na validação SHACL. Nada foi inserido.",
            extra={"violations": format_violations(violations)},
        )

    # --- 3. Inserção no Fuseki — mesma função que pipeline.py usa ---
    try:
        insert_into_fuseki(g)
    except Exception as exc:  # noqa: BLE001 - mesmo nível de reporte do pipeline.py
        return _error(502, "server_error", f"Erro ao inserir no Fuseki: {exc}")

    # --- 4. Gera o pre_authorized_code único e de uso único deste lote ---
    # (ver pre_auth_codes.py e docs/known-issues.md,
    # "pre-authorized_code fixo por nó, sem amarração à entrega/lote").
    # Só se batchNumber veio: sem ele, HoneyApisIdentificationShape já teria
    # barrado a requisição ACIMA — a única forma de chegar aqui sem
    # batchNumber é productType também ausente (o nó nem foi tipado como
    # :HoneyApis, então a shape não se aplica a ele; caso de borda aceito
    # deliberadamente, não um bug — SHACL, não Python, decide o que é
    # obrigatório). pre_auth_codes.generate_for_batch usa batchNumber como
    # chave, então sem ele não há como gerar/rastrear um código associável.
    pre_authorized_code = generate_for_batch(batch_number) if batch_number is not None else None

    # --- 5. Ancoragem on-chain (M8) — melhor esforço, NUNCA reverte a
    # inserção no Fuseki (já commitada no passo 3 acima) nem quebra esta
    # resposta HTTP: sucesso ou falha só mudam `anchoringStatus`/campos
    # associados, sempre presentes explicitamente (decisão do usuário — ver
    # tests/features/ancoragem_lote.feature e
    # docs/referencia-contratos/README.md seção 5).
    #
    # ACHADO DE INTEGRIDADE (investigação pós-M9.5, corrige mismatch real
    # encontrado em tests/step_defs/test_verificacao_hash_onchain.py):
    # `dataHashes` NÃO usa mais o keccak256 do objeto `g` em memória direto.
    # Motivo: quando `producerDID` já tem um recurso cadastrado
    # (`is_new_producer=False`, ver passo 0b acima), as triplas
    # rdf:type/foaf:name/hasDID do produtor DELIBERADAMENTE não são
    # re-adicionadas a `g` (já existem no Fuseki, reafirmá-las é
    # redundante) — então `g`, nesse caso, contém só as 3 triplas do LOTE
    # (type/ownedBy/batchNumber), não as 6 triplas (lote + produtor) que
    # `verifier-demo/onchain_verification.py` reconstrói via SPARQL
    # CONSTRUCT (que sempre traz o produtor inteiro, novo ou reaproveitado,
    # porque ele existe no Fuseki de qualquer forma). Resultado: hash
    # ancorado sobre 3 triplas vs. hash recalculado sobre 6 — mismatch
    # determinístico, não um problema de FORMA de serialização (confirmado:
    # Jena/Fuseki e rdflib produzem N-Triples byte-idênticos para os
    # literais simples usados aqui, sem/com datatype — não é o caso
    # `^^xsd:string`). Quando o produtor é novo (`is_new_producer=True`),
    # `g` já continha as 6 triplas e por isso o mismatch não aparecia nesse
    # caso — só se manifestava com DID de produtor reaproveitado (exatamente
    # o cenário do teste, que reusa o mesmo PRODUCER_DID em toda execução).
    #
    # CORREÇÃO: depois do INSERT (passo 3), reconstrói via
    # `_construct_lote_graph_for_hash` o MESMO conjunto de triplas que o
    # verificador vai reconstruir (CONSTRUCT idêntico ao de
    # `onchain_verification.py`) e ancora o hash DESSE grafo — garante, por
    # construção, que o hash ancorado é sempre recalculável por quem fizer
    # a mesma consulta, independente de o produtor ser novo ou reaproveitado.
    # Custo: um round-trip SPARQL extra ao próprio Fuseki (aceitável — rede
    # zero-gas, chamada local). `deterministic_nt_hash` continua a mesma
    # (serialize nt -> sorted() -> keccak) — só MUDOU qual grafo é
    # hasheado, não como o hash é calculado.
    anchoring_status = "anchoring_skipped"
    tx_hash = None
    anchoring_error = None
    if batch_number is not None:
        # Outbox: grava `pending` ANTES de ancorar (ver bloco OUTBOX acima).
        _safe_write_task(batch_number, "pending")
        try:
            hash_graph = _construct_lote_graph_for_hash(batch_number)
            data_hash = deterministic_nt_hash(hash_graph)
        except Exception as exc:  # noqa: BLE001 - lote já inserido, nunca quebra a resposta HTTP
            anchoring_status = "anchoring_failed"
            anchoring_error = (
                f"Erro ao reconstruir grafo do lote para cálculo do hash de "
                f"ancoragem: {type(exc).__name__}: {exc}"
            )
            logger.error(
                "POST /lote: falha ao reconstruir grafo do lote %r para hash: %s",
                batch_number, anchoring_error,
            )
        else:
            nature_id = product_type or ""
            success, tx_hash, anchoring_error = anchor_batch(batch_number, nature_id, data_hash)
            if success:
                anchoring_status = "anchored"
                _safe_write_task(batch_number, "anchored", tx_hash)
                logger.info("POST /lote: lote %r ancorado on-chain, txHash=%s", batch_number, tx_hash)
            else:
                anchoring_status = "anchoring_failed"
                logger.error("POST /lote: falha ao ancorar lote %r on-chain: %s", batch_number, anchoring_error)
    else:
        # Sem batchNumber não há newAssetIds determinístico nem processo
        # aplicável a ancorar (ver docstring acima sobre o caso de borda
        # productType também ausente) — não tenta ancorar.
        logger.info("POST /lote: lote sem batchNumber, ancoragem on-chain pulada (anchoringStatus=anchoring_skipped).")

    response_body = {
        "status": "inserted",
        "batchNumber": batch_number,
        "lote": str(lote_uri),
        "producer": str(producer_uri) if producer_uri is not None else None,
        "pre_authorized_code": pre_authorized_code,
        "anchoringStatus": anchoring_status,
    }
    if tx_hash is not None:
        response_body["txHash"] = tx_hash
    if anchoring_error is not None:
        response_body["anchoringError"] = anchoring_error
    return response_body


@router.post("/lote/{batch_number}/reemitir-codigo")
async def reemitir_codigo(batch_number: str):
    """Invalida o pre_authorized_code atual do lote e gera um novo — para o
    caso do produtor perder o código original antes de usá-lo (ver
    tests/features/pre_authorized_code.feature, Cenário 2). Não reemite se
    o código atual já foi consumido (credencial já emitida/em emissão) —
    ver pre_auth_codes.reissue_for_batch."""
    novo_codigo, erro = reissue_for_batch(batch_number)
    if erro == "not_found":
        return _error(
            404,
            "lote_sem_codigo",
            f"Lote {batch_number!r} não tem pre_authorized_code registrado "
            f"(não foi cadastrado via POST /lote nesta execução, ou o processo reiniciou).",
        )
    if erro == "already_used":
        return _error(
            409,
            "codigo_ja_utilizado",
            "Código já utilizado, lote já possui credencial emitida.",
        )
    return {"batchNumber": batch_number, "pre_authorized_code": novo_codigo}


# --- Holder: coop-a retira e guarda a VC de certificação do certifier ---
#
# PROOF_JWT_TYP: mesmo valor usado em certifier/credential_endpoint.py e
# scripts/test_credential_flow.py (header `typ` do JWT de key proof,
# conforme OID4VCI).
_PROOF_JWT_TYP = "openid4vci-proof+jwt"


def _build_proof_jwt(audience: str, c_nonce: str) -> str:
    """Monta o JWT de key proof assinado pela chave do PRÓPRIO issuer
    coop-a (`keys.get_issuer_key()`) — mesma Ed25519 que assina os
    SD-JWT VC que a coop-a emite. SIMPLIFICAÇÃO DE MVP, documentada: a
    coop-a não tem uma identidade de "holder" separada da de "issuer"
    (nenhum M anterior criou uma); reusar a chave do issuer evita inventar
    infraestrutura de chave nova só para este fluxo, no mesmo espírito de
    outras simplificações já aceitas no projeto (proof JWT do holder nunca
    é verificado contra PKI real por quem emite — ver
    certifier/credential_endpoint.py). Mesma mecânica de
    scripts/test_credential_flow.py:build_proof_jwt, não reaproveitada de
    lá por ser um script de teste (argparse), não um módulo importável."""
    key = get_issuer_key()
    public_jwk = json.loads(key.export_public())
    header = {"alg": "EdDSA", "typ": _PROOF_JWT_TYP, "jwk": public_jwk}
    payload = {"aud": audience, "iat": int(time.time()), "nonce": c_nonce}
    jws = JWS(json.dumps(payload).encode())
    jws.add_signature(key, alg="EdDSA", protected=json.dumps(header))
    return jws.serialize(compact=True)


@router.post("/lote/{batch_number}/retirar-certificacao")
async def retirar_certificacao(batch_number: str):
    """Roda o fluxo completo de Holder contra o certifier (mesmo mecanismo
    já testado em tests/features/holder_certificacao.feature/
    ancoragem_certificacao.feature, agora como código de PRODUÇÃO, não só
    chamado por teste): POST /lote/{batch}/pre-authorized-code ->
    /token -> /nonce -> /credential. Se a VC vier com sucesso, guarda o
    SD-JWT completo no PRÓPRIO Fuseki da coop-a, como LITERAL (não RDF
    estruturado — a VC já é auto-contida/assinada; decompor em triplas não
    agrega nada e complicaria revalidação de assinatura depois), vinculado
    ao recurso do lote via `agritrust:hasCertificationCredential` — mesma
    URI determinística já usada em POST /lote (`BEE[f"Lote_{slug}"]`). Ver
    tests/features/holder_persiste_vc.feature."""
    try:
        lookup_resp = requests.post(
            f"{CERTIFIER_URL}/lote/{batch_number}/pre-authorized-code", timeout=10
        )
    except requests.RequestException as exc:
        return _error(502, "server_error", f"Falha ao contatar o certifier: {exc}")

    if lookup_resp.status_code != 200:
        return JSONResponse(status_code=lookup_resp.status_code, content=lookup_resp.json())
    pre_authorized_code = lookup_resp.json()["pre_authorized_code"]

    token_resp = requests.post(
        f"{CERTIFIER_URL}/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:pre-authorized_code",
            "pre-authorized_code": pre_authorized_code,
        },
        timeout=10,
    )
    if token_resp.status_code != 200:
        return JSONResponse(status_code=token_resp.status_code, content=token_resp.json())
    access_token = token_resp.json()["access_token"]

    meta_resp = requests.get(f"{CERTIFIER_URL}/.well-known/openid-credential-issuer", timeout=10)
    meta_resp.raise_for_status()
    meta = meta_resp.json()
    config_id = next(iter(meta["credential_configurations_supported"]))

    nonce_resp = requests.post(f"{CERTIFIER_URL}/nonce", timeout=10)
    nonce_resp.raise_for_status()
    c_nonce = nonce_resp.json()["c_nonce"]

    proof_jwt = _build_proof_jwt(meta["credential_issuer"], c_nonce)

    credential_resp = requests.post(
        f"{CERTIFIER_URL}/credential",
        json={
            "credential_configuration_id": config_id,
            "proof": {"proof_type": "jwt", "jwt": proof_jwt},
            "batch_number": batch_number,
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if credential_resp.status_code != 200:
        return JSONResponse(status_code=credential_resp.status_code, content=credential_resp.json())

    sd_jwt = credential_resp.json()["credential"]

    lote_uri = BEE[f"Lote_{_slug(batch_number)}"]
    update_query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    DELETE {{ <{lote_uri}> agritrust:hasCertificationCredential ?old }}
    WHERE {{ OPTIONAL {{ <{lote_uri}> agritrust:hasCertificationCredential ?old }} }} ;
    INSERT DATA {{ <{lote_uri}> agritrust:hasCertificationCredential {_sparql_literal(sd_jwt)} }}
    """
    try:
        update_resp = requests.post(
            f"{FUSEKI_URL}/update",
            data={"update": update_query},
            auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
            timeout=20,
        )
        update_resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error(
            "POST /lote/%r/retirar-certificacao: VC recebida mas falha ao "
            "persistir no Fuseki: %s", batch_number, exc,
        )
        return _error(
            502,
            "server_error",
            f"VC recebida do certifier, mas falha ao guardar no Fuseki da coop-a: {exc}",
        )

    logger.info("POST /lote/%r/retirar-certificacao: VC de certificação recebida e guardada.", batch_number)
    return {"batchNumber": batch_number, "credential": sd_jwt, "status": "stored"}


def _busca_credencial_guardada(batch_number: str) -> str | None:
    """Consulta o PRÓPRIO Fuseki da coop-a por `agritrust:hasCertificationCredential`
    vinculado ao lote (gravado por `/retirar-certificacao`). Retorna o
    SD-JWT (string) ou None se nada foi guardado ainda para esse lote."""
    lote_uri = BEE[f"Lote_{_slug(batch_number)}"]
    query = f"""
    PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
    SELECT ?credencial WHERE {{
      <{lote_uri}> agritrust:hasCertificationCredential ?credencial .
    }}
    LIMIT 1
    """
    resp = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    resp.raise_for_status()
    bindings = resp.json().get("results", {}).get("bindings", [])
    if not bindings:
        return None
    return bindings[0]["credencial"]["value"]


def _compute_sd_hash(credential_jwt: str, disclosures: list[str]) -> str:
    """Calcula `sd_hash` conforme draft-ietf-oauth-selective-disclosure-jwt:
    hash (mesmo algoritmo do `_sd_alg` da credencial — `"sha-256"`, ver
    `certifier/credential_endpoint.py`/lib `sd_jwt`) sobre a concatenação
    EXATA `"<Credential-JWT>~<Disclosure1>~<Disclosure2>~...~"` — tudo que
    vem ANTES do KB-JWT na VP final, incluindo o `~` final que o separa do
    KB-JWT. Resultado codificado base64url SEM padding (mesma convenção
    JWS/JWT de sempre). Não há dependência circular: essa string é
    conhecida integralmente ANTES de montar/assinar o KB-JWT (ela não
    contém o KB-JWT em si)."""
    pre_kb = "~".join([credential_jwt, *disclosures, ""])
    digest = hashlib.sha256(pre_kb.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _build_kb_jwt(audience: str, nonce: str, sd_hash: str) -> str:
    """Monta o Key Binding JWT (SD-JWT VC, apresentação) assinado com a
    MESMA chave do issuer da coop-a (`keys.get_issuer_key()`) — é a chave
    cuja parte PÚBLICA já está em `cnf.jwk` da VC de certificação
    (confirmado em `certifier/credential_endpoint.py`: `cnf.jwk` vem do
    header `jwk` do proof JWT que `_build_proof_jwt` já assina com essa
    mesma chave — ver docstring de tests/step_defs/test_apresentacao_vp.py
    para o levantamento completo). `typ: "kb+jwt"` é o valor padrão da
    spec SD-JWT para esse tipo de JWT (distinto de `_PROOF_JWT_TYP`, usado
    na EMISSÃO, não na apresentação).

    `sd_hash` CONFIRMADO/incluído (campo obrigatório da spec, ver
    `_compute_sd_hash`) — vincula este KB-JWT a ESTA apresentação
    específica (este Credential-JWT + estas Disclosures reveladas), contra
    reuso em outra apresentação."""
    key = get_issuer_key()
    header = {"alg": "EdDSA", "typ": "kb+jwt"}
    payload = {"aud": audience, "nonce": nonce, "iat": int(time.time()), "sd_hash": sd_hash}
    jws = JWS(json.dumps(payload).encode())
    jws.add_signature(key, alg="EdDSA", protected=json.dumps(header))
    return jws.serialize(compact=True)


@router.post("/present/{batch_number}")
async def apresentar_certificacao(batch_number: str, request: Request):
    """Apresenta a VC de certificação já guardada (ver
    `/retirar-certificacao`) em resposta a um presentation request do
    verifier-demo (`POST /presentation-request/{batch_number}`, nonce
    recebido no corpo). Monta a VP completa no formato SD-JWT:
    `<Credential-JWT>~<Disclosures>~<KB-JWT>`.

    Revela TODAS as disclosures disponíveis — seleção granular (revelar
    só um subconjunto) fica para depois, fora do escopo desta entrega.

    DECISÃO (consumo do nonce, documentada aqui por pedido explícito, não
    decidida silenciosamente): este endpoint NÃO marca o nonce como usado
    no store do verifier-demo — esse store pertence a outro processo
    (verifier-demo/presentation_request_endpoint.py), e marcar um recurso
    de outro nó como "usado" a partir de quem o CONSOME, sem o dono validar
    primeiro, inverteria a responsabilidade de verificação. Mesmo padrão
    já usado no projeto para `c_nonce` (nonce_endpoint.py): quem CONSOME
    a prova (o credential endpoint, lá; o endpoint de recebimento de VP,
    aqui) é quem valida e marca como usado — não existe ainda em
    verifier-demo (fora do escopo desta entrega), então o nonce fica
    "usado=False" no store de lá até esse endpoint futuro existir."""
    try:
        body = await request.json()
    except Exception:
        return _error(400, "invalid_request", "Corpo da requisição não é JSON válido.")

    if not isinstance(body, dict):
        return _error(400, "invalid_request", "Corpo da requisição deve ser um objeto JSON.")

    nonce = body.get("nonce")
    audience = body.get("audience")
    if not nonce or not audience:
        return _error(400, "invalid_request", "Campos obrigatórios ausentes: nonce, audience.")

    sd_jwt = _busca_credencial_guardada(batch_number)
    if sd_jwt is None:
        return _error(
            404,
            "credencial_nao_encontrada",
            f"Nenhuma VC de certificação guardada para o lote {batch_number!r} "
            f"(rode POST /lote/{batch_number}/retirar-certificacao primeiro).",
        )

    segments = sd_jwt.split("~")
    credential_jwt = segments[0]
    disclosures = [s for s in segments[1:] if s]

    sd_hash = _compute_sd_hash(credential_jwt, disclosures)
    kb_jwt = _build_kb_jwt(audience, nonce, sd_hash)

    vp = "~".join([credential_jwt, *disclosures, kb_jwt])

    logger.info("POST /present/%r: VP montada e apresentada.", batch_number)
    return {"batchNumber": batch_number, "vp": vp}
