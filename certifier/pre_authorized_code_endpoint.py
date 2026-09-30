"""
GET /lote/{batch_number}/pre-authorized-code — fecha o gap descrito em
tests/features/holder_certificacao.feature: antes desta entrega, o
certifier só tinha um pre_authorized_code FIXO/genérico
(config.PRE_AUTHORIZED_CODE), igual pra qualquer lote — sem amarração entre
"este lote foi aprovado pelo certifier" e "este código dá acesso à VC de
certificação DESTE lote".

Reusa (não duplica) a MESMA consulta federada + validação SHACL
regulatória que credential_endpoint.py já roda internamente: importa
diretamente `_query_federated`/`_monta_grafo_efemero`/`_run_regulatory_shacl`/
`_extract_violations` de lá. Se algum dia essa lógica mudar (nova shape,
novo campo federado), este endpoint muda junto automaticamente — não há
uma segunda cópia pra esquecer de atualizar.

Contrato:
- 200: lote aprovado (conforms=True) — gera e retorna um
  pre_authorized_code novo, único, de uso único, vinculado a este
  batch_number (mesmo store de coop-a/coop-b, ver pre_auth_codes.py).
- 404: lote não encontrado em coop-a/coop-b, ou sem nenhuma análise do lab
  ainda — mesmo critério/mensagem de credential_endpoint.py.
- 409: lote encontrado e analisado, mas reprovado no SHACL regulatório
  (conforms=False) — NENHUM código é gerado pra lote que não seria
  certificado. Corpo inclui as violações, mesmo formato de
  credential_endpoint.py.
- 502: falha de infraestrutura ao consultar o Fuseki (mesmo critério de
  credential_endpoint.py).
"""
import requests
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from credential_endpoint import (
    _extract_violations,
    _monta_grafo_efemero,
    _query_federated,
    _run_regulatory_shacl,
)
from pre_auth_codes import generate_for_batch

router = APIRouter()


@router.get("/lote/{batch_number}/pre-authorized-code")
async def pre_authorized_code_endpoint(batch_number: str):
    try:
        row = _query_federated(batch_number)
    except requests.RequestException as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": "server_error",
                "error_description": f"Falha ao consultar o Fuseki: {exc}",
            },
        )

    if row is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "invalid_request",
                "error_description": (
                    f"Lote {batch_number!r} não encontrado em coop-a/coop-b, ou "
                    f"sem nenhuma análise registrada no lab (POST /analise) ainda."
                ),
            },
        )

    conforms, results_graph = _run_regulatory_shacl(_monta_grafo_efemero(batch_number, row))
    if not conforms:
        return JSONResponse(
            status_code=409,
            content={
                "error": "shacl_violation",
                "error_description": (
                    "Dado do lote não passou na validação SHACL regulatória. "
                    "Nenhum pre_authorized_code foi gerado — este lote não seria "
                    "certificado."
                ),
                "violations": _extract_violations(results_graph),
            },
        )

    code = generate_for_batch(batch_number)
    return {"pre_authorized_code": code, "batch_number": batch_number}
