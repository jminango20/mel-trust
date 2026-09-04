"""
Nonce endpoint OID4VCI (OpenID for Verifiable Credential Issuance) — M2.5.

Escopo: `POST /nonce`. Não confundir com o token endpoint (`token_endpoint.py`):
o token endpoint troca um pre-authorized_code por um access_token (prova de
que o holder tem direito a pedir a credencial); o nonce endpoint emite um
`c_nonce` — um desafio de frescor ("freshness") que o holder vai usar no M3
para construir o JWT de proof-of-possession de chave (key proof) dentro do
credential request. São duas coisas diferentes: access_token prova
autorização, c_nonce (dentro do JWT proof, no claim `nonce`) prova que o
holder controla a chave privada correspondente à chave pública que ele quer
amarrar na credencial, e que essa prova foi gerada agora — não replay de uma
assinatura antiga.

Por isso o endpoint é público, sem access_token: ele precisa poder ser
chamado ANTES do holder ter necessariamente um access_token em mãos válido
para aquele contexto (a spec permite pedir um nonce novo a qualquer momento,
inclusive entre tentativas), e o c_nonce sozinho não dá acesso a nada — só
vira útil quando embutido dentro de um JWT assinado pela chave do holder e
enviado ao credential endpoint (que aí sim é protegido por access_token).

Nota sobre a spec (grau de confiança): a versão final da especificação
OpenID4VCI 1.0 introduziu um "Nonce Endpoint" dedicado (seção correspondente
do documento), substituindo o mecanismo de drafts anteriores em que o
`c_nonce` (e um possível `c_nonce_expires_in`) apareciam dentro da resposta
do token endpoint ou do corpo de erro `invalid_proof` do credential endpoint.
Estou razoavelmente confiante de que a resposta do Nonce Endpoint define
apenas `c_nonce` como parâmetro (sem `c_nonce_expires_in` no corpo desta
resposta específica) — mas não tenho certeza absoluta de que a spec proíbe
ou nunca define um parâmetro de expiração aqui; se isso for crítico, vale
confirmar contra o texto oficial antes de expor este comportamento em
produção. Por segurança e para não inventar campo não confirmado, expomos
só `{"c_nonce": "..."}`.

Duas simplificações deliberadas de MVP, análogas ao token_endpoint.py:
1. O `c_nonce` é armazenado em um dict em memória do processo (_issued_nonces),
   não em Redis/DB — reinicia o processo, perde os nonces pendentes.
2. Tratamos o c_nonce como uso único (single-use): a validação
   (`is_valid_c_nonce`) já CONSOME o nonce por padrão. Isso é uma decisão de
   segurança deliberada, não um requisito textual que eu possa citar
   literalmente da spec: um nonce de frescor reutilizável degrada a garantia
   de "prova gerada agora" que ele existe para dar (um proof JWT antigo,
   capturado, poderia ser reenviado enquanto o nonce ainda não expirou). O
   padrão análogo mais próximo e bem documentado é o nonce do DPoP
   (RFC 9449), que é explicitamente de uso único. Aplicamos a mesma lógica
   aqui por prudência; se a leitura da spec OID4VCI mostrar que ela permite
   reuso até expirar, isso pode ser relaxado depois.
"""
import secrets
import time

from fastapi import APIRouter

from config import C_NONCE_TTL_SECONDS

# Store de nonces emitidos em memória: c_nonce -> {"expires_at": epoch}.
# MVP: sem persistência, sem associação a um holder/sessão específica — a
# spec não exige isso para o nonce endpoint (ele é público e stateless do
# ponto de vista de quem o holder é).
_issued_nonces: dict[str, dict] = {}

router = APIRouter()


def is_valid_c_nonce(c_nonce: str, consume: bool = True) -> bool:
    """
    Helper para uso futuro pelo credential endpoint (M3): confere se o
    c_nonce (extraído do claim `nonce` dentro do JWT de key proof) foi
    realmente emitido por este endpoint e ainda não expirou.

    `consume=True` (default): trata o nonce como uso único, removendo-o do
    store após uma validação bem-sucedida — uma segunda tentativa de usar o
    mesmo c_nonce falha, mesmo antes de expirar. Ver nota de segurança no
    docstring do módulo sobre por que essa é uma escolha deliberada de MVP,
    não uma citação literal da spec.
    """
    entry = _issued_nonces.get(c_nonce)
    if entry is None:
        return False
    valid = time.time() < entry["expires_at"]
    if consume:
        # Remove independentemente do resultado: um nonce expirado também
        # não deve poder ser "tentado de novo" depois — ele já morreu.
        _issued_nonces.pop(c_nonce, None)
    return valid


@router.post("/nonce")
async def nonce_endpoint():
    """
    Sem corpo de requisição, sem autenticação — ao contrário do credential
    endpoint (M3), que vai exigir o access_token emitido por /token no
    header Authorization. O nonce endpoint é deliberadamente público: ele só
    entrega um desafio opaco, não libera nenhum dado nem emite credencial.
    """
    c_nonce = secrets.token_urlsafe(32)
    _issued_nonces[c_nonce] = {
        "expires_at": time.time() + C_NONCE_TTL_SECONDS,
    }

    return {"c_nonce": c_nonce}
