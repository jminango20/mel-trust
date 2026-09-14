"""
Credential endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M3.

Réplica exata do MECANISMO de coop-a/credential_endpoint.py (autenticação
via access_token, validação do key proof via c_nonce single-use, montagem e
assinatura do SD-JWT) — ver a docstring lá para a justificativa completa
das duas notas de confiança sobre a spec (campo `credential_configuration_id`
no request, formato da resposta de sucesso) e da simplificação de segurança
do proof JWT (quem barra replay é o c_nonce, não verificação de assinatura
do holder). Não repetido aqui em detalhe — o mecanismo é idêntico.

O que MUDA em relação a coop-a: os CLAIMS da credencial. coop-b é a
cooperativa que também associa meliponicultores (abelhas nativas sem
ferrão) — o lote de referência aqui é de mel de Jataí (Tetragonisca
angustula), não de Apis mellifera. Ver coop-b/data/sample-instance.ttl
(:Lote_Mel_Jatai_2026_007, :HoneyNativeStinglessBee) para a instância de
domínio que inspira os valores abaixo — mesma fonte de dados que o pipeline
SHACL deste nó já valida (M1.5).

Fluxo de dados fixos vs. dinâmicos: os claims da credencial são HARDCODED
neste milestone, inspirados em coop-b/data/sample-instance.ttl, sem query
real ao Fuseki. M4 troca isso por uma consulta SPARQL real pós-validação
SHACL — mesma decisão de coop-a.
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

# Mesmo VCT de coop-a: é o MESMO tipo de credencial (lote de mel rastreado),
# só emitido por um issuer diferente. O `vct` identifica a estrutura/semântica
# da credencial, não o emissor — isso já é coberto pelo claim `iss`. Não faz
# sentido um vct por cooperativa quando o esquema de claims é o mesmo
# (batchNumber, productType, dados de laboratório etc.), independente da
# espécie de abelha por trás do lote.
VCT = "https://agritrust.example/vct/honey-batch"

# Header `typ` do JWS — `dc+sd-jwt`, não `vc+sd-jwt` (ver nota de mudança de
# media type em coop-a/credential_endpoint.py).
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

    # --- 4. Monta os claims da credencial (dados FIXOS — M4 pluga Fuseki) ---
    now = int(time.time())
    exp = now + CREDENTIAL_VALIDITY_SECONDS

    user_claims = {
        # --- Sempre visíveis (fora de _sd) ---
        # Mesma justificativa estrutural de coop-a: `vct`/`iss` precisam
        # estar em claro para o verificador saber como interpretar o JWT e
        # resolver a chave de assinatura ANTES de processar qualquer
        # disclosure; `batchNumber`/`productType` são o mínimo que qualquer
        # apresentação precisa expor para ser útil (rastreabilidade pública
        # de lote), sem serem dados sensíveis por si só.
        "vct": VCT,
        "iss": DID_WEB,
        "iat": now,
        "exp": exp,
        # Batch de coop-b/data/sample-instance.ttl (:Lote_Mel_Jatai_2026_007).
        "batchNumber": "MN-2026-007",
        # DECISÃO: diferente de coop-a ("Apis mellifera"), coop-b é
        # meliponicultura (abelha nativa sem ferrão). A espécie usada é
        # Tetragonisca angustula (Jataí), confirmada em
        # coop-b/data/sample-instance.ttl (comentário do arquivo cita
        # explicitamente "espécie escolhida: Tetragonisca angustula (Jataí)",
        # mesma referência de ontology/instances.ttl :Lote_Mel_Jatai_2026_015)
        # — não é um valor inventado aqui, é o dado de domínio já modelado
        # para este nó.
        "productType": "Tetragonisca angustula (Jataí)",

        # --- Disclosable (cada SDObj vira uma Disclosure própria) ---
        #
        # Geolocalização/origem do meliponário: mesma filosofia de coop-a
        # (uma única Disclosure para o objeto composto, porque lat/lon/
        # descrição só fazem sentido revelados juntos — ver justificativa
        # completa em coop-a/credential_endpoint.py). Coordenada DIFERENTE
        # da usada em coop-a (correção pós-revisão: reaproveitar a mesma
        # lat/lon do apiário de Apis para o meliponário sugeria implicitamente
        # que os dois GPS batiam, sem essa confirmação existir em dado de
        # domínio nenhum). Valor abaixo é FICTÍCIO/de teste — não corresponde
        # a nenhuma coordenada real do produtor, só precisa ser um ponto
        # plausível dentro do Cerrado para efeito de MVP.
        SDObj("apiaryLocation"): {
            "lat": -15.83,
            "lon": -48.15,
            "description": "Meliponário Jataí do Cerrado, SIE-DF-0123 "
            "(coordenada fictícia de teste, não georreferenciada)",
        },

        # Dados de laboratório: TRÊS disclosures separadas, mesma
        # justificativa de minimização granular de coop-a. Sem
        # `hmfContent`... espera, HMF está presente; o que NÃO está presente
        # aqui é `diastaseActivity`: diferente de HoneyApis, a instância
        # :HoneyNativeStinglessBee em coop-b/data/sample-instance.ttl não
        # declara `:hasDiastaseActivity` — atividade diastásica não é um
        # parâmetro de controle padrão para mel de meliponíneos neste
        # modelo de dados, então não inventamos um claim para ele.
        SDObj("moistureContent"): "23.0",
        SDObj("hmfContent"): "9.0",
        SDObj("acidityContent"): "45.0",

        # Identidade do produtor: mesmo produtor de coop-a
        # (:Apicultor_Joao_Cerrado, "João da Silva"), mas com o DID
        # cadastrado no lado coop-b (coop-b/data/sample-instance.ttl usa um
        # did:key diferente do usado em coop-a para o mesmo nome civil —
        # replicamos esse dado tal como modelado, sem tentar unificar DIDs
        # entre nós, o que é um problema de identidade federada fora do
        # escopo deste milestone).
        SDObj("producerName"): "João da Silva",
        SDObj("producerDID"): "did:key:z6MkuQ5t9ijNZzxUMWebnh9NF2fBcNiUpmKgJhaWv3fZ7SFA",

        # SEM `certificateReference`: diferente de coop-a, não existe ainda
        # nenhum certificado real emitido pelo certifier para o lote
        # MN-2026-007 (o certifier, nesta entrega, só emite de fato uma
        # credencial de certificação para o lote MS-2026-042 de coop-a — ver
        # certifier/credential_endpoint.py). Inventar um `urn:...:CERT-...`
        # aqui criaria uma referência cruzada que aponta para nada. Correção
        # pós-revisão: o placeholder anterior (CERT-2026-007) foi removido —
        # o claim só deve aparecer quando M4 (ou um M3.5 de certificação real
        # para coop-b) tiver um certificado de verdade para referenciar.
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
