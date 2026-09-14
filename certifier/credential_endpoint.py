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
certifier/data/sample-lab-result-valid.ttl para o contexto de domínio: o
certifier recebe o laudo (hoje simulado por um .ttl estático, já que a API
mock de laboratório real ainda não existe), valida via SHACL (M1.5) e —
neste M3 — também pode emitir uma credencial verificável sobre esse laudo.

Tipo novo: `HoneyCertificationCredential`
(vct = https://agritrust.example/vct/honey-certification), distinto de
`HoneyBatchCredential` (vct = .../honey-batch) usado por coop-a/coop-b.
São dois VCTs diferentes de propósito: a estrutura de claims é
completamente distinta (um lote fala de produtor/apiário/espécie; uma
certificação fala de laudo/laboratório/parâmetros físico-químicos) — usar
o mesmo vct para os dois obrigaria um verificador a inspecionar claims
específicos só para descobrir que tipo de coisa está segurando, o que o
`vct` existe justamente para evitar.

Os valores abaixo são inspirados em certifier/data/sample-lab-result-valid.ttl
(o laudo sobre o lote MS-2026-042 de coop-a, já validado pelo gate SHACL do
M1.5) e em ontology/instances.ttl (:Certificado_Analise_Nativa,
:Processo_Colheita_Silvestre_15042026, :Laboratorio_Apicola_Central) — ver
comentários inline nos pontos em que um valor é extrapolado/inventado por
falta de instância real correspondente.

Fluxo de dados fixos vs. dinâmicos: os claims são HARDCODED neste
milestone, sem query real ao Fuseki do certifier. M4 troca isso por uma
consulta SPARQL real pós-validação SHACL/pipeline.
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
        # Mesma justificativa estrutural de coop-a/coop-b para vct/iss: têm
        # que estar em claro para o verificador saber como interpretar o
        # JWT e resolver a chave de assinatura antes de qualquer disclosure.
        "vct": VCT,
        "iss": DID_WEB,
        "iat": now,
        "exp": exp,
        # `batchNumber`: vínculo entre esta credencial de CERTIFICAÇÃO e o
        # lote ORIGINAL emitido por coop-a (mesmo valor hardcoded lá,
        # "MS-2026-042" — decisão confirmada com o usuário do projeto).
        # Sempre visível porque é justamente o dado que permite a um
        # verificador CRUZAR as duas credenciais (lote + certificação) sem
        # precisar negociar disclosure nenhuma — esconder isso quebraria a
        # própria utilidade de "amarrar" as duas credenciais.
        "batchNumber": "MS-2026-042",
        # `legalStatus`: de agritrust:legalStatus em
        # ontology/instances.ttl (:Lote_Mel_Silvestre_2026_042). Sempre
        # visível pela mesma razão de coop-a expor batchNumber/productType
        # em claro: é o mínimo que qualquer verificação regulatória ou de
        # prateleira precisa (aprovado ou não para comercialização) sem
        # negociar acesso aos detalhes físico-químicos do laudo.
        "legalStatus": "Aprovado para Comercialização",

        # --- Disclosable (cada SDObj vira uma Disclosure própria) ---
        #
        # Mesma filosofia de minimização granular de coop-a/coop-b: cada
        # fato discreto do laudo é sua própria Disclosure, para que um
        # verificador possa pedir só o que precisa (ex.: um comprador que só
        # quer confirmar `certificateType`+`qualityStatus` não é forçado a
        # também receber os valores numéricos de umidade/HMF/acidez, que
        # podem ser mais sensíveis para o laboratório/cooperativa divulgar
        # amplamente).
        #
        # Identificação do certificado em si (de agritrust:certificateType /
        # agritrust:certificateNumber, ver :Certificado_Analise_Nativa em
        # ontology/instances.ttl).
        SDObj("certificateType"): "Análise Físico-Química",
        # Valor placeholder plausível, mesmo padrão de nomenclatura de
        # "LAUDO-2026-0421" em instances.ttl (:Certificado_Analise_Nativa) —
        # mas NÚMERO DISTINTO. Correção pós-revisão: reusar o número exato
        # de outra credencial (que pertence de fato ao lote de Jataí,
        # MN-2026-015, não a este lote de Apis MS-2026-042) criaria uma
        # colisão de identificador entre dois laudos diferentes na
        # ontologia canônica — mesmo sendo "só um placeholder", um
        # certificateNumber existe para ser único por laudo.
        SDObj("certificateNumber"): "LAUDO-2026-0442",

        # Parâmetros físico-químicos: QUATRO disclosures separadas (não um
        # objeto único), valores de
        # certifier/data/sample-lab-result-valid.ttl — mesmo raciocínio de
        # coop-a para moisture/hmf/acidity (cada parâmetro é um fato
        # discreto, disclosable independentemente); `diastaseActivity` entra
        # aqui como uma quarta disclosure própria porque o laudo do
        # certifier DE FATO mede esse parâmetro (:hasDiastaseActivity
        # presente no .ttl), diferente de coop-a/coop-b, que não o expõem
        # como claim na credencial de lote.
        SDObj("moistureContent"): "18.0",
        SDObj("hmfContent"): "30.0",
        SDObj("acidityContent"): "28.5",
        SDObj("diastaseActivity"): "12.0",

        # Origem floral (de :hasFloralOrigin) — disclosable porque é um dado
        # de proveniência mais fino que pode não ser necessário para toda
        # verificação (ex.: um regulador que só confirma conformidade legal
        # não precisa necessariamente da origem floral específica).
        SDObj("floralOrigin"): "Aroeira (Myracrodruon urundeuva)",

        # Status de qualidade (de agritrust:qualityStatus, mesmo texto do
        # .ttl de referência) — disclosable, ao contrário de `legalStatus`
        # (sempre visível): `legalStatus` é a decisão BINÁRIA final
        # ("pode comercializar ou não"), enquanto `qualityStatus` é o
        # detalhe técnico/normativo por trás dessa decisão — um verificador
        # de prateleira quer só o binário; uma auditoria mais profunda pede
        # também o detalhe.
        SDObj("qualityStatus"): "Em Conformidade com IN 11/2000 e Portaria 795/2023",

        # Atividade de análise laboratorial: um objeto ÚNICO (SDObj),
        # análogo à decisão de `apiaryLocation` em coop-a de agrupar campos
        # relacionados numa única disclosure quando eles só fazem sentido
        # revelados juntos (início/fim/responsável de UM MESMO evento —
        # revelar só o horário de início sem o responsável, ou vice-versa,
        # não tem utilidade prática isolada).
        #
        # EXTRAPOLAÇÃO EXPLÍCITA (não é cópia de uma instância real
        # existente): não há, em ontology/instances.ttl, uma instância de
        # "processo de análise laboratorial" pronta — só processos de
        # colheita/filtração (:Processo_Colheita_Silvestre_15042026, com
        # prov:startedAtTime/endedAtTime/agritrust:executedBy). Adaptamos
        # esse MESMO padrão de propriedades (não propriedades novas) para
        # um evento de análise fictício, plausível, um dia após a colheita
        # do lote (2026-04-15) e um dia antes da data de emissão do
        # certificado de referência mais próximo no ontology
        # (:Certificado_Analise_Nativa, vc:issuanceDate "2026-04-17").
        # `executedBy`: entidade (:Laboratorio_Apicola_Central) já modelada
        # em ontology/instances.ttl, mas o DID lá (agritrust:hasDID
        # "did:agritrust:lab:LAB001") é do formato de versão ANTERIOR do
        # projeto — o CLAUDE.md já avisa explicitamente sobre isso para
        # instances.ttl em geral ("gerar credenciais novas em did:web/
        # did:key, não copiar este formato") e fixa a decisão de DIDs:
        # did:web para instituições. Um laboratório é uma instituição, não
        # um produtor individual, então NUNCA did:agritrust:* nem did:key
        # aqui — usamos um did:web fictício de teste em vez do formato
        # legado.
        SDObj("analysisActivity"): {
            "startedAtTime": "2026-04-16T08:00:00Z",
            "endedAtTime": "2026-04-16T16:30:00Z",
            "executedBy": "did:web:laboratorio-ficticio.example",
        },

        # Referência da PRÓPRIA credencial de certificação. Mesmo valor que
        # coop-a/credential_endpoint.py já citava como placeholder do lado
        # da credencial de LOTE (`certificateReference`) — aqui, do lado do
        # certifier, este é o identificador "real" (dentro do MVP) que
        # fecha o vínculo cruzado entre as duas credenciais: a de lote
        # (coop-a) referencia este identificador; esta credencial de
        # certificação declara ser exatamente essa referência.
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
