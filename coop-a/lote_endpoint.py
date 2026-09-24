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

Escopo dos campos aceitos (JSON): `batchNumber`, `productType`,
`producerName`, `producerDID` são obrigatórios; `floralOrigin`,
`productionDate`, `apiaryLocation` ({"lat":..., "lon":...}) são opcionais —
mesma cautela de credential_endpoint.py contra campos ausentes (só entram no
grafo se vierem preenchidos).

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
import re

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import FOAF, RDF, XSD

from config import FUSEKI_ADMIN_PASSWORD, FUSEKI_ADMIN_USER, FUSEKI_URL
from shacl_pipeline import extract_violations, format_violations, insert_into_fuseki, run_shacl

router = APIRouter()

# Mesmos namespaces de coop-a/data/sample-instance.ttl.
BEE = Namespace("https://w3id.org/agrisemantics/AGRITRUST/bee-extension#")
AGRITRUST = Namespace("https://w3id.org/agrisemantics/AGRITRUST#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

_REQUIRED_FIELDS = ("batchNumber", "productType", "producerName", "producerDID")

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

    missing = [f for f in _REQUIRED_FIELDS if not body.get(f)]
    if missing:
        return _error(
            400,
            "invalid_request",
            f"Campos obrigatórios ausentes: {', '.join(missing)}.",
        )

    batch_number = str(body["batchNumber"])
    product_type = str(body["productType"])
    producer_name = str(body["producerName"])
    producer_did = str(body["producerDID"])
    floral_origin = body.get("floralOrigin")
    production_date = body.get("productionDate")
    apiary_location = body.get("apiaryLocation")

    if not _LOCAL_NAME_RE.match(product_type):
        return _error(
            400,
            "invalid_request",
            f"productType inválido: {product_type!r}. Esperado um nome de "
            f"classe da ontologia (ex.: 'HoneyApis'), só letras/dígitos/underscore.",
        )

    # --- 0. Resolve produtor existente por DID, antes de montar o grafo ---
    try:
        existing_producer = find_existing_producer(producer_did)
    except Exception as exc:  # noqa: BLE001 - mesmo nível de reporte do pipeline.py
        return _error(502, "server_error", f"Erro ao consultar o Fuseki: {exc}")

    # --- 1. Monta o grafo RDF (mesma "forma" de data/sample-instance.ttl) ---
    g = Graph()
    g.bind("agritrust", AGRITRUST)
    g.bind("geo", GEO)
    g.bind("foaf", FOAF)

    lote_uri = BEE[f"Lote_{_slug(batch_number)}"]

    if existing_producer is not None:
        # Reusa o recurso já cadastrado (mesmo agritrust:hasDID) — não
        # reinserimos rdf:type/foaf:name/hasDID: essas triplas já existem
        # para esse produtor, reafirmá-las não é errado (RDF é um
        # conjunto), mas não é necessário.
        producer_uri = URIRef(existing_producer)
    else:
        producer_uri = BEE[f"Produtor_{_slug(producer_did)}"]
        g.add((producer_uri, RDF.type, AGRITRUST.DataProducer))
        g.add((producer_uri, FOAF.name, Literal(producer_name)))
        g.add((producer_uri, AGRITRUST.hasDID, Literal(producer_did)))

    g.add((lote_uri, RDF.type, BEE[product_type]))
    g.add((lote_uri, AGRITRUST.ownedBy, producer_uri))
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
        # razão do _slug acima.
        geo_feature_uri = BEE[f"Apiario_{_slug(batch_number)}_Geo"]
        geo_point_uri = BEE[f"Apiario_{_slug(batch_number)}_Point"]
        g.add((producer_uri, AGRITRUST.hasOperatingLocation, geo_feature_uri))
        g.add((geo_feature_uri, RDF.type, GEO.Feature))
        g.add((geo_feature_uri, GEO.hasGeometry, geo_point_uri))
        g.add((geo_point_uri, RDF.type, GEO.Point))
        # Convenção do projeto: POINT(lon lat), não POINT(lat lon) — ver
        # coop-a/data/sample-instance.ttl e ontology/instances.ttl.
        g.add((geo_point_uri, GEO.asWKT, Literal(f"POINT({lon} {lat})", datatype=GEO.wktLiteral)))

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

    return {
        "status": "inserted",
        "batchNumber": batch_number,
        "lote": str(lote_uri),
        "producer": str(producer_uri),
    }
