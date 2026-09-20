"""
Credential endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M3.

Escopo: `POST /credential`. Este é o passo final do fluxo pre-authorized_code
começado em M1 (metadata) → M2 (`/token`) → M2.5 (`/nonce`): o holder já tem
um `access_token` (M2) e um `c_nonce` fresco (M2.5); aqui ele envia os dois —
o access_token no header `Authorization`, o c_nonce embutido dentro de um JWT
de key proof assinado pela própria chave dele — e recebe de volta um SD-JWT
VC (`HoneyBatchCredential`, conforme declarado no metadata de M1).

Duas notas de confiança sobre a spec, deliberadamente explícitas em vez de
assumidas (a spec OID4VCI passou por várias mudanças estruturais entre
drafts, incluindo neste próprio projeto — ver nonce_endpoint.py sobre o
Nonce Endpoint dedicado ter substituído o c_nonce embutido em respostas
anteriores):

1. Corpo do Credential Request: uso `credential_configuration_id` (chave que
   bate com `credential_configurations_supported` no metadata de M1, aqui
   `"HoneyBatchCredential"`) — este é o campo que tenho mais confiança de
   estar correto na versão atual/final da spec para amarrar o request a uma
   configuração declarada. Não uso `format`+`vct` soltos no corpo do request
   (esquema de drafts mais antigos) nem `credential_identifier` (variante
   usada quando o token response já devolveu uma lista pré-autorizada de
   identifiers via `authorization_details` — não é o caso aqui, M2 não emite
   isso). Estou razoavelmente confiante desta escolha, mas não 100% seguro
   de que não haja mais nenhum campo obrigatório de nível superior no
   request que eu esteja deixando de checar.
2. Corpo de sucesso da resposta: `{"credential": "<SD-JWT compact>"}`. Sei
   que a spec previu em algum momento (dependendo do draft) campos
   adicionais como `c_nonce`/`c_nonce_expires_in` embutidos aqui — mas isso
   pertence ao esquema ANTERIOR ao Nonce Endpoint dedicado, que este projeto
   já adotou em M2.5 (novo c_nonce sempre vem de POST /nonce, não da
   resposta do credential endpoint). Também sei que existe uma variante de
   resposta em lista (`"credentials": [...]`) usada para emissão em lote —
   não implementada aqui porque o request só pede uma credencial por vez.
   `notification_id` (para o Notification Endpoint, que informa o issuer se
   o holder aceitou/rejeitou a credencial) não é emitido porque este projeto
   não implementa esse endpoint — omitir o campo é o comportamento correto
   quando o issuer não o suporta (a spec o define como opcional).

Fluxo de dados fixos vs. dinâmicos: em M3, os claims da credencial (lote de
mel, laboratório, produtor) eram HARDCODED, inspirados em
coop-a/data/sample-instance.ttl mas sem query real ao Fuseki. Este é o M4:
a Parte 4 (montagem dos claims) agora roda uma consulta SPARQL SELECT real
contra o dataset "coop-a" do Fuseki (já populado pelo pipeline SHACL de
M1.5/pipeline.py) — o resto do arquivo (autenticação, proof, c_nonce)
permanece idêntico ao M3.
"""
import re
import time

import jwt as pyjwt
import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jwcrypto.jwk import JWK
from sd_jwt.common import SDObj
from sd_jwt.issuer import SDJWTIssuer

from config import (
    CREDENTIAL_VALIDITY_SECONDS,
    DEFAULT_BATCH_NUMBER,
    DID_WEB,
    FUSEKI_ADMIN_PASSWORD,
    FUSEKI_ADMIN_USER,
    FUSEKI_URL,
)
from keys import get_issuer_key
from nonce_endpoint import is_valid_c_nonce
from token_endpoint import is_valid_access_token

router = APIRouter()

# Deve bater com a chave usada em
# main.py:credential_configurations_supported (M1). Duplicado aqui como
# literal em vez de importado de main.py para evitar import circular
# (main.py importa este módulo para registrar o router).
CREDENTIAL_CONFIGURATION_ID = "HoneyBatchCredential"

VCT = "https://agritrust.example/vct/honey-batch"

# Header `typ` do JWS — `dc+sd-jwt`, não `vc+sd-jwt`. Ver
# docs/notas-arquitetura/sd-jwt-vc-media-type.md: a spec SD-JWT VC mudou o
# media type de `vc+sd-jwt` para `dc+sd-jwt` em nov/2024 (conflito de nome
# com o media type já registrado pelo W3C VC Data Model).
SD_JWT_TYP = "dc+sd-jwt"

# `typ` esperado no header do JWT de key proof enviado pelo holder,
# conforme OID4VCI ("JWT Proof Type"). Não é verificado estritamente aqui
# (ver simplificação de proof abaixo), só documentado.
PROOF_JWT_TYP = "openid4vci-proof+jwt"


# --- M4: query SPARQL contra o Fuseki do coop-a ---
#
# Namespaces usados na instância (coop-a/data/sample-instance.ttl) — os
# MESMOS prefixos/URIs declarados no cabeçalho daquele .ttl. Repetidos aqui
# como literais porque esta query é montada em Python (f-string), não lida
# de um arquivo .rq separado — não há um arquivo de queries neste projeto
# ainda, e criar um só para esta única query seria escopo extra não pedido.
_SPARQL_PREFIXES = """
PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
"""

# Query SELECT que busca, a partir de um agritrust:batchNumber, tudo que a
# Parte 4 precisa para montar os claims da credencial. Comentários por
# campo (POR QUÊ cada um está aqui, não só o quê):
#
# - ?batch / ?productType: `?batch a ?productType` NÃO é OPTIONAL nem tem
#   FILTER adicional porque, na instância atual, o recurso do lote
#   (:Lote_Mel_Silvestre_2026_042) só tem UM rdf:type assertado (:HoneyApis)
#   — não há ambiguidade de "qual tipo pegar" a resolver aqui. Se um lote
#   viesse a ter múltiplos rdf:type (ex.: uma superclasse OWL inferida),
#   esta query pegaria qualquer um deles arbitrariamente — não é um
#   problema hoje porque `inference="none"` no pipeline SHACL (M1.5) não
#   materializa hierarquia de classes na instância.
# - ?moisture / ?hmf / ?acidity: cada um em seu próprio OPTIONAL porque nem
#   todo lote necessariamente tem os três parâmetros laboratoriais
#   preenchidos — um lote incompleto não deve fazer a query inteira falhar
#   em retornar zero linhas. (hasDiastaseActivity não é buscado aqui: não é
#   um claim que M3 definiu para a credencial de lote — buscar um campo que
#   nunca vira claim seria código morto.)
# - ?producer: obrigatório (não OPTIONAL) via agritrust:ownedBy, porque um
#   lote sem produtor dono seria um dado de domínio incompleto — se isso
#   acontecer, preferimos que a query não devolva parte dos claims de forma
#   silenciosa (ownedBy ausente já teria sido barrado por SHACL/minCount se
#   a shape exigir, mas a query aqui não assume isso).
# - ?producerLabel / ?producerFoafName: dois OPTIONAL separados (não um
#   único com COALESCE dentro do WHERE) porque rdfs:label e foaf:name são
#   propriedades semanticamente diferentes que por acaso convergem no mesmo
#   valor nesta instância — o COALESCE fica em Python (mais legível que
#   aninhar BIND/COALESCE em SPARQL para uma prioridade simples).
# - ?producerDID: agritrust:hasDID do produtor — chave para o claim
#   `producerDID` (M3 já decidiu esse claim como disclosable).
# - ?geoWKT / ?geoLabel: todo o bloco de geolocalização é UM OPTIONAL
#   (aninhado, não três OPTIONAL soltos) porque os três triplos
#   (hasOperatingLocation -> hasGeometry -> asWKT) são uma cadeia: só faz
#   sentido buscar o rótulo do geo:Feature SE a cadeia geométrica inteira
#   existir. Um produtor sem localização cadastrada não deve gerar linhas
#   "meio nulas" a mais na tabela de resultado.
_CLAIMS_QUERY_TEMPLATE = _SPARQL_PREFIXES + """
SELECT ?productType ?moisture ?hmf ?acidity
       ?producerLabel ?producerFoafName ?producerDID ?geoWKT ?geoLabel
WHERE {
  ?batch agritrust:batchNumber %(batch_number)s ;
         a ?productType ;
         agritrust:ownedBy ?producer .
  OPTIONAL { ?batch :hasMoistureContent ?moisture }
  OPTIONAL { ?batch :hasHMFContent ?hmf }
  OPTIONAL { ?batch :hasAcidity ?acidity }
  OPTIONAL { ?producer rdfs:label ?producerLabel }
  OPTIONAL { ?producer foaf:name ?producerFoafName }
  OPTIONAL { ?producer agritrust:hasDID ?producerDID }
  OPTIONAL {
    ?producer agritrust:hasOperatingLocation ?geoFeature .
    ?geoFeature geo:hasGeometry ?geom .
    ?geom geo:asWKT ?geoWKT .
    OPTIONAL { ?geoFeature rdfs:label ?geoLabel }
  }
}
LIMIT 1
"""

# `POINT(lon lat)` — único formato de geometria usado neste projeto (ver
# CLAUDE.md/instances.ttl: sempre geo:Point via geo:asWKT). Regex simples em
# vez de uma lib GIS (ex.: shapely) de propósito: shapely existe para
# operações geométricas (interseção, buffer, reprojeção etc.) que este
# projeto nunca faz — aqui só extraímos dois números de um literal textual
# com um formato fixo e conhecido. Trazer uma dependência geoespacial só
# para isso seria peso morto.
_WKT_POINT_RE = re.compile(r"POINT\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Suficiente para este caso de uso (batch_number vindo do corpo da
    requisição, sem aspas nem quebras de linha esperadas) — não é um
    escapador SPARQL genérico e completo.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _parse_wkt_point(wkt: str) -> tuple[float, float]:
    """Extrai (lat, lon) de um literal `POINT(lon lat)` (ordem WKT: X=lon, Y=lat)."""
    match = _WKT_POINT_RE.search(wkt)
    if not match:
        raise ValueError(f"Literal WKT não reconhecido como POINT(lon lat): {wkt!r}")
    lon_str, lat_str = match.groups()
    return float(lat_str), float(lon_str)


def query_batch_claims(batch_number: str) -> dict | None:
    """Consulta o Fuseki do coop-a (SPARQL SELECT) pelos dados do lote.

    Retorna um dict com os campos brutos da primeira linha de resultado, ou
    None se o batch_number não existir no grafo (query sem resultados) —
    o chamador decide o que fazer (404), esta função não decide HTTP status.

    Autenticação/URL: mesma convenção de pipeline.py (Basic Auth com
    FUSEKI_ADMIN_USER/FUSEKI_ADMIN_PASSWORD contra {FUSEKI_URL}), só que
    aqui é GET/POST em /sparql (leitura) em vez de /update (escrita).
    """
    query = _CLAIMS_QUERY_TEMPLATE % {"batch_number": _sparql_literal(batch_number)}

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

    return {
        "productType": _val("productType"),
        "moisture": _val("moisture"),
        "hmf": _val("hmf"),
        "acidity": _val("acidity"),
        "producerLabel": _val("producerLabel"),
        "producerFoafName": _val("producerFoafName"),
        "producerDID": _val("producerDID"),
        "geoWKT": _val("geoWKT"),
        "geoLabel": _val("geoLabel"),
    }


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
    assinatura contra uma PKI real de holder.

    Simplificação deliberada de MVP (não há ainda resolução de did:key do
    holder nem verificação de assinatura Ed25519 correspondente — isso é
    parte do trabalho de M5, quando did:key/did:web ganham resolução real
    neste projeto): aceitamos qualquer JWT sintaticamente válido (3
    segmentos, JSON decodificável) como prova de posse, e usamos o `jwk` do
    header (se presente — é o parâmetro de header padrão da spec para o
    holder anunciar a chave pública que quer amarrar à credencial) só para
    popular o claim de key binding `cnf` da credencial emitida, não para
    autenticar a origem do request agora. O que efetivamente barra
    replay/forjamento neste milestone é o c_nonce de uso único (ver
    `is_valid_c_nonce`), não a assinatura do proof em si.

    Isso é uma simplificação real de segurança, não apenas de spec: em uma
    implementação de produção completa, o issuer verificaria a assinatura
    do proof JWT contra a chave pública do próprio `jwk`/`kid` do header
    (prova de posse de fato) antes de confiar nela. Documentado aqui em vez
    de fingir que essa verificação existe.
    """
    header = pyjwt.get_unverified_header(jwt_str)
    payload = pyjwt.decode(jwt_str, options={"verify_signature": False})
    return header, payload


@router.post("/credential")
async def credential_endpoint(request: Request):
    # --- 1. Autenticação via access_token (RFC 6750 Bearer Token) ---
    access_token = _extract_bearer_token(request.headers.get("authorization"))
    if access_token is None:
        # RFC 6750 §3.1: quando a requisição não inclui autenticação (token
        # ausente/malformado), a recomendação é responder 401 SEM o
        # parâmetro `error` no header WWW-Authenticate (evitar vazar
        # informação de diagnóstico a um cliente não autenticado).
        return _oauth_error(
            401,
            "invalid_token",
            "Header Authorization ausente ou não é um Bearer token.",
            www_authenticate="Bearer",
        )
    if not is_valid_access_token(access_token):
        # RFC 6750 §3.1: token presente mas inválido/expirado → 401 com
        # error="invalid_token" no WWW-Authenticate.
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

    # `batch_number`: campo OPCIONAL, EXTENSÃO PRÓPRIA DESTE MVP, NÃO faz
    # parte da spec OID4VCI (o Credential Request da spec não tem noção de
    # "escolher um lote" — isso normalmente seria resolvido antes, no fluxo
    # de Credential Offer, que amarraria a oferta a um `credential_identifier`
    # específico já pré-associado no lado do issuer; este repo ainda não
    # implementa Credential Offer). Aqui aceitamos esse campo extra ao lado
    # de `credential_configuration_id` só para ter algum jeito de escolher
    # QUAL lote consultar no Fuseki; se ausente, cai no
    # DEFAULT_BATCH_NUMBER (mesmo valor literal que M3 tinha hardcoded).
    batch_number = body.get("batch_number") or DEFAULT_BATCH_NUMBER

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
    # binding na credencial emitida — ver ressalva de segurança na docstring
    # de `_decode_proof_jwt`.
    holder_jwk_dict = proof_header.get("jwk")
    holder_key = JWK(**holder_jwk_dict) if holder_jwk_dict else None

    # --- 4. Monta os claims da credencial (M4: query SPARQL real ao Fuseki) ---
    try:
        row = query_batch_claims(batch_number)
    except requests.RequestException as exc:
        # Falha de infraestrutura (Fuseki fora do ar, timeout, credencial
        # de admin errada etc.) — não é o mesmo caso de "lote não existe".
        # 502 porque o coop-a (issuer) depende de um serviço upstream
        # (Fuseki) que falhou ao responder.
        return _oauth_error(502, "server_error", f"Falha ao consultar o Fuseki: {exc}")

    if row is None:
        # Lote pedido não existe no grafo do coop-a — não emitimos uma
        # credencial vazia/quebrada, retornamos 404. Não é um erro OAuth
        # (não está na lista de `error` de RFC 6749/OID4VCI), mas o mesmo
        # formato de corpo JSON (`_oauth_error`) é reaproveitado por
        # consistência com o resto deste arquivo.
        return _oauth_error(
            404,
            "invalid_request",
            f"Nenhum lote encontrado no Fuseki (dataset coop-a) com "
            f"agritrust:batchNumber = {batch_number!r}.",
        )

    # `productType`: pega o NOME LOCAL (fragmento após '#') da URI de
    # rdf:type do lote — ex. da URI
    # https://w3id.org/agrisemantics/AGRITRUST/bee-extension#HoneyApis
    # extrai a string "HoneyApis". DECISÃO CONSCIENTE (não é bug nem
    # regressão acidental de M3): M3 usava o rótulo humano "Apis mellifera"
    # (hardcoded), mas esse rótulo vem de rdfs:label em bee-extension.ttl,
    # que NÃO é carregado no Fuseki (só a instância de dados é inserida lá,
    # ver pipeline.py) — não há como obter "Apis mellifera" via SPARQL
    # contra este dataset hoje. Em vez de reintroduzir esse rótulo hardcoded
    # (o que reverteria o ganho de M4 de vir tudo de dado real), usamos o
    # nome local da classe, que É recuperável via SPARQL puro.
    product_type = row["productType"].rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    # producerName: prioriza foaf:name sobre rdfs:label quando ambos
    # existem (foaf:name é uma propriedade semanticamente mais específica
    # para "nome de pessoa"; rdfs:label é um rótulo genérico que, nesta
    # instância, por acaso tem o mesmo valor). Fallback para rdfs:label se
    # foaf:name não estiver presente.
    producer_name = row["producerFoafName"] or row["producerLabel"]

    # Geolocalização: parse manual do WKT (`POINT(lon lat)`) — ver
    # `_parse_wkt_point` acima para a justificativa de não usar lib GIS.
    # Todo o bloco de geo é OPTIONAL na query (ver comentários da query),
    # então `geoWKT` pode vir None se o produtor não tiver localização
    # cadastrada — nesse caso, omitimos o claim inteiro em vez de inventar
    # coordenadas.
    apiary_location = None
    if row["geoWKT"]:
        lat, lon = _parse_wkt_point(row["geoWKT"])
        apiary_location = {
            "lat": lat,
            "lon": lon,
            "description": row["geoLabel"] or "",
        }

    now = int(time.time())
    exp = now + CREDENTIAL_VALIDITY_SECONDS

    user_claims = {
        # --- Sempre visíveis (fora de _sd) ---
        # `vct` identifica o tipo de credencial (Verifiable Credential Type),
        # claim obrigatório e estrutural do SD-JWT VC — nunca disclosable,
        # porque o próprio protocolo de verificação precisa dele em claro
        # para saber COMO interpretar o resto do JWT antes de qualquer
        # disclosure ser processada.
        "vct": VCT,
        # `iss` identifica o emissor — precisa estar em claro para o
        # verificador poder resolver a chave pública (did:web) e checar a
        # assinatura do JWS. Escondê-lo tornaria a própria verificação da
        # assinatura impossível sem já saber quem assinou.
        "iss": DID_WEB,
        "iat": now,
        "exp": exp,
        # `batchNumber` e `productType` (espécie) ficam em claro de
        # propósito: são o mínimo que QUALQUER apresentação desta credencial
        # precisa expor para ser útil (ex.: rastreabilidade pública de lote,
        # ou um scanner de prateleira lendo só "isto é mel de Apis
        # mellifera, lote X" sem precisar negociar disclosure nenhuma). Não
        # são dados sensíveis por si — não identificam o produtor nem
        # revelam localização exata.
        "batchNumber": batch_number,
        "productType": product_type,
    }

    # --- Disclosable (cada SDObj vira uma Disclosure própria) ---
    #
    # Geolocalização do apiário: um objeto único (lat/lon/descrição) é
    # UMA Disclosure, não decomposto em disclosures separadas para lat e
    # lon. Justificativa: lat e lon isolados não têm utilidade
    # semântica um sem o outro (um lat sozinho não localiza nada) — não
    # há cenário real de "quero provar só a latitude, não a longitude".
    # Tratar como uma unidade atômica também evita um problema sutil de
    # SD-JWT: se lat e lon fossem disclosures INDEPENDENTES, um
    # verificador que recebesse só uma delas não teria garantia
    # criptográfica de que as duas vieram do MESMO par originalmente
    # emitido (embora aqui isso não seja uma ameaça real dado que ambas
    # sempre são reveladas juntas na prática) — mas mantém o modelo mais
    # simples de raciocinar.
    #
    # M4: só incluímos o claim se a query realmente encontrou uma
    # localização (ver bloco OPTIONAL da query) — omitir o claim quando o
    # dado não existe é preferível a uma Disclosure com lat/lon None,
    # porque um None disclosável não corresponde a nenhum dado real do
    # produtor.
    if apiary_location is not None:
        user_claims[SDObj("apiaryLocation")] = apiary_location

    # Dados de laboratório: TRÊS disclosures separadas (uma por
    # sub-campo), não um objeto único "labResults" com uma única
    # Disclosure. Justificativa: minimização de dados por campo real —
    # um verificador que só precisa confirmar conformidade de umidade
    # (ex.: gate SHACL do certifier, que já valida limite de umidade por
    # espécie conforme CLAUDE.md/validation-shapes.ttl) não deveria
    # precisar obrigar a revelação de HMF e acidez juntos só porque
    # estavam agrupados em um único objeto disclosable. Separar por
    # sub-campo é o uso mais literal do propósito do SD-JWT: cada fato
    # discreto é seu próprio Disclosure, disclosable independentemente.
    #
    # M4: cada um só entra em user_claims se a query retornou valor (ver
    # OPTIONAL de cada um na query) — um lote sem, por exemplo,
    # hasHMFContent preenchido não deveria gerar um claim "hmfContent": None
    # disclosável, que não corresponde a um fato real do lote.
    if row["moisture"] is not None:
        user_claims[SDObj("moistureContent")] = row["moisture"]
    if row["hmf"] is not None:
        user_claims[SDObj("hmfContent")] = row["hmf"]
    if row["acidity"] is not None:
        user_claims[SDObj("acidityContent")] = row["acidity"]

    # Identidade do produtor: NOME e DID como disclosures SEPARADAS, não
    # agrupadas em um objeto "producer" único. Justificativa: são usos
    # diferentes por consumidores diferentes — um comprador que só quer
    # verificar proveniência via ancoragem em blockchain (M11) precisa
    # do `producerDID` (chave para consultar o AssetRegistry.sol) mas
    # não necessariamente do nome civil da pessoa; já uma auditoria
    # regulatória (MAPA) pode exigir o nome sem precisar do material de
    # chave. Separar permite que cada apresentação (OID4VP, fora do
    # escopo deste milestone) revele só o que o caso de uso exige.
    if producer_name is not None:
        user_claims[SDObj("producerName")] = producer_name
    if row["producerDID"] is not None:
        user_claims[SDObj("producerDID")] = row["producerDID"]

    # SEM `certificateReference`: não existe certificado real emitido para
    # este lote (nem no dataset do coop-a, nem no certifier). Claim fica
    # ausente do payload até existir um certificado real a referenciar
    # (provavelmente via SPARQL federado) — mesma decisão do coop-b.

    # --- 5. Monta e assina o SD-JWT ---
    issuer_key = get_issuer_key()

    sdjwt = SDJWTIssuer(
        user_claims=user_claims,
        issuer_key=issuer_key,
        holder_key=holder_key,
        sign_alg="EdDSA",
        extra_header_parameters={"typ": SD_JWT_TYP},
    )

    return {"credential": sdjwt.sd_jwt_issuance}
