"""
Credential endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M3.

Escopo: `POST /credential`. Este é o passo final do fluxo pre-authorized_code
começado em M1 (metadata) → M2 (`/token`) → M2.5 (`/nonce`): o holder já tem
um `access_token` (M2) e um `c_nonce` fresco (M2.5); aqui ele envia os dois —
o access_token no header `Authorization`, o c_nonce embutido dentro de um JWT
de key proof assinado pela própria chave dele — e recebe de volta um SD-JWT
VC (`HoneyBatchCredential`, conforme declarado no metadata de M1).

Réplica exata do MECANISMO de coop-a/credential_endpoint.py (autenticação
via access_token, validação do key proof via c_nonce single-use, montagem e
assinatura do SD-JWT) — ver a docstring lá para a justificativa completa das
duas notas de confiança sobre a spec (campo `credential_configuration_id` no
request, formato da resposta de sucesso) e da simplificação de segurança do
proof JWT (quem barra replay é o c_nonce, não verificação de assinatura do
holder). Não repetido aqui em detalhe — o mecanismo é idêntico.

O que MUDA em relação a coop-a: os CLAIMS da credencial. coop-b é a
cooperativa que também associa meliponicultores (abelhas nativas sem
ferrão) — o lote de referência aqui é de mel de Jataí (Tetragonisca
angustula), não de Apis mellifera. Ver coop-b/data/sample-instance.ttl
(:Lote_Mel_Jatai_2026_007, :HoneyNativeStinglessBee) para a instância de
domínio que alimenta a query abaixo.

Fluxo de dados fixos vs. dinâmicos: em M3, os claims da credencial eram
HARDCODED. Este é o M4: a Parte 4 (montagem dos claims) agora roda uma
consulta SPARQL SELECT real contra o dataset "coop-b" do Fuseki (já populado
pelo pipeline SHACL de M1.5/pipeline.py) — o resto do arquivo (autenticação,
proof, c_nonce) permanece idêntico ao M3. O SCHEMA de dados de coop-b (ver
sample-instance.ttl) é estruturalmente idêntico ao de coop-a — mesmas
propriedades (agritrust:ownedBy, :hasMoistureContent,
agritrust:hasOperatingLocation -> geo:Feature -> geo:Point -> geo:asWKT etc.)
— só os valores e o rdf:type do lote (:HoneyNativeStinglessBee em vez de
:HoneyApis) mudam. Por isso a query SPARQL abaixo é, propositalmente, quase
idêntica à de coop-a: não há schema mismatch a reportar aqui (diferente do
certifier, ver certifier/credential_endpoint.py).
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

# Mesmo VCT de coop-a: é o MESMO tipo de credencial (lote de mel rastreado),
# só emitido por um issuer diferente. O `vct` identifica a estrutura/semântica
# da credencial, não o emissor — isso já é coberto pelo claim `iss`.
VCT = "https://agritrust.example/vct/honey-batch"

# Header `typ` do JWS — `dc+sd-jwt`, não `vc+sd-jwt` (ver nota de mudança de
# media type em coop-a/credential_endpoint.py).
SD_JWT_TYP = "dc+sd-jwt"

# `typ` esperado no header do JWT de key proof enviado pelo holder,
# conforme OID4VCI ("JWT Proof Type"). Não é verificado estritamente aqui
# (ver simplificação de proof abaixo), só documentado.
PROOF_JWT_TYP = "openid4vci-proof+jwt"


# --- M4: query SPARQL contra o Fuseki do coop-b ---
#
# Namespaces usados na instância (coop-b/data/sample-instance.ttl) — os
# MESMOS prefixos/URIs declarados no cabeçalho daquele .ttl. Repetidos aqui
# como literais pela mesma razão de coop-a: não há arquivo de queries
# separado neste projeto ainda.
_SPARQL_PREFIXES = """
PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
"""

# Query SELECT que busca, a partir de um agritrust:batchNumber, tudo que a
# Parte 4 precisa para montar os claims da credencial. MESMA estrutura da
# query de coop-a (ver comentários por campo lá) — repetida aqui porque o
# schema de coop-b usa exatamente as mesmas propriedades:
#
# - ?batch / ?productType: `?batch a ?productType` não é OPTIONAL pela mesma
#   razão de coop-a — o lote (:Lote_Mel_Jatai_2026_007) tem um único
#   rdf:type assertado (:HoneyNativeStinglessBee), sem ambiguidade a
#   resolver, e inference="none" no pipeline SHACL (M1.5) não materializa
#   hierarquia de classes.
# - ?moisture / ?hmf / ?acidity: cada um em seu próprio OPTIONAL — mesma
#   razão de coop-a (nem todo lote necessariamente tem os três parâmetros
#   preenchidos). `:hasDiastaseActivity` continua fora da query: não é um
#   claim que M3 definiu para a credencial de LOTE de coop-b (mel nativo não
#   tem esse parâmetro medido na instância) — buscar um campo que nunca vira
#   claim seria código morto, mesma decisão de coop-a.
# - ?floralOrigin: origem floral é dado DECLARADO PELO PRODUTOR na colheita
#   (:hasFloralOrigin no próprio recurso do lote, não no laudo do
#   certifier) — mesma migração já feita em coop-a/credential_endpoint.py
#   (ver CHANGELOG.md, "Ajuste pós-demo"). OPTIONAL pela mesma razão dos
#   três campos acima.
# - ?producer: obrigatório via agritrust:ownedBy, mesma razão de coop-a (um
#   lote sem produtor dono seria dado de domínio incompleto).
# - ?producerLabel / ?producerFoafName / ?producerDID: mesma estrutura de
#   coop-a — dois OPTIONAL separados para rótulo humano vs. nome FOAF
#   (prioridade resolvida em Python), mais o DID do produtor.
# - ?geoWKT / ?geoLabel: bloco de geolocalização como UM OPTIONAL aninhado
#   (produtor -> agritrust:hasOperatingLocation -> geo:Feature ->
#   geo:hasGeometry -> geo:Point -> geo:asWKT), mesma cadeia usada em
#   coop-a/data/sample-instance.ttl e agora também presente em
#   coop-b/data/sample-instance.ttl (:Meliponario_Jatai_Cerrado_Geo, ligada
#   via :Apicultor_Joao_Cerrado agritrust:hasOperatingLocation).
_CLAIMS_QUERY_TEMPLATE = _SPARQL_PREFIXES + """
SELECT ?productType ?moisture ?hmf ?acidity ?floralOrigin
       ?producerLabel ?producerFoafName ?producerDID ?geoWKT ?geoLabel
WHERE {
  ?batch agritrust:batchNumber %(batch_number)s ;
         a ?productType ;
         agritrust:ownedBy ?producer .
  OPTIONAL { ?batch :hasMoistureContent ?moisture }
  OPTIONAL { ?batch :hasHMFContent ?hmf }
  OPTIONAL { ?batch :hasAcidity ?acidity }
  OPTIONAL { ?batch :hasFloralOrigin ?floralOrigin }
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

# `POINT(lon lat)` — mesmo formato/regex de coop-a (ver justificativa lá
# sobre não usar lib GIS para um parse tão simples).
_WKT_POINT_RE = re.compile(r"POINT\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Mesmo nível de escape simples de coop-a (não é um escapador SPARQL
    genérico e completo) — ver docs/known-issues.md.
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
    """Consulta o Fuseki do coop-b (SPARQL SELECT) pelos dados do lote.

    Retorna um dict com os campos brutos da primeira linha de resultado, ou
    None se o batch_number não existir no grafo (query sem resultados) — o
    chamador decide o que fazer (404), esta função não decide HTTP status.
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
        "floralOrigin": _val("floralOrigin"),
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
    assinatura contra uma PKI real de holder. Mesma simplificação
    deliberada de MVP documentada em coop-a/credential_endpoint.py: quem
    efetivamente barra replay/forjamento neste milestone é o c_nonce de uso
    único, não a assinatura do proof em si (isso é trabalho de M5).
    """
    header = pyjwt.get_unverified_header(jwt_str)
    payload = pyjwt.decode(jwt_str, options={"verify_signature": False})
    return header, payload


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

    # `batch_number`: campo OPCIONAL, EXTENSÃO PRÓPRIA DESTE MVP, mesma
    # decisão/justificativa de coop-a (ver docstring lá). Se ausente, cai no
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
    # binding na credencial emitida.
    holder_jwk_dict = proof_header.get("jwk")
    holder_key = JWK(**holder_jwk_dict) if holder_jwk_dict else None

    # --- 4. Monta os claims da credencial (M4: query SPARQL real ao Fuseki) ---
    try:
        row = query_batch_claims(batch_number)
    except requests.RequestException as exc:
        # Falha de infraestrutura (Fuseki fora do ar, timeout, credencial de
        # admin errada etc.) — 502 porque o coop-b (issuer) depende de um
        # serviço upstream (Fuseki) que falhou ao responder.
        return _oauth_error(502, "server_error", f"Falha ao consultar o Fuseki: {exc}")

    if row is None:
        # Lote pedido não existe no grafo do coop-b — não emitimos uma
        # credencial vazia/quebrada, retornamos 404.
        return _oauth_error(
            404,
            "invalid_request",
            f"Nenhum lote encontrado no Fuseki (dataset coop-b) com "
            f"agritrust:batchNumber = {batch_number!r}.",
        )

    # `productType`: nome local (fragmento após '#') da URI de rdf:type do
    # lote — ex. de
    # https://w3id.org/agrisemantics/AGRITRUST/bee-extension#HoneyNativeStinglessBee
    # extrai "HoneyNativeStinglessBee". Mesma decisão de coop-a: M3 usava um
    # rótulo humano hardcoded ("Tetragonisca angustula (Jataí)"), mas esse
    # rótulo só existe como comentário/rdfs:label em bee-extension.ttl e
    # instances.ttl, NÃO carregados no Fuseki (só a instância de dados é
    # inserida lá, ver pipeline.py) — não recuperável via SPARQL puro contra
    # este dataset.
    product_type = row["productType"].rsplit("#", 1)[-1].rsplit("/", 1)[-1]

    # producerName: mesma prioridade de coop-a (foaf:name sobre rdfs:label).
    producer_name = row["producerFoafName"] or row["producerLabel"]

    # Geolocalização: parse manual do WKT, mesma justificativa de coop-a.
    # Claim só incluído se a query encontrou localização (bloco OPTIONAL na
    # query) — omitir em vez de inventar coordenadas quando o dado não
    # existe.
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
        # Mesma justificativa estrutural de coop-a: `vct`/`iss` precisam
        # estar em claro para o verificador saber como interpretar o JWT e
        # resolver a chave de assinatura ANTES de processar qualquer
        # disclosure; `batchNumber`/`productType` são o mínimo que qualquer
        # apresentação precisa expor para ser útil, sem serem dados
        # sensíveis por si só.
        "vct": VCT,
        "iss": DID_WEB,
        "iat": now,
        "exp": exp,
        "batchNumber": batch_number,
        "productType": product_type,
    }

    # --- Disclosable (cada SDObj vira uma Disclosure própria) ---
    #
    # Geolocalização do meliponário: uma única Disclosure para o objeto
    # composto (lat/lon/descrição) — mesma justificativa de coop-a (lat/lon
    # isolados não têm utilidade semântica um sem o outro).
    if apiary_location is not None:
        user_claims[SDObj("apiaryLocation")] = apiary_location

    # Dados de laboratório: três disclosures separadas, mesma justificativa
    # de minimização granular por sub-campo de coop-a. `diastaseActivity`
    # continua FORA (mesmo motivo de M3: mel de meliponíneo não tem esse
    # parâmetro na instância de coop-b, e M3 nunca definiu esse claim aqui).
    if row["moisture"] is not None:
        user_claims[SDObj("moistureContent")] = row["moisture"]
    if row["hmf"] is not None:
        user_claims[SDObj("hmfContent")] = row["hmf"]
    if row["acidity"] is not None:
        user_claims[SDObj("acidityContent")] = row["acidity"]

    # Origem floral: dado DECLARADO PELO PRODUTOR (:hasFloralOrigin no
    # recurso do lote), não confirmado por laboratório — mesma migração de
    # coop-a. Disclosable pela mesma razão dos outros campos de laudo.
    if row["floralOrigin"] is not None:
        user_claims[SDObj("floralOrigin")] = row["floralOrigin"]

    # Identidade do produtor: nome e DID como disclosures separadas, mesma
    # justificativa de coop-a (consumidores diferentes precisam de partes
    # diferentes da identidade).
    if producer_name is not None:
        user_claims[SDObj("producerName")] = producer_name
    if row["producerDID"] is not None:
        user_claims[SDObj("producerDID")] = row["producerDID"]

    # SEM `certificateReference`: mesma decisão de M3 — não existe ainda
    # nenhum certificado real emitido pelo certifier para o lote coop-b (o
    # certifier, nesta entrega, só emite de fato uma credencial de
    # certificação para o lote MS-2026-042 de coop-a). M4 não reintroduz
    # esse placeholder: continua ausente até existir um certificado real a
    # referenciar.

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
