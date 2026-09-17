"""
Endpoint de verificação agregada — M9.5 (primeira fatia).

Escopo: `GET /verify/{batch_number}`. Este é o primeiro passo do papel de
"auditor externo" do verifier-demo (ver CLAUDE.md): dado um número de lote,
descobrir em qual cooperativa ele foi produzido e se já existe um laudo de
certificação sobre ele, consultando os TRÊS Fuseki dos outros nós (coop-a,
coop-b, certifier) SEM nunca copiar/persistir esses dados no verifier-demo.

NÃO faz parte deste escopo (ver instruções da tarefa, deliberadamente fora
daqui — vem depois, em cima deste endpoint):
  - checagem ODRL de propósito;
  - validação SHACL da resposta agregada;
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
import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import COOP_A_SPARQL_URL, COOP_B_SPARQL_SERVICE_URL, CERTIFIER_SPARQL_SERVICE_URL

router = APIRouter()

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
# certifier); `qualityStatus`/`floralOrigin` são OPTIONAL — mesma
# simplificação de campos documentada abaixo, mesmo raciocínio de "auditor
# de primeira camada" já usado no M4 do certifier.
_FEDERATED_QUERY_TEMPLATE = _SPARQL_PREFIXES + """
SELECT ?sourceNode ?productType ?producerLabel ?producerFoafName ?producerDID
       ?legalStatus ?qualityStatus ?floralOrigin
WHERE {
  {
    ?batch agritrust:batchNumber %(batch_number)s ;
           a ?productType ;
           agritrust:ownedBy ?producer .
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
      OPTIONAL { ?labResult :hasFloralOrigin ?floralOrigin }
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
            "floralOrigin": _val(row, "floralOrigin"),
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
        },
        # None explícito (não campo ausente) quando não há laudo ainda —
        # distinção deliberada entre "não certificado" e "erro". Usamos
        # `legalStatus` como sentinela de "achou certificação" porque é o
        # único campo não-OPTIONAL dentro do SERVICE do certifier — se ele
        # veio vazio, o bloco OPTIONAL inteiro não casou.
        "certification": certification,
    }


@router.get("/verify/{batch_number}")
def verify_batch(batch_number: str):
    """Agrega dado de lote (coop-a OU coop-b) + certificação (certifier).

    Uma única query SPARQL federada (ver docstring do módulo) resolve os
    três nós numa só chamada HTTP. Se o lote não foi achado em NENHUMA
    cooperativa -> 404. Falha de rede/timeout contra o nó de entrada
    (coop-a) -> 502 (mesmo padrão que M4 de coop-a já usa para "Fuseki
    fora do ar") — sem robustez adicional (retry, timeout diferenciado por
    sub-consulta SERVICE, etc.), conforme escopo desta entrega.
    """
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

    return {"batchNumber": batch_number, **result}
