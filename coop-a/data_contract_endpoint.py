"""
POST /data-contract — registra os termos de venda de dados como um
agritrust:DataContract no Fuseki do coop-a.

Campos (JSON): `consumer` (nome; vira um recurso agritrust:DataConsumer com rdfs:label,
ligado por agritrust:hasPolicyAssignee), `purpose` (agritrust:purpose), `priceUnits`
(inteiro, menor unidade do USDT mock, 6 casas), `buyerWallet` (EIP-55), `validFrom` e
`validUntil` (ISO-8601, ex. 2026-11-01T00:00:00Z).

contractRef = keccak256 do JSON canônico dos termos recebidos: só as chaves acima que
vieram, ordenadas, separadores ("," ":"), UTF-8 sem escape, datas como recebidas. Mesmo
conteúdo, mesmo contractRef. É o bytes32 que o VendaDados usa como `ref`. A URI do
contrato é derivada dele (`:DataContract_<hex>`), então repetir o POST nunca duplica.

Mesma divisão de trabalho de POST /lote: Python valida só FORMA (JSON, tipos, carteira
EIP-55, datas parseáveis -> 400 invalid_request). As REGRAS DE NEGÓCIO (preço presente e
>= 1, validFrom < validUntil, consumidor presente) são do SHACL
(`DataContractShape`), mesmo gate de POST /lote -> 400 shacl_violation, nada inserido.

Respostas: 201 {"contractRef"}; 409 {"error": "already_exists", "contractRef"}.
Ordem: forma -> contractRef -> já existe? (409) -> SHACL (400) -> insere (201).
"""
import hashlib
import json
import logging
from datetime import datetime

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD
from web3 import Web3

from config import FUSEKI_ADMIN_PASSWORD, FUSEKI_ADMIN_USER, FUSEKI_URL
from shacl_pipeline import extract_violations, format_violations, insert_into_fuseki, run_shacl

logger = logging.getLogger(__name__)

router = APIRouter()

BEE = Namespace("https://w3id.org/agrisemantics/AGRITRUST/bee-extension#")
AGRITRUST = Namespace("https://w3id.org/agrisemantics/AGRITRUST#")

_TERM_KEYS = ("consumer", "purpose", "priceUnits", "buyerWallet", "validFrom", "validUntil")
_STRING_KEYS = ("consumer", "purpose", "validFrom", "validUntil")


def _error(status_code: int, error: str, description: str, extra: dict | None = None) -> JSONResponse:
    body = {"error": error, "error_description": description}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _check_form(terms: dict) -> str | None:
    """Só FORMA. Devolve a descrição do problema, ou None. Valor de negócio (preço 0 ou
    negativo, datas invertidas, campo ausente) passa daqui e o SHACL decide."""
    for key in _STRING_KEYS:
        if key in terms and not isinstance(terms[key], str):
            return f"{key} deve ser string"
    if "priceUnits" in terms and (isinstance(terms["priceUnits"], bool) or not isinstance(terms["priceUnits"], int)):
        return "priceUnits deve ser um número inteiro"
    wallet = terms.get("buyerWallet")
    if not isinstance(wallet, str) or not Web3.is_checksum_address(wallet):
        return "buyerWallet ausente ou fora do formato EIP-55 (endereço com checksum)"
    for key in ("validFrom", "validUntil"):
        if key in terms:
            try:
                datetime.fromisoformat(terms[key].replace("Z", "+00:00"))
            except ValueError:
                return f"{key} não é uma data ISO-8601"
    return None


def contract_ref(terms: dict) -> str:
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "0x" + Web3.keccak(text=canonical).hex().removeprefix("0x")


def _already_exists(ref: str) -> bool:
    response = requests.post(
        f"{FUSEKI_URL}/query",
        data={"query": f'PREFIX : <{BEE}>\nASK {{ ?c :contractRef "{ref}" }}'},
        headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD),
        timeout=15,
    )
    response.raise_for_status()
    return bool(response.json()["boolean"])


def _build_graph(terms: dict, ref: str) -> Graph:
    g = Graph()
    contract = BEE[f"DataContract_{ref[2:]}"]
    g.add((contract, RDF.type, AGRITRUST.DataContract))
    g.add((contract, BEE.contractRef, Literal(ref)))
    g.add((contract, BEE.buyerWallet, Literal(terms["buyerWallet"])))
    if "priceUnits" in terms:
        g.add((contract, BEE.priceUnits, Literal(terms["priceUnits"], datatype=XSD.integer)))
    if "purpose" in terms:
        g.add((contract, AGRITRUST.purpose, Literal(terms["purpose"])))
    for key in ("validFrom", "validUntil"):
        if key in terms:
            g.add((contract, AGRITRUST[key], Literal(terms[key], datatype=XSD.dateTime)))
    if "consumer" in terms:
        consumer = BEE[f"Consumidor_{hashlib.sha256(terms['consumer'].encode('utf-8')).hexdigest()[:16]}"]
        g.add((consumer, RDF.type, AGRITRUST.DataConsumer))
        g.add((consumer, RDFS.label, Literal(terms["consumer"])))
        g.add((contract, AGRITRUST.hasPolicyAssignee, consumer))
    return g


@router.post("/data-contract")
async def post_data_contract(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "invalid_request", "corpo deve ser JSON")
    if not isinstance(body, dict):
        return _error(400, "invalid_request", "corpo deve ser um objeto JSON")

    terms = {k: body[k] for k in _TERM_KEYS if k in body}
    problem = _check_form(terms)
    if problem:
        return _error(400, "invalid_request", problem)

    ref = contract_ref(terms)
    try:
        if _already_exists(ref):
            return _error(409, "already_exists", "contrato com os mesmos termos já existe", {"contractRef": ref})
    except requests.RequestException as exc:
        logger.error("POST /data-contract: consulta ao Fuseki falhou: %s", exc)
        return _error(502, "server_error", "falha ao consultar o Fuseki")

    graph = _build_graph(terms, ref)
    conforms, results_graph, _ = run_shacl(graph)
    if not conforms:
        violations = extract_violations(results_graph)
        return _error(400, "shacl_violation", "; ".join(format_violations(violations)), {"violations": violations})

    try:
        insert_into_fuseki(graph)
    except requests.RequestException as exc:
        logger.error("POST /data-contract: insert no Fuseki falhou: %s", exc)
        return _error(502, "server_error", "falha ao gravar no Fuseki")
    return JSONResponse(status_code=201, content={"contractRef": ref})
