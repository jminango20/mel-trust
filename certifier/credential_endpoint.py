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
lote que a cooperativa já produziu e cadastrou. Ver certifier/pipeline.py e
certifier/data/sample-lab-result-valid.ttl para o contexto de domínio.

Tipo: `HoneyCertificationCredential`
(vct = https://agritrust.example/vct/honey-certification), distinto de
`HoneyBatchCredential` (vct = .../honey-batch) usado por coop-a/coop-b — ver
docstring original de M3 para a justificativa completa de ter dois VCTs.

Fluxo de dados fixos vs. dinâmicos: em M3, TODOS os claims eram HARDCODED.
Este é o M4: a Parte 4 (montagem dos claims) agora roda uma consulta SPARQL
SELECT real contra o dataset "certifier" do Fuseki para os campos que TÊM
correspondência real em certifier/data/sample-lab-result-valid.ttl.

SCHEMA MISMATCH (documentado explicitamente, não escondido): nem todo claim
que M3 hardcoded tem uma tripla correspondente no grafo do laudo. Migraram
para SPARQL real: `legalStatus` (sempre visível), `qualityStatus`,
`floralOrigin`, `moistureContent`, `hmfContent`, `acidityContent`,
`diastaseActivity` (disclosable) — todos existem como triplas em
sample-lab-result-valid.ttl (agritrust:legalStatus, agritrust:qualityStatus,
:hasFloralOrigin, :hasMoistureContent, :hasHMFContent, :hasAcidity,
:hasDiastaseActivity) e são lidos via agritrust:batchNumber do lote
referenciado pelo laudo. CONTINUAM HARDCODED (sem correspondência no grafo
do certifier, ver justificativa por campo abaixo, na montagem dos claims):
`certificateType`, `certificateNumber`, o objeto `analysisActivity`
(startedAtTime/endedAtTime/executedBy) e `certificateReference`. Não
inventamos propriedades RDF novas para representá-los — isso seria além do
escopo desta migração e criaria dado fictício disfarçado de dado real.
"""
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


# --- M4: query SPARQL contra o Fuseki do certifier ---
#
# Namespaces usados na instância (certifier/data/sample-lab-result-valid.ttl)
# — os MESMOS prefixos/URIs declarados no cabeçalho daquele .ttl.
_SPARQL_PREFIXES = """
PREFIX : <https://w3id.org/agrisemantics/AGRITRUST/bee-extension#>
PREFIX agritrust: <https://w3id.org/agrisemantics/AGRITRUST#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
"""

# Query SELECT que busca, a partir de agritrust:batchNumber (aqui: o número
# do LOTE ORIGINAL sobre o qual o laudo fala, não um identificador próprio do
# laudo — o .ttl do certifier não modela um recurso "laudo" separado do
# lote; ele registra as medições laboratoriais DIRETAMENTE no mesmo recurso
# :Lote_Mel_Silvestre_2026_042, só que agora do lado do dataset "certifier",
# povoado a partir do resultado de laboratório em vez da autodeclaração do
# produtor). Comentários por campo:
#
# - ?labResult: NÃO expomos um claim `productType` aqui — diferente de
#   coop-a/coop-b, o certifier (M3) nunca definiu esse claim na credencial
#   de CERTIFICAÇÃO (o tipo do mel é responsabilidade da credencial de
#   LOTE, não da de certificação). Ainda assim exigimos `?labResult a
#   ?anyType` (variável não usada em nenhum claim, só como guarda na
#   WHERE) para não casar com QUALQUER recurso que tenha por acaso
#   `agritrust:batchNumber` + `agritrust:legalStatus` — exigir um rdf:type
#   qualquer é uma checagem mínima de que o recurso é um dado de domínio
#   tipado (revisão pós-M4: a primeira versão desta query não tinha essa
#   guarda).
# - ?legalStatus: NÃO OPTIONAL — é o claim sempre-visível `legalStatus` da
#   credencial (decisão binária final de comercialização); se um laudo não
#   tiver esse campo preenchido, é dado de domínio incompleto e preferimos
#   que a query não devolva parte dos claims silenciosamente (mesma postura
#   de coop-a com agritrust:ownedBy).
# - ?qualityStatus / ?floralOrigin / ?moisture / ?hmf / ?acidity / ?diastase:
#   cada um em seu próprio OPTIONAL — nem todo laudo necessariamente
#   preenche todos os parâmetros físico-químicos (ex.: um laudo simplificado
#   sem diástase). `diastaseActivity` ESTÁ presente aqui (diferente de
#   coop-a/coop-b): o laudo do certifier de fato mede esse parâmetro
#   (:hasDiastaseActivity existe em sample-lab-result-valid.ttl), e M3 já
#   definiu `diastaseActivity` como claim real desta credencial.
_CLAIMS_QUERY_TEMPLATE = _SPARQL_PREFIXES + """
SELECT ?legalStatus ?qualityStatus ?floralOrigin
       ?moisture ?hmf ?acidity ?diastase
WHERE {
  ?labResult agritrust:batchNumber %(batch_number)s ;
             agritrust:legalStatus ?legalStatus ;
             a ?anyType .
  OPTIONAL { ?labResult agritrust:qualityStatus ?qualityStatus }
  OPTIONAL { ?labResult :hasFloralOrigin ?floralOrigin }
  OPTIONAL { ?labResult :hasMoistureContent ?moisture }
  OPTIONAL { ?labResult :hasHMFContent ?hmf }
  OPTIONAL { ?labResult :hasAcidity ?acidity }
  OPTIONAL { ?labResult :hasDiastaseActivity ?diastase }
}
LIMIT 1
"""


def _sparql_literal(value: str) -> str:
    """Escapa uma string simples para uso como literal RDF dentro da query.

    Mesmo nível de escape simples de coop-a (não é um escapador SPARQL
    genérico e completo) — ver docs/known-issues.md.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def query_certification_claims(batch_number: str) -> dict | None:
    """Consulta o Fuseki do certifier (SPARQL SELECT) pelos dados do laudo.

    Retorna um dict com os campos brutos da primeira linha de resultado, ou
    None se nenhum laudo com esse batch_number existir no grafo — o
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
        "legalStatus": _val("legalStatus"),
        "qualityStatus": _val("qualityStatus"),
        "floralOrigin": _val("floralOrigin"),
        "moisture": _val("moisture"),
        "hmf": _val("hmf"),
        "acidity": _val("acidity"),
        "diastase": _val("diastase"),
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

    # `batch_number`: campo OPCIONAL, EXTENSÃO PRÓPRIA DESTE MVP (mesma
    # decisão de coop-a/coop-b). Aqui identifica o LOTE cujo laudo se quer
    # consultar — não um identificador próprio do laudo (ver comentário da
    # query acima). Se ausente, cai no DEFAULT_BATCH_NUMBER.
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

    # --- 4. Monta os claims da credencial (M4: query SPARQL real ao Fuseki
    # para os campos com correspondência no grafo; hardcoded para os que não
    # têm — ver docstring do módulo para a lista completa) ---
    try:
        row = query_certification_claims(batch_number)
    except requests.RequestException as exc:
        # Falha de infraestrutura (Fuseki fora do ar, timeout, credencial de
        # admin errada etc.) — 502 porque o certifier (issuer) depende de um
        # serviço upstream (Fuseki) que falhou ao responder.
        return _oauth_error(502, "server_error", f"Falha ao consultar o Fuseki: {exc}")

    if row is None:
        # Nenhum laudo com esse batchNumber existe no grafo do certifier —
        # não emitimos uma credencial de certificação vazia/quebrada,
        # retornamos 404.
        return _oauth_error(
            404,
            "invalid_request",
            f"Nenhum laudo encontrado no Fuseki (dataset certifier) com "
            f"agritrust:batchNumber = {batch_number!r}.",
        )

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
        # lote ORIGINAL emitido por coop-a. M4: agora é o próprio valor
        # usado para consultar o Fuseki (batch_number acima), não mais um
        # literal hardcoded independente — mas o efeito observável é o
        # mesmo valor de M3 ("MS-2026-042" por default). Sempre visível pela
        # mesma razão de M3: é o dado que permite cruzar lote + certificação
        # sem negociar disclosure nenhuma.
        "batchNumber": batch_number,
        # `legalStatus`: M4 REAL — vem de agritrust:legalStatus no laudo
        # (ver query acima), não mais hardcoded. Sempre visível pela mesma
        # razão de M3 (decisão binária final de comercialização).
        "legalStatus": row["legalStatus"],
    }

    # --- Disclosable (cada SDObj vira uma Disclosure própria) ---

    # Identificação do certificado em si: SEM correspondência no grafo do
    # certifier (sample-lab-result-valid.ttl não modela um recurso "laudo"
    # com número/tipo próprios — só as medições sobre o lote). SCHEMA
    # MISMATCH: continua HARDCODED, idêntico a M3 — não inventamos
    # agritrust:certificateType/certificateNumber como triplas nesta
    # migração.
    user_claims[SDObj("certificateType")] = "Análise Físico-Química"
    user_claims[SDObj("certificateNumber")] = "LAUDO-2026-0442"

    # Parâmetros físico-químicos: M4 REAL — cada um só entra se a query
    # retornou valor (blocos OPTIONAL da query), mesma cautela de coop-a
    # contra claims disclosable com valor None. `diastaseActivity` migra
    # para SPARQL real aqui (diferente de coop-a/coop-b): o laudo do
    # certifier de fato mede esse parâmetro.
    if row["moisture"] is not None:
        user_claims[SDObj("moistureContent")] = row["moisture"]
    if row["hmf"] is not None:
        user_claims[SDObj("hmfContent")] = row["hmf"]
    if row["acidity"] is not None:
        user_claims[SDObj("acidityContent")] = row["acidity"]
    if row["diastase"] is not None:
        user_claims[SDObj("diastaseActivity")] = row["diastase"]

    # Origem floral: M4 REAL — de :hasFloralOrigin no laudo.
    if row["floralOrigin"] is not None:
        user_claims[SDObj("floralOrigin")] = row["floralOrigin"]

    # Status de qualidade: M4 REAL — de agritrust:qualityStatus no laudo.
    # Disclosable, ao contrário de `legalStatus` (sempre visível) — mesma
    # distinção binário-vs-detalhe de M3.
    if row["qualityStatus"] is not None:
        user_claims[SDObj("qualityStatus")] = row["qualityStatus"]

    # Atividade de análise laboratorial: objeto único (SDObj). SEM
    # correspondência no grafo do certifier — sample-lab-result-valid.ttl
    # não tem prov:startedAtTime/endedAtTime/agritrust:executedBy para o
    # laudo (só ontology/instances.ttl tem um evento PROV-O análogo para
    # COLHEITA, não para análise laboratorial — ver docstring original de
    # M3). SCHEMA MISMATCH: continua HARDCODED, idêntico a M3.
    user_claims[SDObj("analysisActivity")] = {
        "startedAtTime": "2026-04-16T08:00:00Z",
        "endedAtTime": "2026-04-16T16:30:00Z",
        "executedBy": "did:web:laboratorio-ficticio.example",
    }

    # Referência da PRÓPRIA credencial de certificação: SEM correspondência
    # no grafo (é um identificador da CREDENCIAL, não do dado de domínio
    # subjacente — nasce no momento da emissão, não é algo que o laudo em si
    # "contém"). SCHEMA MISMATCH: continua HARDCODED, idêntico a M3. Este é
    # o mesmo identificador que a credencial de LOTE do coop-a referencia
    # como `certificateReference` (ver coop-a/credential_endpoint.py) —
    # fecha o vínculo cruzado entre as duas credenciais.
    user_claims[SDObj("certificateReference")] = "urn:agritrust:certifier:cert:CERT-2026-042"

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
