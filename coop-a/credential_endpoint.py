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

Fluxo de dados fixos vs. dinâmicos: os claims da credencial (lote de mel,
laboratório, produtor) são HARDCODED neste milestone — inspirados em
coop-a/data/sample-instance.ttl, sem query real ao Fuseki. M4 troca isso por
uma consulta SPARQL real pós-validação SHACL.
"""
import time

import jwt as pyjwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from jwcrypto.jwk import JWK
from sd_jwt.common import SDObj
from sd_jwt.issuer import SDJWTIssuer

from config import CREDENTIAL_VALIDITY_SECONDS, DID_WEB
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

    # --- 4. Monta os claims da credencial (dados FIXOS — M4 pluga Fuseki) ---
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
        "batchNumber": "MS-2026-042",
        "productType": "Apis mellifera",

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
        SDObj("apiaryLocation"): {
            "lat": -15.7,
            "lon": -47.9,
            "description": "Apiário Cerrado Vivo, SIE-DF-0123",
        },

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
        SDObj("moistureContent"): "18.5",
        SDObj("hmfContent"): "12.3",
        SDObj("acidityContent"): "32.1",

        # Identidade do produtor: NOME e DID como disclosures SEPARADAS, não
        # agrupadas em um objeto "producer" único. Justificativa: são usos
        # diferentes por consumidores diferentes — um comprador que só quer
        # verificar proveniência via ancoragem em blockchain (M11) precisa
        # do `producerDID` (chave para consultar o AssetRegistry.sol) mas
        # não necessariamente do nome civil da pessoa; já uma auditoria
        # regulatória (MAPA) pode exigir o nome sem precisar do material de
        # chave. Separar permite que cada apresentação (OID4VP, fora do
        # escopo deste milestone) revele só o que o caso de uso exige.
        SDObj("producerName"): "João da Silva",
        SDObj("producerDID"): "did:key:z6MkpTHR8VNsBxYAAWHut2Geadd9jSwuBV8xRoAnwWsdvktH",

        # Referência ao certificado emitido pelo certifier (M4/certifier —
        # ainda não existe de fato; valor placeholder plausível). Disclosable
        # porque nem toda apresentação da credencial de lote precisa expor o
        # vínculo com o processo de certificação específico (isso é mais
        # relevante para auditoria de cadeia de custódia / PROV-O do que
        # para uma verificação simples de "este lote é de mel de Apis").
        SDObj("certificateReference"): "urn:agritrust:certifier:cert:CERT-2026-042",
    }

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
