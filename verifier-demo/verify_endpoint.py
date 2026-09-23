"""
Endpoint de verificação agregada — M9.5 (primeira fatia).

Escopo: `GET /verify/{batch_number}`. Este é o primeiro passo do papel de
"auditor externo" do verifier-demo (ver CLAUDE.md): dado um número de lote,
descobrir em qual cooperativa ele foi produzido e se já existe um laudo de
certificação sobre ele, consultando os TRÊS Fuseki dos outros nós (coop-a,
coop-b, certifier) SEM nunca copiar/persistir esses dados no verifier-demo.

Atualização (pós-M9.5): a checagem ODRL de propósito (header
`X-Access-Purpose`, comparação de string local, sem motor ODRL) foi
adicionada — ver `_ODRL_POLICY_REFERENCE` e o início de `verify_batch`
abaixo. Também foi adicionada validação SHACL da FORMA da resposta agregada
(`VerifierResponseShape`, ver `ontology/verifier-response-shape.ttl` e
`validate_response_shape` abaixo) — roda depois da query federada e antes de
serializar a resposta; `conforms=False` vira 500 (dado incompleto vindo dos
outros nós, não erro do requisitante). Ainda NÃO fazem parte deste escopo
(deliberadamente fora daqui — vêm depois, em cima deste endpoint):
  - checagem de identidade do requisitante (assignee da política ODRL);
  - conferência do hash da credencial no AssetRegistry.sol (M11).
Não há nenhum stub/esboço dessas partes aqui — só este comentário como
lembrete de onde elas vão entrar.

--- SPARQL federado de verdade: uma query, SERVICE real ---

"SPARQL federado" no sentido da spec W3C (SPARQL 1.1 Federated Query) é o
uso da palavra-chave `SERVICE <url> { ... }` DENTRO de uma única query:
você manda a query para UM endpoint (o "nó de entrada"), e cada bloco
`SERVICE` daquele endpoint delega a execução daquele trecho específico
para um endpoint REMOTO, trazendo de volta só o resultado daquele bloco
para ser combinado (via UNION/JOIN por variável compartilhada) com o
resto da query. Isso é o que este módulo faz de verdade — uma ÚNICA
requisição HTTP do verifier-demo para o Fuseki do coop-a
(`COOP_A_SPARQL_URL`), com a query inteira (incluindo os blocos SERVICE)
resolvida do lado do Fuseki.

Por que isso é a peça que evita centralizar dado dos 3 nós num lugar só:
o verifier-demo nunca baixa/copia o grafo de coop-b ou certifier para
processar localmente — ele manda UMA pergunta, e é o Fuseki do coop-a
(atuando como coordenador da federação nesta consulta) quem busca, em
tempo real, o pedaço que falta em cada endpoint remoto e devolve só o
resultado já combinado. Nenhum dos três nós replica o dado dos outros
dois em nenhum momento; o verifier-demo não guarda nem uma cópia
temporária além da resposta HTTP desta requisição.

Investigação prévia (histórico, relevante para quem mexer aqui de novo):
uma tentativa inicial usando `SERVICE <http://localhost:3031/...>` FALHOU
com "Unexpected error making the query" do lado do Fuseki do coop-a. A
causa NÃO era limitação do motor SPARQL do Jena — era rede Docker: o
Fuseki do coop-a roda DENTRO de um container, e `localhost` visto de
dentro desse container aponta para o próprio container (que só escuta na
porta 3030), não para o host onde coop-b/certifier publicam as portas
3031/3032. Trocando para `http://host.docker.internal:PORTA/...`
(hostname especial que resolve para o IP do host a partir de dentro de um
container — funciona no Docker Desktop nativamente, e via
`extra_hosts: host.docker.internal:host-gateway` no docker-compose.yml
de cada nó no Docker Engine/Linux), a federação funciona perfeitamente.
Ver docs/notas-arquitetura/verifier-demo-service-federado.md para o
relato completo dessa investigação.

Por que coop-a é o "nó de entrada" fixo: é uma escolha arbitrária (não há
razão de domínio para ser coop-a e não coop-b) — só precisa ser
consistente. Efeito colateral aceito: esta consulta depende do Fuseki do
coop-a estar no ar mesmo para achar um lote que só existe em coop-b (ver
tratamento de erro 502 abaixo).
"""
import os

import requests
from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pyshacl import validate
from rdflib import BNode, Graph, Literal, Namespace, RDF, XSD

from config import COOP_A_SPARQL_URL, COOP_B_SPARQL_SERVICE_URL, CERTIFIER_SPARQL_SERVICE_URL

router = APIRouter()

# =============================================================================
#   Validação SHACL da resposta agregada — M9.5+
# =============================================================================
#
# `ontology/verifier-response-shape.ttl`: NÃO é o `ontology/validation-shapes.ttl`
# canônico do Ivan (regra de negócio de domínio sobre PRODUTO). É uma shape
# nova, mantida em arquivo separado, que valida a FORMA da resposta agregada
# que este módulo monta (batch + certification) — ver docstring daquele
# arquivo e docs/perguntas-para-ivan.md.
_AGT = Namespace("https://w3id.org/agrisemantics/AGRITRUST#")
_BEE = Namespace("https://w3id.org/agrisemantics/AGRITRUST/bee-extension#")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RESPONSE_SHAPES_PATH = os.path.join(
    _REPO_ROOT, "ontology", "verifier-response-shape.ttl"
)


def _build_response_graph(batch_number: str, result: dict) -> Graph:
    """Monta o grafo RDF agregado (batch + certification) em memória.

    Nunca é persistido em nenhum Fuseki — existe só pelo tempo desta
    requisição, para ser validado contra VerifierResponseShape antes de
    virar JSON. Ver ontology/verifier-response-shape.ttl para o desenho do
    nó-alvo esperado.
    """
    g = Graph()
    resp = BNode()
    g.add((resp, RDF.type, _BEE.VerifiedBatchResponse))
    g.add((resp, _AGT.batchNumber, Literal(batch_number, datatype=XSD.string)))

    product_type = result.get("batch", {}).get("productType")
    if product_type is not None:
        g.add((resp, _BEE.hasProductType, Literal(product_type, datatype=XSD.string)))

    certification = result.get("certification")
    if certification is not None:
        cert = BNode()
        g.add((resp, _BEE.hasVerifiedCertification, cert))
        legal_status = certification.get("legalStatus")
        quality_status = certification.get("qualityStatus")
        if legal_status is not None:
            g.add((cert, _AGT.legalStatus, Literal(legal_status, datatype=XSD.string)))
        if quality_status is not None:
            g.add(
                (cert, _AGT.qualityStatus, Literal(quality_status, datatype=XSD.string))
            )

    return g


def validate_response_shape(batch_number: str, result: dict):
    """Valida a resposta agregada contra VerifierResponseShape (pyshacl).

    Retorna (conforms, results_text). Não é regra de negócio (já coberta em
    M1.5, na ENTRADA de cada nó) — é contrato de FORMA: se os dados vindos
    de coop-a/coop-b/certifier não bastam para montar uma resposta com os
    campos mínimos esperados, é erro do sistema (dados dos outros nós não
    batem com o contrato), não do requisitante.
    """
    data_graph = _build_response_graph(batch_number, result)

    shapes_graph = Graph()
    shapes_graph.parse(_RESPONSE_SHAPES_PATH, format="turtle")

    conforms, _results_graph, results_text = validate(
        data_graph,
        shacl_graph=shapes_graph,
        inference="none",
    )
    return conforms, results_text

# =============================================================================
#   Checagem ODRL de propósito (Python local) — M9.5+, sobre o endpoint acima
# =============================================================================
#
# Isto NÃO é um motor ODRL: nenhuma lib processa o JSON-LD abaixo em tempo de
# execução. `_ODRL_POLICY_REFERENCE` é só DOCUMENTAÇÃO — a representação em
# JSON-LD da regra de negócio que o `if` em `verify_batch` implementa "na
# unha" (comparação direta de string). Ela espelha a política ODRL REAL já
# existente na ontologia canônica (`ontology/instances.ttl`, linhas ~568-594,
# `:Politica_Acesso_EUDR_Silvestre`): um `odrl:Set` com uma `odrl:permission`
# de `odrl:action odrl:read`, restrita por um `odrl:constraint` de
# `odrl:leftOperand odrl:purpose` / `odrl:operator odrl:eq` /
# `odrl:rightOperand` igual ao propósito exigido.
#
# *** CORRECAO POS-ENTREGA - fonte de verdade e o repo, nao mensagem avulsa ***
# A primeira versao desta checagem usava o literal "EUDR_verification",
# vindo de uma mensagem avulsa (chat/WhatsApp) do Ivan sobre uma
# "Policy_001" - texto que NUNCA foi commitado neste repositorio. Isso foi
# um engano do assistente (Claude) que implementou a checagem sem antes
# conferir a fonte real e versionada: ontology/instances.ttl, linhas
# ~568-594, ja define a politica ODRL de verdade para este proposito -
# :Politica_Acesso_EUDR_Silvestre, com odrl:rightOperand "Compliance
# EUDR"@en. Corrigido para usar ESSE literal. Licao registrada: dado
# versionado no repo (.ttl, codigo, CHANGELOG) e sempre a fonte de
# verdade sobre exemplos soltos de conversa que nao viraram parte dele,
# mesmo vindo do co-autor da ontologia - se uma mensagem e o repo
# divergem, checar o repo primeiro, nao assumir que a mensagem esta
# atualizada.
_ODRL_POLICY_REFERENCE = """
{
  "@context": {
    "odrl": "http://www.w3.org/ns/odrl/2/"
  },
  "@type": "odrl:Set",
  "@id": "_:Politica_Acesso_Verify_Endpoint",
  "odrl:permission": [
    {
      "@type": "odrl:Permission",
      "odrl:action": { "@id": "odrl:read" },
      "odrl:target": { "@id": "_:VerifyEndpointResponse" },
      "odrl:constraint": [
        {
          "@type": "odrl:Constraint",
          "odrl:leftOperand": { "@id": "odrl:purpose" },
          "odrl:operator": { "@id": "odrl:eq" },
          "odrl:rightOperand": "Compliance EUDR"
        }
      ]
    }
  ]
}
"""

# Proposito unico aceito por esta checagem - mesmo literal de
# :Politica_Acesso_EUDR_Silvestre em ontology/instances.ttl (ver nota de
# correcao acima).
_REQUIRED_PURPOSE = "Compliance EUDR"

# Mesmos prefixos/URIs usados em coop-a/coop-b/certifier — ver docstrings
# daqueles credential_endpoint.py para a fonte (.ttl) de cada termo.
_SPARQL_PREFIXES = """
PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
"""

# Query federada única: um UNION de dois ramos pra achar o lote (local no
# coop-a, OU via SERVICE no coop-b — nunca os dois, um lote existe em só
# uma cooperativa), mais um bloco OPTIONAL+SERVICE pra certificação no
# certifier, que roda independente de QUAL ramo do UNION casou.
#
# Por que os OPTIONAL de produtor (rdfs:label/foaf:name/hasDID) estão
# DUPLICADOS dentro de cada ramo do UNION, em vez de um bloco só depois:
# a variável ?producer, quando vem do ramo SERVICE (coop-b), é uma URI do
# grafo de coop-b — um OPTIONAL fora do SERVICE rodaria contra o grafo
# LOCAL do coop-a, que não tem aquela URI, e o campo ficaria vazio à toa.
# Cada ramo precisa resolver os dados do SEU produtor no SEU próprio
# endpoint (local ou via SERVICE).
#
# `a ?productType` / `agritrust:ownedBy ?producer` não são OPTIONAL, nem
# no ramo local nem no SERVICE, pela mesma razão de coop-a/M4: um lote sem
# tipo ou sem produtor dono é dado de domínio incompleto — preferimos que
# aquele ramo do UNION simplesmente não contribua nenhuma linha a deixar
# passar um resultado parcial silencioso.
#
# O bloco de certificação é OPTIONAL (não UNION) porque não é uma segunda
# FONTE alternativa do mesmo fato — é um dado ADICIONAL que pode ou não
# existir ainda, independente de onde o lote foi encontrado.
# `agritrust:legalStatus` dentro do SERVICE não é OPTIONAL (é o claim
# "sempre visível" da credencial de certificação, mesmo critério do M4 do
# certifier); `qualityStatus` é OPTIONAL — mesma simplificação de campos
# documentada abaixo, mesmo raciocínio de "auditor de primeira camada" já
# usado no M4 do certifier.
#
# `floralOrigin` NÃO é buscado aqui dentro do SERVICE do certifier: mudou
# de dono para o produtor (mesma decisão de coop-a/credential_endpoint.py —
# é dado declarado na colheita, o laboratório nunca confirma isso), então
# migrou para dentro de CADA ramo do UNION abaixo (`:hasFloralOrigin` do
# recurso do LOTE, não do laudo).
_FEDERATED_QUERY_TEMPLATE = _SPARQL_PREFIXES + """
SELECT ?sourceNode ?productType ?producerLabel ?producerFoafName ?producerDID
       ?floralOrigin ?legalStatus ?qualityStatus
WHERE {
  {
    ?batch agritrust:batchNumber %(batch_number)s ;
           a ?productType ;
           agritrust:ownedBy ?producer .
    OPTIONAL { ?batch :hasFloralOrigin ?floralOrigin }
    OPTIONAL { ?producer rdfs:label ?producerLabel }
    OPTIONAL { ?producer foaf:name ?producerFoafName }
    OPTIONAL { ?producer agritrust:hasDID ?producerDID }
    BIND("coop-a" AS ?sourceNode)
  }
  UNION
  {
    SERVICE <%(coop_b_service_url)s> {
      ?batch agritrust:batchNumber %(batch_number)s ;
             a ?productType ;
             agritrust:ownedBy ?producer .
      OPTIONAL { ?batch :hasFloralOrigin ?floralOrigin }
      OPTIONAL { ?producer rdfs:label ?producerLabel }
      OPTIONAL { ?producer foaf:name ?producerFoafName }
      OPTIONAL { ?producer agritrust:hasDID ?producerDID }
    }
    BIND("coop-b" AS ?sourceNode)
  }
  OPTIONAL {
    SERVICE <%(certifier_service_url)s> {
      ?labResult agritrust:batchNumber %(batch_number)s ;
                 agritrust:legalStatus ?legalStatus ;
                 a ?anyType .
      OPTIONAL { ?labResult agritrust:qualityStatus ?qualityStatus }
    }
  }
}
LIMIT 1
"""


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Mesmo nível de escape simples usado em coop-a/coop-b/certifier — não é
    um escapador SPARQL genérico e completo (ver docs/known-issues.md).
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _val(row: dict, key: str) -> str | None:
    cell = row.get(key)
    return cell["value"] if cell else None


def query_federated(batch_number: str) -> dict | None:
    """Roda a query federada única contra o Fuseki de ENTRADA (coop-a).

    Uma só requisição HTTP — a federação (SERVICE) acontece do lado do
    Fuseki do coop-a, não aqui. Retorna None se o lote não foi encontrado
    em nenhum ramo do UNION (0 bindings) — o chamador decide o HTTP status.
    """
    query = _FEDERATED_QUERY_TEMPLATE % {
        "batch_number": _sparql_literal(batch_number),
        "coop_b_service_url": COOP_B_SPARQL_SERVICE_URL,
        "certifier_service_url": CERTIFIER_SPARQL_SERVICE_URL,
    }

    response = requests.post(
        COOP_A_SPARQL_URL,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=20,
    )
    response.raise_for_status()

    bindings = response.json().get("results", {}).get("bindings", [])
    if not bindings:
        return None

    row = bindings[0]
    product_type_uri = _val(row, "productType")
    product_type = (
        product_type_uri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
        if product_type_uri
        else None
    )
    producer_name = _val(row, "producerFoafName") or _val(row, "producerLabel")

    legal_status = _val(row, "legalStatus")
    certification = (
        {
            "legalStatus": legal_status,
            "qualityStatus": _val(row, "qualityStatus"),
        }
        if legal_status is not None
        else None
    )

    return {
        "sourceNode": _val(row, "sourceNode"),
        "batch": {
            "productType": product_type,
            "producerName": producer_name,
            "producerDID": _val(row, "producerDID"),
            # floralOrigin mudou de "certification" para "batch": é dado
            # declarado pelo produtor na colheita (:hasFloralOrigin do
            # lote, em coop-a/coop-b), não confirmado pelo laboratório —
            # ver docstring da query acima.
            "floralOrigin": _val(row, "floralOrigin"),
        },
        # None explícito (não campo ausente) quando não há laudo ainda —
        # distinção deliberada entre "não certificado" e "erro". Usamos
        # `legalStatus` como sentinela de "achou certificação" porque é o
        # único campo não-OPTIONAL dentro do SERVICE do certifier — se ele
        # veio vazio, o bloco OPTIONAL inteiro não casou.
        "certification": certification,
    }


@router.get("/verify/{batch_number}")
def verify_batch(batch_number: str, x_access_purpose: str | None = Header(None)):
    """Agrega dado de lote (coop-a OU coop-b) + certificação (certifier).

    Uma única query SPARQL federada (ver docstring do módulo) resolve os
    três nós numa só chamada HTTP. Se o lote não foi achado em NENHUMA
    cooperativa -> 404. Falha de rede/timeout contra o nó de entrada
    (coop-a) -> 502 (mesmo padrão que M4 de coop-a já usa para "Fuseki
    fora do ar") — sem robustez adicional (retry, timeout diferenciado por
    sub-consulta SERVICE, etc.), conforme escopo desta entrega. Antes de
    serializar a resposta, valida sua FORMA contra `VerifierResponseShape`
    (ver `validate_response_shape`) -> `conforms=False` vira 500.

    Antes de qualquer coisa (inclusive antes de gastar uma consulta SPARQL
    federada cara), checa o header `X-Access-Purpose` contra a política
    ODRL de propósito documentada em `_ODRL_POLICY_REFERENCE` acima —
    checagem "na unha" em Python, não um motor ODRL. Valor exigido:
    `"Compliance EUDR"`, o mesmo literal de `:Politica_Acesso_EUDR_Silvestre`
    em `ontology/instances.ttl` (ver nota de correção acima sobre por que
    não é `"EUDR_verification"`).

    Por que HEADER (`X-Access-Purpose`) e não query string (`?purpose=`):
      (a) o propósito de acesso é um metadado de COMO a requisição é feita,
          não do QUE está sendo pedido — o recurso continua sendo
          `/verify/{batch_number}`; o prefixo `X-` é a convenção usual pra
          esse tipo de metadado de requisição que não faz parte da
          identidade do recurso;
      (b) query params tendem a vazar mais facilmente em logs de proxy,
          histórico de URL do navegador, etc. — aqui isso não é uma defesa
          forte de segurança de verdade (não há TLS nem autenticação real
          ainda), mas é a prática mais cuidadosa mesmo assim;
      (c) é o mesmo lugar (header) onde viveria um `Authorization: Bearer
          ...` — o que se conecta ao próximo parágrafo.

    O que esta checagem NÃO faz (e por quê isso é intencional, não um
    esquecimento): hoje `X-Access-Purpose` é um valor que QUALQUER cliente
    pode declarar livremente — não há verificação de QUEM está fazendo a
    requisição, só do propósito que ela AFIRMA ter. Isso cobre apenas a
    metade "purpose" da política ODRL real (`:Politica_Acesso_EUDR_Silvestre`
    em `ontology/instances.ttl`), não a metade "assignee": aquela política
    amarra a permissão a `odrl:assignee :Importador_Alemao_Mel`, uma
    identidade específica — não "qualquer requisitante que declare o
    propósito certo". Numa versão futura com autenticação real do
    requisitante (ex.: apresentação de uma credencial verificável dele
    mesmo via OID4VP, ou um token OAuth com claims de identidade), o
    propósito declarado precisaria ser cruzado com uma política que de fato
    autoriza AQUELE assignee para AQUELE propósito. Essa checagem de
    identidade/assignee está deliberadamente fora do escopo desta entrega —
    só é documentada aqui como o próximo passo.
    """
    if x_access_purpose is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "error_description": (
                    "Header 'X-Access-Purpose' é obrigatório — declare o "
                    "propósito de acesso a este endpoint (checagem ODRL de "
                    "propósito, ver docstring de verify_batch)."
                ),
            },
        )

    if x_access_purpose != _REQUIRED_PURPOSE:
        return JSONResponse(
            status_code=403,
            content={
                "error": "access_denied",
                "error_description": (
                    f"Propósito de acesso {x_access_purpose!r} não autorizado "
                    f"para este endpoint. Propósito aceito: {_REQUIRED_PURPOSE!r}."
                ),
            },
        )

    try:
        result = query_federated(batch_number)
    except requests.RequestException as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": "server_error",
                "error_description": (
                    f"Falha ao consultar a federação SPARQL (nó de entrada "
                    f"coop-a): {exc}"
                ),
            },
        )

    if result is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "error_description": (
                    f"Nenhum lote com agritrust:batchNumber = {batch_number!r} "
                    "encontrado em coop-a nem coop-b."
                ),
            },
        )

    # Validação SHACL da FORMA da resposta agregada (VerifierResponseShape,
    # ver ontology/verifier-response-shape.ttl) — não é a query SPARQL
    # falhando nem o requisitante errando algo: é o resultado combinado dos
    # outros nós não batendo com o contrato mínimo esperado (ex.: productType
    # ausente porque a instância em coop-a/coop-b está incompleta). Por isso
    # 500, não 400/404.
    conforms, results_text = validate_response_shape(batch_number, result)
    if not conforms:
        return JSONResponse(
            status_code=500,
            content={
                "error": "server_error",
                "error_description": (
                    "Resposta agregada não passou na validação SHACL "
                    "(VerifierResponseShape) — dado incompleto vindo de "
                    "coop-a/coop-b/certifier."
                ),
                "shacl_violations": results_text,
            },
        )

    return {"batchNumber": batch_number, **result}
