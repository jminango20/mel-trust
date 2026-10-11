"""
Credential endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M3.

Réplica exata do MECANISMO de coop-a/credential_endpoint.py (autenticação
via access_token, validação do key proof via c_nonce single-use, montagem e
assinatura do SD-JWT) — ver a docstring lá para a justificativa completa
das duas notas de confiança sobre a spec (campo `credential_configuration_id`
no request, formato da resposta de sucesso) e da simplificação de segurança
do proof JWT. Não repetido aqui em detalhe — o mecanismo é idêntico.

O que MUDA em relação a coop-a/coop-b: o TIPO de credencial e seus claims.
O certifier não emite uma credencial de "lote original" (isso é papel da
cooperativa que produziu o mel) — ele emite uma credencial de CERTIFICAÇÃO:
o resultado de uma análise laboratorial (laudo) sobre uma amostra de um
lote que a cooperativa já produziu e cadastrou.

Tipo: `HoneyCertificationCredential`
(vct = https://agritrust.example/vct/honey-certification), distinto de
`HoneyBatchCredential` (vct = .../honey-batch) usado por coop-a/coop-b — ver
docstring original de M3 para a justificativa completa de ter dois VCTs.

M5.6 (pós-narrativa) — REESTRUTURAÇÃO: o certifier deixou de ler fixture
.ttl local (certifier/data/sample-lab-result-*.ttl via pipeline.py, ambos
já removidos do repo) para montar os claims desta credencial.

HISTÓRICO (M6, revertido): por um turno, o certifier chegou a parar de
federar com o lab (que virou stateless) e passou a ler medições só de
coop-a/coop-b (`POST /lote/{batchNumber}/resultado-analise`). Isso foi
REVERTIDO — o lab voltou a ter Fuseki próprio (`POST /analise` persiste de
novo) e o certifier voltou a federar com os TRÊS (coop-a/coop-b + lab), como
era originalmente. Ver docs/known-issues.md/CHANGELOG.md.

O fluxo (Parte 4 abaixo), a cada chamada de `/credential`:
  1. Roda UMA query SPARQL federada (mesmo padrão `SERVICE` real de
     verifier-demo/verify_endpoint.py — ver a docstring de lá para a
     explicação completa da mecânica), enviada ao PRÓPRIO Fuseki do
     certifier (nó de entrada), que resolve dois blocos `SERVICE`:
       - coop-a OU coop-b (UNION): busca o `productType` do lote (o
         certifier precisa saber se é `:HoneyApis` ou
         `:HoneyNativeStinglessBee` para o SHACL regulatório aplicar o
         limite certo).
       - lab (OPTIONAL): busca `moistureContent`/`hmfContent`/`acidityContent`/
         `diastaseActivity` — as medições físico-químicas de verdade,
         vindas do nó `lab/` (POST /analise), não gravadas localmente no
         certifier.
  2. Monta um grafo RDF EFêMERO, em memória, nunca persistido em nenhum
     Fuseki: um recurso com `rdf:type` = o `productType` encontrado, e as
     medições encontradas como propriedades (mesmas URIs de sempre:
     `:hasMoistureContent` etc.) — mesma técnica que
     `verify_endpoint.py:validate_response_shape` já usa para validar a
     FORMA da resposta agregada; aqui o uso é validar o CONTEÚDO contra
     regra regulatória, não a forma.
  3. Roda `pyshacl.validate` desse grafo efêmero contra
     `ontology/validation-shapes.ttl` — as MESMAS shapes regulatórias reais
     (`HoneyApisMoistureShape`, `HoneyNativeMoistureShape` etc.) que
     `coop-a/pipeline.py`/`coop-b/pipeline.py` já rodam na entrada do
     lote. Antes desta reestruturação, essas shapes regulatórias nunca
     eram aplicadas pelo certifier — ele só lia `agritrust:legalStatus`/
     `agritrust:qualityStatus` como literais já prontos no fixture (ou
     seja, a "aprovação" era um dado de exemplo escrito à mão, não uma
     decisão calculada). Agora é calculada de verdade a cada emissão.
  4. Só se `conforms=True`: monta `legalStatus`/`qualityStatus` (derivados
     do resultado do SHACL, não mais lidos de um literal do grafo — ver
     `_decidir_status` abaixo) e segue para assinar a credencial (Parte 5,
     inalterada). Se `conforms=False`: `POST /credential` retorna `400`
     com as violações (mesmo contrato de erro de `coop-a/lote_endpoint.py`)
     — NENHUMA credencial é emitida para um lote fora do padrão
     regulatório.

`floralOrigin` continua FORA (mudou de dono para coop-a/coop-b antes desta
reestruturação — dado declarado pelo produtor na colheita, não confirmado
pelo laboratório; sem relação com a mudança desta entrega).

AUSENTES da credencial, sem mudança nesta entrega (sem correspondência
possível ainda, nem no lab nem em coop-a/coop-b): `certificateType`,
`certificateNumber`, o objeto `analysisActivity`
(startedAtTime/endedAtTime/executedBy) e `certificateReference`. Migração
prevista pra M5.5. Ver docs/notas-arquitetura/certifier-claims-hardcoded-m4.md.
"""
import logging
import os
import re
import time

import jwt as pyjwt
import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jwcrypto.jwk import JWK
from pyshacl import validate as shacl_validate
from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.namespace import XSD
from sd_jwt.common import SDObj
from sd_jwt.issuer import SDJWTIssuer
from web3 import Web3

from anchoring import (
    anchor_certification,
    get_onchain_data_hash,
    is_known_transaction_error,
    parent_exists_onchain,
)
from config import (
    COOP_A_SPARQL_SERVICE_URL,
    COOP_B_SPARQL_SERVICE_URL,
    CREDENTIAL_VALIDITY_SECONDS,
    DEFAULT_BATCH_NUMBER,
    DID_WEB,
    FUSEKI_ADMIN_PASSWORD,
    FUSEKI_ADMIN_USER,
    FUSEKI_URL,
    LAB_SPARQL_SERVICE_URL,
)
from keys import get_issuer_key
from nonce_endpoint import is_valid_c_nonce
from token_endpoint import is_valid_access_token

router = APIRouter()

logger = logging.getLogger(__name__)

# Deve bater com a chave usada em
# main.py:credential_configurations_supported (M1, corrigido nesta entrega —
# ver comentário em main.py sobre o bug herdado do M1 nos três nós).
CREDENTIAL_CONFIGURATION_ID = "HoneyCertificationCredential"

# VCT NOVO, distinto de honey-batch (coop-a/coop-b) — ver docstring do
# módulo para a justificativa de ter dois vct's.
VCT = "https://agritrust.example/vct/honey-certification"

# Header `typ` do JWS — `dc+sd-jwt`, não `vc+sd-jwt` (mesma mudança de media
# type aplicada em coop-a/coop-b).
SD_JWT_TYP = "dc+sd-jwt"

# `typ` esperado no header do JWT de key proof enviado pelo holder,
# conforme OID4VCI ("JWT Proof Type"). Não é verificado estritamente aqui
# (ver simplificação de proof abaixo), só documentado.
PROOF_JWT_TYP = "openid4vci-proof+jwt"


# --- M5.6: query SPARQL FEDERADA (SERVICE) contra coop-a/coop-b + lab ---
#
# Namespaces do bee-extension/agritrust de sempre — os MESMOS usados em
# ontology/validation-shapes.ttl e em coop-a/coop-b/lab.
_SPARQL_PREFIXES = """
PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
"""

BEE = Namespace("https://w3id.org/agrisemantics/AGRITRUST/bee-extension#")
AGRITRUST = Namespace("https://w3id.org/agrisemantics/AGRITRUST#")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGULATORY_SHAPES_PATH = os.path.join(REPO_ROOT, "ontology", "validation-shapes.ttl")

# Query federada: UNION entre coop-a e coop-b pra achar o productType do
# lote (mesma lógica de "nunca os dois, um lote existe numa cooperativa só"
# de verify_endpoint.py) + OPTIONAL SERVICE no lab pras medições. Enviada
# ao PRÓPRIO Fuseki do certifier (nó de entrada — resolve os SERVICE).
#
# `ORDER BY ?batch` + `LIMIT 1`: achado real (não hipotético) rodando
# tests/step_defs/test_lab_certifier.py — o coop-a acumulou DOIS recursos
# distintos para o mesmo agritrust:batchNumber "MS-2026-042"
# (:Lote_Mel_Silvestre_2026_042, do fixture original, e :Lote_MS_2026_042,
# criado por POST /lote em teste anterior). Sem ORDER BY, qual dos dois o
# SELECT ... LIMIT 1 devolve não é garantido por nenhuma regra SPARQL — é
# implementação-dependente do Fuseki. `?batch` (o próprio recurso do lote)
# não era projetado antes; agora é, só para servir de critério de
# ordenação determinístico — não é usado em nenhum claim da credencial.
# Isso NÃO resolve a duplicação em si (ver docs/known-issues.md), só torna
# o RESULTADO reprodutível entre chamadas repetidas.
_FEDERATED_QUERY_TEMPLATE = _SPARQL_PREFIXES + """
SELECT ?batch ?productType ?producer ?moisture ?hmf ?acidity ?diastase
WHERE {
  {
    SERVICE <%(coop_a_service_url)s> {
      ?batch agritrust:batchNumber %(batch_number)s ;
             a ?productType ;
             agritrust:ownedBy ?producer .
    }
  }
  UNION
  {
    SERVICE <%(coop_b_service_url)s> {
      ?batch agritrust:batchNumber %(batch_number)s ;
             a ?productType ;
             agritrust:ownedBy ?producer .
    }
  }
  OPTIONAL {
    SERVICE <%(lab_service_url)s> {
      ?analise agritrust:batchNumber %(batch_number)s .
      OPTIONAL { ?analise :hasMoistureContent ?moisture }
      OPTIONAL { ?analise :hasHMFContent ?hmf }
      OPTIONAL { ?analise :hasAcidity ?acidity }
      OPTIONAL { ?analise :hasDiastaseActivity ?diastase }
    }
  }
}
ORDER BY ?batch
LIMIT 1
"""


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Mesmo nível de escape simples de coop-a (não é um escapador SPARQL
    genérico e completo) — ver docs/known-issues.md.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _query_federated(batch_number: str) -> dict | None:
    """Roda a query federada única (productType via coop-a/coop-b + medições
    via lab) contra o Fuseki de ENTRADA (o próprio certifier).

    Retorna None se o lote não foi achado em NENHUMA cooperativa (nem
    productType) OU se foi achado mas o lab não tem NENHUMA medição pra ele
    ainda (sem dado físico-químico, não há o que validar) — o chamador
    decide o HTTP status (404), esta função só decide "achei o suficiente
    pra tentar validar, ou não".
    """
    query = _FEDERATED_QUERY_TEMPLATE % {
        "batch_number": _sparql_literal(batch_number),
        "coop_a_service_url": COOP_A_SPARQL_SERVICE_URL,
        "coop_b_service_url": COOP_B_SPARQL_SERVICE_URL,
        "lab_service_url": LAB_SPARQL_SERVICE_URL,
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

    row = bindings[0]

    def _val(key: str) -> str | None:
        cell = row.get(key)
        return cell["value"] if cell else None

    product_type = _val("productType")
    producer = _val("producer")
    moisture = _val("moisture")
    hmf = _val("hmf")
    acidity = _val("acidity")
    diastase = _val("diastase")

    if product_type is None:
        return None
    if moisture is None and hmf is None and acidity is None and diastase is None:
        # Lote existe em coop-a/coop-b, mas o lab não tem NENHUMA medição
        # pra ele ainda — nada a validar/certificar.
        return None

    return {
        "productType": product_type,
        "producer": producer,
        "moisture": moisture,
        "hmf": hmf,
        "acidity": acidity,
        "diastase": diastase,
    }


def _monta_grafo_efemero(batch_number: str, row: dict) -> Graph:
    """Grafo RDF EM MEMÓRIA, nunca persistido em nenhum Fuseki — só pra
    rodar pyshacl contra as shapes regulatórias reais. Mesma técnica de
    verify_endpoint.py:validate_response_shape (montar grafo a partir de um
    resultado SPARQL federado pra validar, não pra gravar).

    Inclui `agritrust:batchNumber`/`agritrust:ownedBy` (não só as medições
    físico-químicas): `HoneyApisIdentificationShape`
    (ontology/validation-shapes.ttl) exige os dois com `sh:minCount 1` —
    sem eles, QUALQUER lote reprovaria por identificação ausente, mesmo com
    medições dentro do limite. `ownedBy` vem do MESMO resultado federado
    (coop-a/coop-b), não é inventado aqui."""
    g = Graph()
    node = BEE["AnaliseEfemera"]
    g.add((node, RDF.type, URIRef(row["productType"])))
    g.add((node, AGRITRUST.batchNumber, Literal(batch_number)))
    g.add((node, AGRITRUST.ownedBy, URIRef(row["producer"])))
    if row["moisture"] is not None:
        g.add((node, BEE.hasMoistureContent, Literal(row["moisture"], datatype=XSD.decimal)))
    if row["hmf"] is not None:
        g.add((node, BEE.hasHMFContent, Literal(row["hmf"], datatype=XSD.decimal)))
    if row["acidity"] is not None:
        g.add((node, BEE.hasAcidity, Literal(row["acidity"], datatype=XSD.decimal)))
    if row["diastase"] is not None:
        g.add((node, BEE.hasDiastaseActivity, Literal(row["diastase"], datatype=XSD.decimal)))
    return g


def _run_regulatory_shacl(data_graph: Graph):
    """Roda pyshacl contra ontology/validation-shapes.ttl — as MESMAS shapes
    regulatórias que coop-a/coop-b/pipeline.py já usam na entrada do lote.
    Mesmos parâmetros de validate() usados em coop-a/shacl_pipeline.py."""
    shapes_graph = Graph()
    shapes_graph.parse(REGULATORY_SHAPES_PATH, format="turtle")
    conforms, results_graph, results_text = shacl_validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="none",
        allow_warnings=True,
        allow_infos=True,
    )
    return conforms, results_graph


def _extract_violations(results_graph: Graph) -> list[str]:
    """Mesmo formato de linha usado em coop-a/shacl_pipeline.py."""
    query = """
    PREFIX sh: <http://www.w3.org/ns/shacl#>
    SELECT ?focusNode ?severity ?message WHERE {
        ?result a sh:ValidationResult ;
                sh:focusNode ?focusNode ;
                sh:resultSeverity ?severity .
        OPTIONAL { ?result sh:resultMessage ?message }
    }
    """
    lines = []
    for focus_node, severity, message in results_graph.query(query):
        severity_label = str(severity).rsplit("#", 1)[-1]
        msg = message if message is not None else "(sem sh:resultMessage)"
        lines.append(f"[{severity_label}] {focus_node}: {msg}")
    return lines


def _decidir_status(product_type_uri: str) -> tuple[str, str]:
    """Deriva (legalStatus, qualityStatus) a partir do productType, DEPOIS
    de conforms=True já confirmado pelo SHACL regulatório. Antes desta
    reestruturação, esses dois textos eram literais lidos direto de um
    fixture .ttl (dado de exemplo escrito à mão); agora são DERIVADOS da
    decisão real do SHACL — texto plausível por tipo de produto,
    simplificação documentada (não pretende ser a redação jurídica exata
    de cada norma)."""
    local_name = product_type_uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if local_name == "HoneyNativeStinglessBee":
        quality = "Em Conformidade com Padrão Científico para Meliponídeos e Portaria 1.439/2025"
    elif local_name == "HoneyApis":
        quality = "Em Conformidade com IN 11/2000 e Portaria 795/2023"
    else:
        quality = "Em conformidade com os limites regulatórios aplicáveis"
    return "Aprovado para Comercialização", quality


def _oauth_error(status_code: int, error: str, description: str, www_authenticate: str | None = None) -> JSONResponse:
    headers = {"WWW-Authenticate": www_authenticate} if www_authenticate else None
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
        headers=headers,
    )


def _extract_bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def _decode_proof_jwt(jwt_str: str) -> tuple[dict, dict]:
    """
    Decodifica header e payload do JWT de key proof SEM verificar a
    assinatura contra uma PKI real de holder. Mesma simplificação
    deliberada de MVP documentada em coop-a/credential_endpoint.py: quem
    efetivamente barra replay/forjamento neste milestone é o c_nonce de uso
    único, não a assinatura do proof em si (isso é trabalho de M5).
    """
    header = pyjwt.get_unverified_header(jwt_str)
    payload = pyjwt.decode(jwt_str, options={"verify_signature": False})
    return header, payload


# --- OUTBOX DE ANCORAGEM DA VC DE CERTIFICAÇÃO ---------------------------------
# Mesmo desenho do outbox de coop-a/coop-b/lab; ver
# tests/features/ancoragem_outbox_certifier.feature. DECISÕES:
# 1. O hash ancorado é o keccak256 do SD-JWT COMPLETO emitido (salts aleatórios
#    nas disclosures): NÃO é reconstruível de nenhum dado. Por isso a
#    `AnchoringTask` — recurso SEPARADO no Fuseki do certifier,
#    `:AnchoringTask_<slug(batchNumber)>` — GUARDA, no momento da emissão: o
#    hash (`:anchoringDataHash`, "0x…"), o batchNumber (`:anchoringBatch`) e o
#    natureId/productType (`:anchoringNature`). O retry reenvia o hash GUARDADO e
#    nunca recalcula; o natureId guardado evita depender de consulta federada.
#    LIMITAÇÃO: tarefa perdida => hash irrecuperável (a VC já foi entregue, mas
#    fica sem ancoragem até uma nova emissão).
# 2. assetId do documento = keccak256(batchNumber + "-certification"); pai =
#    keccak256(batchNumber) (ver anchoring.py).
# 3. Um documento por lote: se já existe tarefa para o batchNumber, em QUALQUER
#    estado, POST /credential responde 409 ANTES de assinar qualquer coisa.
# 4. Ordem em POST /credential: (409?) -> assina o SD-JWT -> grava `pending`
#    (com hash + natureId) -> ancora -> `anchored`. Se gravar a tarefa falhar, a
#    VC NÃO é entregue (502): sem tarefa o hash não seria recuperável e o 409 não
#    protegeria o lote de uma reemissão.
# 5. Retry manual (POST /anchoring/retry); sem scheduler na PoC.
# Estados: pending | anchored | mismatch (asset on-chain com hash DIFERENTE do
# guardado — nunca vira anchored, é reexaminado a cada retry).
_TASK_PREFIXES = f"""PREFIX : <{BEE}>\n"""


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


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


def _fuseki_select(query: str) -> list[dict]:
    response = requests.post(
        f"{FUSEKI_URL}/sparql",
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["results"]["bindings"]


def task_exists(batch_number: str) -> bool:
    """Já existe AnchoringTask (em qualquer estado) para o batchNumber?"""
    rows = _fuseki_select(f"""{_TASK_PREFIXES}
    SELECT ?t WHERE {{ ?t :anchoringBatch {_sparql_literal(batch_number)} }} LIMIT 1""")
    return bool(rows)


def create_pending_task(batch_number: str, data_hash: bytes, nature_id: str) -> None:
    """Grava a tarefa `pending` GUARDANDO hash + natureId (única chance: o hash não é recuperável depois)."""
    task = _task_uri(batch_number)
    _fuseki_update(f"""{_TASK_PREFIXES}
    DELETE WHERE {{ <{task}> ?p ?o }} ;
    INSERT DATA {{
      <{task}> a :AnchoringTask ;
        :anchoringBatch {_sparql_literal(batch_number)} ;
        :anchoringStatus "pending" ;
        :anchoringDataHash {_sparql_literal("0x" + bytes(data_hash).hex())} ;
        :anchoringNature {_sparql_literal(nature_id)} .
    }}""")


def set_task_status(batch_number: str, status: str, tx_hash: str | None = None, detail: str | None = None) -> None:
    """Troca SÓ status/txHash/detail da tarefa, preservando o hash e o natureId guardados."""
    inserts = f":anchoringStatus {_sparql_literal(status)}"
    if tx_hash:
        inserts += f" ; :anchoringTxHash {_sparql_literal(tx_hash)}"
    if detail:
        inserts += f" ; :anchoringDetail {_sparql_literal(detail)}"
    _fuseki_update(f"""{_TASK_PREFIXES}
    DELETE {{ ?t :anchoringStatus ?s . ?t :anchoringTxHash ?x . ?t :anchoringDetail ?d }}
    INSERT {{ ?t {inserts} }}
    WHERE  {{ ?t :anchoringBatch {_sparql_literal(batch_number)} ; :anchoringStatus ?s .
             OPTIONAL {{ ?t :anchoringTxHash ?x }} OPTIONAL {{ ?t :anchoringDetail ?d }} }}""")


def _safe_set_task_status(batch_number: str, status: str, tx_hash: str | None = None) -> None:
    """Em POST /credential a VC já foi entregue/ancorada: falha aqui só é logada
    (a tarefa fica `pending` e o retry resolve via getAsset)."""
    try:
        set_task_status(batch_number, status, tx_hash)
    except Exception as exc:  # noqa: BLE001
        logger.error("POST /credential: falha ao gravar AnchoringTask %s da VC do lote %r: %s", status, batch_number, exc)


def read_task(batch_number: str) -> dict | None:
    rows = _fuseki_select(f"""{_TASK_PREFIXES}
    SELECT ?hash ?nature WHERE {{
      ?t :anchoringBatch {_sparql_literal(batch_number)} .
      OPTIONAL {{ ?t :anchoringDataHash ?hash }} OPTIONAL {{ ?t :anchoringNature ?nature }}
    }} LIMIT 1""")
    if not rows:
        return None
    return {"dataHash": rows[0].get("hash", {}).get("value"), "nature": rows[0].get("nature", {}).get("value")}


def _pending_batches() -> list[str]:
    """VCs a processar no retry: `pending` E `mismatch` (este é REEXAMINADO a cada
    retry — nunca enviado, só reportado de novo — e só vira `anchored` se o hash
    on-chain passar a bater com o guardado)."""
    rows = _fuseki_select(f"""{_TASK_PREFIXES}
    SELECT ?b WHERE {{ ?t :anchoringStatus ?s ; :anchoringBatch ?b .
    FILTER(?s IN ("pending", "mismatch")) }} ORDER BY ?b""")
    return [r["b"]["value"] for r in rows]


def _resolve_existing(batch_number: str, data_hash: bytes, onchain_hash: bytes | None, result: dict) -> dict:
    """O asset da VC JÁ existe on-chain: compara com o hash GUARDADO e resolve sem
    enviar nada. Bate => `anchored` (already_onchain). Não bate => `mismatch`."""
    if onchain_hash is not None and onchain_hash == bytes(data_hash):
        set_task_status(batch_number, "anchored")
        return {**result, "outcome": "already_onchain"}
    detail = (
        f"hash on-chain 0x{onchain_hash.hex() if onchain_hash else ''} "
        f"!= hash guardado 0x{bytes(data_hash).hex()}"
    )
    set_task_status(batch_number, "mismatch", detail=detail)
    logger.error("Retry de ancoragem da VC: MISMATCH no lote %r: %s", batch_number, detail)
    return {**result, "outcome": "mismatch", "detail": detail}


def _retry_one(batch_number: str) -> dict:
    result = {"batchNumber": batch_number, "txHash": None}

    task = read_task(batch_number)
    stored = (task or {}).get("dataHash") or ""
    nature_id = (task or {}).get("nature")
    if task is None or not re.fullmatch(r"0x[0-9a-fA-F]{64}", stored) or not nature_id:
        # Sem hash/natureId guardados não há o que reenviar — e o hash do SD-JWT não é recuperável.
        return {**result, "outcome": "failed",
                "error": "tarefa sem hash/natureId guardados válidos; o hash do SD-JWT não é recuperável"}
    data_hash = bytes.fromhex(stored[2:])

    # getAsset ANTES de qualquer tx: se levantar (RPC fora), propaga — não
    # reenvia às cegas uma tx que talvez já exista.
    exists, onchain_hash = get_onchain_data_hash(batch_number)
    if exists:
        return _resolve_existing(batch_number, data_hash, onchain_hash, result)

    # A VC é um documento FILHO: sem o asset do lote on-chain o createDocument
    # reverte. Não envia; espera o pai (a tarefa fica pending).
    if not parent_exists_onchain(batch_number):
        return {**result, "outcome": "waiting_parent",
                "detail": "asset do lote (pai) ainda não existe on-chain; nada enviado"}

    success, tx_hash, error = anchor_certification(batch_number, nature_id, data_hash)
    if success:
        set_task_status(batch_number, "anchored", tx_hash)
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
    """Processa todas as tarefas `pending` (e reexamina as `mismatch`), reenviando o
    hash GUARDADO. Sync (def) de propósito: faz I/O bloqueante (requests/web3) e o
    FastAPI roda em threadpool."""
    try:
        batches = _pending_batches()
    except Exception as exc:  # noqa: BLE001
        return _oauth_error(502, "server_error", f"Erro ao consultar o Fuseki: {exc}")

    results = []
    for batch_number in batches:
        try:
            results.append(_retry_one(batch_number))
        except Exception as exc:  # noqa: BLE001 - uma tarefa com erro não interrompe as outras
            logger.error("Retry de ancoragem da VC: erro no lote %r: %s", batch_number, exc)
            results.append(
                {"batchNumber": batch_number, "txHash": None, "outcome": "error",
                 "error": f"{type(exc).__name__}: {exc}"}
            )
    return {"processed": len(results), "results": results}


@router.post("/credential")
async def credential_endpoint(request: Request):
    # --- 1. Autenticação via access_token (RFC 6750 Bearer Token) ---
    access_token = _extract_bearer_token(request.headers.get("authorization"))
    if access_token is None:
        return _oauth_error(
            401,
            "invalid_token",
            "Header Authorization ausente ou não é um Bearer token.",
            www_authenticate="Bearer",
        )
    if not is_valid_access_token(access_token):
        return _oauth_error(
            401,
            "invalid_token",
            "access_token inválido, expirado ou desconhecido.",
            www_authenticate='Bearer error="invalid_token"',
        )

    # --- 2. Corpo da requisição ---
    try:
        body = await request.json()
    except Exception:
        return _oauth_error(400, "invalid_request", "Corpo da requisição não é JSON válido.")

    if not isinstance(body, dict):
        return _oauth_error(400, "invalid_request", "Corpo da requisição deve ser um objeto JSON.")

    credential_configuration_id = body.get("credential_configuration_id")
    if credential_configuration_id != CREDENTIAL_CONFIGURATION_ID:
        return _oauth_error(
            400,
            "invalid_request",
            f"credential_configuration_id ausente ou não suportado. "
            f"Esperado: {CREDENTIAL_CONFIGURATION_ID!r}.",
        )

    # `batch_number`: campo OPCIONAL, EXTENSÃO PRÓPRIA DESTE MVP (mesma
    # decisão de coop-a/coop-b). Aqui identifica o LOTE cujo laudo se quer
    # consultar — não um identificador próprio do laudo (ver comentário da
    # query acima). Se ausente, cai no DEFAULT_BATCH_NUMBER.
    batch_number = body.get("batch_number") or DEFAULT_BATCH_NUMBER

    # --- 2b. Um documento de certificação POR LOTE: se já existe AnchoringTask
    # (em QUALQUER estado) para o batchNumber, 409 ANTES de assinar qualquer
    # coisa e antes de consumir o c_nonce — nenhuma VC nova, nenhuma tarefa nova.
    # Reemitir geraria outro SD-JWT (salts novos => hash novo) que não cabe no
    # mesmo asset (keccak256(batch + "-certification")); atualizar exigiria
    # updateAsset/UPDATE_ASSET (ver docs/known-issues.md). Nota: o endpoint é
    # `async def` com I/O bloqueante, então as requisições rodam uma a uma e
    # checar-e-depois-gravar não tem corrida dentro do processo. ---
    try:
        already_issued = task_exists(batch_number)
    except requests.RequestException as exc:
        return _oauth_error(502, "server_error", f"Falha ao consultar o Fuseki: {exc}")
    if already_issued:
        return _oauth_error(
            409,
            "credential_already_issued",
            f"O lote {batch_number!r} já tem uma certificação emitida (um documento por lote). "
            f"Nenhuma nova VC foi emitida.",
        )

    # --- 3. Proof de posse de chave (key proof) ---
    proof = body.get("proof")
    if not isinstance(proof, dict) or proof.get("proof_type") != "jwt" or not proof.get("jwt"):
        return _oauth_error(
            400,
            "invalid_proof",
            'Campo "proof" ausente ou malformado. Esperado {"proof_type": "jwt", "jwt": "<JWT>"}.',
        )

    try:
        proof_header, proof_payload = _decode_proof_jwt(proof["jwt"])
    except Exception:
        return _oauth_error(400, "invalid_proof", "JWT de proof não pôde ser decodificado.")

    c_nonce = proof_payload.get("nonce")
    if not c_nonce or not is_valid_c_nonce(c_nonce):
        return _oauth_error(
            400,
            "invalid_proof",
            "Claim 'nonce' ausente do proof, ou c_nonce inválido/expirado/já usado. "
            "Obtenha um c_nonce novo em POST /nonce e tente novamente.",
        )

    # Chave pública do holder, se anunciada no header do proof JWT (parâmetro
    # de header `jwk`, padrão da spec). Usada só para o claim `cnf` de key
    # binding na credencial emitida.
    holder_jwk_dict = proof_header.get("jwk")
    holder_key = JWK(**holder_jwk_dict) if holder_jwk_dict else None

    # --- 4. Consulta federada (coop-a/coop-b + lab) + SHACL regulatório
    # real — ver docstring do módulo (M5.6) para o fluxo completo ---
    try:
        row = _query_federated(batch_number)
    except requests.RequestException as exc:
        # Falha de infraestrutura (algum dos 3 Fuseki fora do ar, timeout,
        # credencial de admin errada etc.) — 502, mesmo padrão de antes.
        return _oauth_error(502, "server_error", f"Falha ao consultar o Fuseki: {exc}")

    if row is None:
        # Lote não encontrado em coop-a/coop-b, OU encontrado mas sem
        # nenhuma medição no lab ainda — não emitimos uma credencial de
        # certificação vazia/quebrada, retornamos 404.
        return _oauth_error(
            404,
            "invalid_request",
            f"Lote {batch_number!r} não encontrado em coop-a/coop-b, ou sem "
            f"nenhuma análise registrada no lab (POST /analise) ainda.",
        )

    conforms, results_graph = _run_regulatory_shacl(_monta_grafo_efemero(batch_number, row))
    if not conforms:
        # Dado existe (lote + análise do lab), mas fora do limite
        # regulatório (HoneyApisMoistureShape etc.) — NENHUMA credencial de
        # certificação é emitida. Mesmo contrato de erro de
        # coop-a/lote_endpoint.py (400, error=shacl_violation, violations).
        return JSONResponse(
            status_code=400,
            content={
                "error": "shacl_violation",
                "error_description": (
                    "Dado do lote não passou na validação SHACL regulatória. "
                    "Nenhuma credencial de certificação foi emitida."
                ),
                "violations": _extract_violations(results_graph),
            },
        )

    legal_status, quality_status = _decidir_status(row["productType"])

    # `natureId` para a ancoragem on-chain (Passo 6 abaixo): productType
    # vem da query federada como URI completa
    # (https://w3id.org/.../bee-extension#HoneyApis) — mesma extração de
    # nome local já usada em `_decidir_status` acima e em
    # `lab/analise_endpoint.py:_resolve_product_type`, para casar com o
    # `natureId` ("HoneyApis"/"HoneyNativeStinglessBee") registrado no
    # ProcessRegistry (honey-certification-document).
    nature_id = row["productType"].rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    now = int(time.time())
    exp = now + CREDENTIAL_VALIDITY_SECONDS

    user_claims = {
        # --- Sempre visíveis (fora de _sd) ---
        # Mesma justificativa estrutural de coop-a/coop-b para vct/iss.
        "vct": VCT,
        "iss": DID_WEB,
        "iat": now,
        "exp": exp,
        # `batchNumber`: vínculo entre esta credencial de CERTIFICAÇÃO e o
        # lote ORIGINAL emitido por coop-a/coop-b. Sempre visível pela mesma
        # razão de sempre: é o dado que permite cruzar lote + certificação
        # sem negociar disclosure nenhuma.
        "batchNumber": batch_number,
        # `legalStatus`: agora DERIVADO do resultado do SHACL regulatório
        # (ver _decidir_status), não mais lido de um literal do grafo.
        # Sempre visível pela mesma razão de sempre (decisão binária final
        # de comercialização).
        "legalStatus": legal_status,
    }

    # --- Disclosable (cada SDObj vira uma Disclosure própria) ---

    # SEM `certificateType`/`certificateNumber`: não há dado real (nem em
    # coop-a/coop-b) que corresponda a esses dois campos. Ausentes até M5.5
    # — ver docs/notas-arquitetura/certifier-claims-hardcoded-m4.md.

    # Parâmetros físico-químicos: agora vindos do lab (via federação), não
    # mais de fixture local. Cada um só entra se a query retornou valor
    # (mesma cautela de sempre contra claims disclosable com valor None).
    if row["moisture"] is not None:
        user_claims[SDObj("moistureContent")] = row["moisture"]
    if row["hmf"] is not None:
        user_claims[SDObj("hmfContent")] = row["hmf"]
    if row["acidity"] is not None:
        user_claims[SDObj("acidityContent")] = row["acidity"]
    if row["diastase"] is not None:
        user_claims[SDObj("diastaseActivity")] = row["diastase"]

    # SEM `floralOrigin`: mudou de dono para coop-a/coop-b (dado declarado
    # pelo produtor na colheita, não confirmado pelo laboratório) — ver
    # docstring do módulo.

    # Status de qualidade: agora DERIVADO (ver _decidir_status), não mais
    # lido de um literal do grafo. Disclosable, ao contrário de
    # `legalStatus` (sempre visível) — mesma distinção binário-vs-detalhe
    # de sempre.
    user_claims[SDObj("qualityStatus")] = quality_status

    # SEM `analysisActivity` nem `certificateReference`: sem modelagem
    # PROV-O da atividade de análise / sem certificado real gravado em
    # nenhum Fuseki, ficam ausentes até M5.5 (mesma decisão aplicada ao
    # `certificateReference` do coop-a).

    # --- 5. Monta e assina o SD-JWT ---
    issuer_key = get_issuer_key()

    sdjwt = SDJWTIssuer(
        user_claims=user_claims,
        issuer_key=issuer_key,
        holder_key=holder_key,
        sign_alg="EdDSA",
        extra_header_parameters={"typ": SD_JWT_TYP},
    )

    # --- 6. Ancoragem on-chain da VC de certificação (M9) — melhor
    # esforço, NUNCA reverte a emissão da VC (já montada/assinada acima)
    # nem quebra esta resposta HTTP: sucesso ou falha só mudam
    # `anchoringStatus`/campos associados, sempre presentes explicitamente
    # (mesma decisão do usuário já aplicada em coop-a/coop-b/lab, ver
    # tests/features/ancoragem_certificacao.feature).
    #
    # Ancora via o processo NOVO `honey-certification-document`/`issuance`
    # (action=CREATE_DOCUMENT=9), NÃO o antigo `honey-certification`
    # (action=CREATE_ASSET=0) — ver certifier/anchoring.py e
    # docs/referencia-contratos/README.md seção 4 para a justificativa
    # completa.
    #
    # `dataHashes` usa o keccak256 do SD-JWT COMPLETO já serializado
    # (`sdjwt.sd_jwt_issuance`) — o mesmo artefato final entregue ao holder
    # nesta mesma resposta, não um subconjunto de claims.
    #
    # OUTBOX: ANTES de ancorar, grava a `AnchoringTask` `pending` GUARDANDO o
    # hash (único momento em que ele existe: os salts das disclosures são
    # aleatórios), o batchNumber e o natureId. Se gravar falhar, a VC NÃO é
    # entregue (502): sem tarefa o hash seria irrecuperável e o 409 acima não
    # protegeria o lote de uma reemissão. Depois de ancorar, `anchored`; se
    # ancorar falhar, a tarefa fica `pending` e POST /anchoring/retry reenvia o
    # hash guardado.
    data_hash = Web3.keccak(text=sdjwt.sd_jwt_issuance)
    try:
        create_pending_task(batch_number, data_hash, nature_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("POST /credential: falha ao gravar AnchoringTask pending do lote %r: %s", batch_number, exc)
        return _oauth_error(
            502,
            "server_error",
            f"Não foi possível registrar a tarefa de ancoragem da VC (Fuseki): {exc}. Nenhuma VC foi entregue.",
        )

    anchoring_status = "anchoring_skipped"
    tx_hash = None
    anchoring_error = None
    try:
        success, tx_hash, anchoring_error = anchor_certification(
            batch_number, nature_id, data_hash
        )
        if success:
            anchoring_status = "anchored"
            _safe_set_task_status(batch_number, "anchored", tx_hash)
            logger.info(
                "POST /credential: VC de certificação do lote %r ancorada on-chain, txHash=%s",
                batch_number, tx_hash,
            )
        else:
            anchoring_status = "anchoring_failed"
            logger.error(
                "POST /credential: falha ao ancorar VC de certificação do lote %r on-chain: %s",
                batch_number, anchoring_error,
            )
    except Exception as exc:  # noqa: BLE001 - VC já emitida, nunca quebra a resposta HTTP
        anchoring_status = "anchoring_failed"
        anchoring_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "POST /credential: erro inesperado ao tentar ancorar VC de certificação do lote %r: %s",
            batch_number, anchoring_error,
        )

    response_body = {
        "credential": sdjwt.sd_jwt_issuance,
        "anchoringStatus": anchoring_status,
    }
    if tx_hash is not None:
        response_body["txHash"] = tx_hash
    if anchoring_error is not None:
        response_body["anchoringError"] = anchoring_error

    return response_body
