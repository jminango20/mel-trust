"""
Store em memória de `pre_authorized_code` único e de uso único, por lote —
réplica exata do padrão de coop-a/pre_auth_codes.py e coop-b/pre_auth_codes.py.

Diferença de contexto: em coop-a/coop-b o código é gerado em POST /lote (na
CRIAÇÃO do lote). Aqui é gerado em GET /lote/{batch_number}/pre-authorized-code
(pre_authorized_code_endpoint.py), só DEPOIS que a consulta federada + SHACL
regulatório (a mesma lógica de credential_endpoint.py) confirma
conforms=True — ou seja, só existe código pra lote que o certifier já
decidiu que aprovaria. Ver tests/features/holder_certificacao.feature.

O valor FIXO de config.PRE_AUTHORIZED_CODE continua aceito por
token_endpoint.py como bypass de dev/teste (nunca consumido) — mesma
justificativa de coop-a: testes/fluxos que não passam por este endpoint
novo continuam funcionando sem depender dele.
"""
import secrets

# code -> {"batch_number": str, "usado": bool}
_codes: dict[str, dict] = {}

# batch_number -> code atualmente válido para esse lote (o mais recente
# gerado). Um batchNumber só tem UM código "ativo" por vez.
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
    (nunca foi gerado, ou foi sobrescrito por uma geração posterior pro
    mesmo lote)."""
    return _codes.get(code)


def mark_used(code: str) -> None:
    if code in _codes:
        _codes[code]["usado"] = True
