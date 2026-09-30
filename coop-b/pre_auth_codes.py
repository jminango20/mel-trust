"""
Store em memória de `pre_authorized_code` único e de uso único, por lote.

Substitui, para lotes cadastrados via POST /lote, o código FIXO/genérico
(config.PRE_AUTHORIZED_CODE) que token_endpoint.py usava até aqui — ver
docs/known-issues.md, "pre-authorized_code fixo por nó, sem amarração à
entrega/lote". Mesmo padrão de armazenamento já usado em token_endpoint.py
(_issued_tokens) e nonce_endpoint.py: dict em memória do processo, sem
persistência — reinicia o processo, perde todos os códigos emitidos
(aceitável pro MVP local).

O valor FIXO de config.PRE_AUTHORIZED_CODE continua aceito por
token_endpoint.py como bypass de dev/teste (nunca consumido) — necessário
porque lotes inseridos fora de POST /lote (fixture .ttl via pipeline.py, ex.
MS-2026-042) nunca passam por este store e não têm nenhum código dinâmico
associado. Ver docstring de token_endpoint.py.
"""
import secrets

# code -> {"batch_number": str, "usado": bool}
_codes: dict[str, dict] = {}

# batch_number -> code atualmente válido para esse lote (o mais recente
# gerado/reemitido). Um batchNumber só tem UM código "ativo" por vez.
_batch_to_code: dict[str, str] = {}


def generate_for_batch(batch_number: str) -> str:
    """Gera um código novo, único (secrets.token_urlsafe — aleatoriedade
    criptográfica, não sequencial/previsível), associa ao batch_number e
    marca como não usado. Vira o código ativo desse lote."""
    code = secrets.token_urlsafe(24)
    _codes[code] = {"batch_number": batch_number, "usado": False}
    _batch_to_code[batch_number] = code
    return code


def get_entry(code: str) -> dict | None:
    """Retorna {"batch_number", "usado"} do código, ou None se não existe
    (nunca foi gerado, ou foi invalidado por uma reemissão)."""
    return _codes.get(code)


def mark_used(code: str) -> None:
    if code in _codes:
        _codes[code]["usado"] = True


def reissue_for_batch(batch_number: str) -> tuple[str | None, str | None]:
    """Invalida o código atual do lote e gera um novo.

    Retorna (novo_codigo, erro) — exatamente um dos dois é não-None:
    - erro="not_found": este lote nunca teve um código gerado por este
      processo (não foi cadastrado via POST /lote nesta execução, ou o
      processo reiniciou desde então).
    - erro="already_used": o código atual já foi trocado por um
      access_token (credencial já em processo de emissão/emitida) — não
      reemite, pra não invalidar um fluxo já em andamento.
    """
    current_code = _batch_to_code.get(batch_number)
    if current_code is None:
        return None, "not_found"

    entry = _codes.get(current_code)
    if entry is not None and entry["usado"]:
        return None, "already_used"

    # Invalida o antigo (remove do store — get_entry passa a devolver None
    # pra ele) e gera o novo.
    _codes.pop(current_code, None)
    novo_codigo = generate_for_batch(batch_number)
    return novo_codigo, None
