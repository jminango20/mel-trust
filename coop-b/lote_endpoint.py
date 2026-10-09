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
mensagem genérica de "campo obrigatório ausente". Réplica exata de
coop-a/lote_endpoint.py — ver docstring lá para o raciocínio completo.

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
import logging
import re
import uuid

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import FOAF, RDF, XSD

from anchoring import anchor_batch, get_onchain_data_hash
from config import FUSEKI_ADMIN_PASSWORD, FUSEKI_ADMIN_USER, FUSEKI_URL
from hash_utils import deterministic_nt_hash
from pre_auth_codes import generate_for_batch, reissue_for_batch
from shacl_pipeline import extract_violations, format_violations, insert_into_fuseki, run_shacl

logger = logging.getLogger(__name__)

router = APIRouter()

# Mesmos namespaces de coop-b/data/sample-instance.ttl.
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
    geo:Point em coop-a/data/sample-instance.ttl e
    coop-b/data/sample-instance.ttl (ver CHANGELOG.md): reenviar o mesmo
    batchNumber/producerDID não deve criar um recurso novo a cada chamada, e
    sim reafirmar as mesmas triplas (INSERT DATA é idempotente para triplas
    idênticas)."""
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def _error(status_code: int, error: str, description: str, extra: dict | None = None) -> JSONResponse:
    body = {"error": error, "error_description": description}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Mesmo nível de escape simples usado em coop-b/credential_endpoint.py e
    demais nós — não é um escapador SPARQL genérico e completo (ver
    docs/known-issues.md).
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# --- CONSTRUCT usado para reconstruir, a partir do Fuseki, EXATAMENTE o
# mesmo conjunto de triplas que `verifier-demo/onchain_verification.py`
# reconstrói para recalcular o hash (réplica EXATA de
# `coop-a/lote_endpoint.py:_LOTE_CONSTRUCT_TEMPLATE`, mesmo achado de
# integridade: ancorar o hash do objeto `g` em memória falha quando o
# produtor é reaproveitado, porque `g` só ganha as triplas do produtor se
# ele for NOVO — ver ACHADO DE INTEGRIDADE no passo 5 de `cadastrar_lote`
# abaixo, e tests/features/ancoragem_lote_produtor_reaproveitado.feature).
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
    integridade — réplica exata de `coop-a/lote_endpoint.py`. Ver ACHADO DE
    INTEGRIDADE no passo 5 de `cadastrar_lote` para o motivo de ancorar o
    hash deste grafo reconstruído, e não do grafo `g` em memória."""
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
    mesmo batchNumber (ex.: fixture legado com URI escrita à mão vs. a URI
    que este endpoint mintaria — foi exatamente esse tipo de duplicata,
    achada no coop-a, que motivou esta checagem; ver docs/known-issues.md).
    Retorna a URI (string) do primeiro achado, ou None se batchNumber ainda
    não existe."""
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

    success, tx_hash, error = anchor_batch(batch_number, _nature_id_from_graph(graph), data_hash)
    if not success:
        return {**result, "outcome": "failed", "error": error}
    write_anchoring_task(batch_number, "anchored", tx_hash)
    return {**result, "outcome": "sent", "txHash": tx_hash}


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
        # claim que coop-b/credential_endpoint.py lê de volta como
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
        # Réplica exata de coop-a/lote_endpoint.py — ver docstring lá.
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
    # Só se batchNumber veio — ver docstring de coop-a/lote_endpoint.py
    # (mesmo raciocínio de caso de borda: sem batchNumber E sem productType,
    # HoneyApisIdentificationShape não se aplica ao nó e ele pode passar).
    pre_authorized_code = generate_for_batch(batch_number) if batch_number is not None else None

    # --- 5. Ancoragem on-chain (M8) — réplica exata de
    # coop-a/lote_endpoint.py (ver comentário lá para o raciocínio
    # completo), melhor esforço, nunca reverte a inserção no Fuseki.
    #
    # ACHADO DE INTEGRIDADE (confirmado nesta sessão via
    # tests/features/ancoragem_lote_produtor_reaproveitado.feature):
    # ancorar o hash do objeto `g` em memória falha quando o PRODUTOR é
    # reaproveitado (`is_new_producer=False`) — `g` só ganha as triplas do
    # produtor (`rdf:type`/`foaf:name`/`hasDID`) quando ele é NOVO; um
    # verificador externo reconstruindo via SPARQL CONSTRUCT sempre traz o
    # produtor inteiro (ele existe no Fuseki de qualquer forma). Resultado:
    # hash ancorado sobre 3 triplas vs. hash recalculado sobre 6 —
    # mismatch determinístico, não um problema de FORMA de serialização.
    #
    # CORREÇÃO: depois do INSERT (passo 3), reconstrói via
    # `_construct_lote_graph_for_hash` o MESMO conjunto de triplas que o
    # verificador vai reconstruir (CONSTRUCT idêntico ao de
    # `onchain_verification.py`) e ancora o hash DESSE grafo — garante, por
    # construção, que o hash ancorado é sempre recalculável, independente
    # de o produtor ser novo ou reaproveitado. Custo: um round-trip SPARQL
    # extra ao próprio Fuseki (aceitável — rede zero-gas, chamada local).
    # `deterministic_nt_hash` (hash_utils.py) resolve, de quebra, o
    # não-determinismo de ordem de serialização entre processos (achado
    # anterior, já corrigido em coop-a/lab) — este endpoint nunca tinha
    # recebido essa correção até agora.
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
    """Invalida o pre_authorized_code atual do lote e gera um novo. Réplica
    exata de coop-a/lote_endpoint.py — ver docstring lá."""
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
