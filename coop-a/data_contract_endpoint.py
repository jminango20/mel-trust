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

Datas: `validFrom`/`validUntil` devem ser ISO 8601 COM hora e fuso (`Z` ou `±hh:mm`). Data sem
hora ou sem fuso -> 400 invalid_request com mensagem fixa (DATE_FORMAT_MSG). Motivo: a janela
é comparada com o timestamp (UTC) do bloco do depósito; sem fuso a data seria ambígua. (O
contractRef continua hasheando a string recebida; o Fuseki guarda o xsd:dateTime normalizado
pelo rdflib, ex. `Z` vira `+00:00`.)

GET /data-contract/{contractRef}/payment — status do pagamento, SÓ LEITURA, SEM CHAVE: lê
`depositos(ref)` do VendaDados por eth_call (mesma configuração VENDA_DADOS_* de delivery.py)
e os termos (buyerWallet, priceUnits, validFrom, validUntil) do Fuseki. 200
{"contractRef", "buyerWallet", "status"} (buyerWallet = o dos termos, em todos os status); 404 {"error": "not_found"} (nenhum contrato com essa ref, ou ref
mal formada); 503 {"error": "service_unavailable"} com mensagem FIXA (Fuseki ou node fora do ar;
o texto da exceção só vai ao log). Precedência do status (`payment_status`):
  estado 0 -> not_paid; comprador/valor != buyerWallet/priceUnits -> mismatch;
  estado 2 -> released; estado 3 -> refunded; estado 1 fora da janela -> outside_window;
  estado 1 dentro -> paid.
Janela: validFrom <= depositadoEm <= validUntil (inclusivos), tudo em UTC: validFrom/validUntil
(com o fuso guardado) são convertidos com astimezone(UTC) e comparados com o timestamp do BLOCO
do depósito, não com a hora da leitura. Data guardada sem fuso (só dado legado, o POST já não
a aceita) é lida como UTC.
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD
from web3 import Web3

import delivery
from config import FUSEKI_ADMIN_PASSWORD, FUSEKI_ADMIN_USER, FUSEKI_URL
from shacl_pipeline import extract_violations, format_violations, insert_into_fuseki, run_shacl

logger = logging.getLogger(__name__)

router = APIRouter()

BEE = Namespace("https://w3id.org/agrisemantics/AGRITRUST/bee-extension#")
AGRITRUST = Namespace("https://w3id.org/agrisemantics/AGRITRUST#")

_TERM_KEYS = ("consumer", "purpose", "priceUnits", "buyerWallet", "validFrom", "validUntil")
_STRING_KEYS = ("consumer", "purpose", "validFrom", "validUntil")

# ISO 8601 com hora E fuso (Z ou ±hh:mm). Mensagem FIXA: diz o formato aceito, sem eco do valor.
_DATETIME_WITH_ZONE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DATE_FORMAT_MSG = "validFrom e validUntil devem ser ISO 8601 com hora e fuso (Z ou ±hh:mm), ex. 2026-11-01T00:00:00Z"

_CONTRACT_REF_RE = re.compile(r"^0x[0-9a-f]{64}$")
SERVICE_UNAVAILABLE_MSG = "leitura do pagamento indisponível no momento; tente novamente mais tarde"
_ESTADO_NENHUM, _ESTADO_DEPOSITADO, _ESTADO_LIBERADO, _ESTADO_REEMBOLSADO = 0, 1, 2, 3
_DEPOSITOS_ABI = [{"type": "function", "name": "depositos", "stateMutability": "view",
                   "inputs": [{"name": "", "type": "bytes32"}],
                   "outputs": [{"name": "comprador", "type": "address"}, {"name": "valor", "type": "uint256"},
                               {"name": "depositadoEm", "type": "uint64"}, {"name": "estado", "type": "uint8"}]}]


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
            if not _DATETIME_WITH_ZONE_RE.match(terms[key]):
                return DATE_FORMAT_MSG
            try:
                datetime.fromisoformat(terms[key].replace("Z", "+00:00"))
            except ValueError:
                return DATE_FORMAT_MSG
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


# --- Leitura do pagamento ---------------------------------------------------------------

def _to_utc(value: str) -> datetime:
    """xsd:dateTime guardado no Fuseki -> datetime em UTC. Sem fuso (legado) = UTC.
    Ex.: 2026-11-01T00:00:00+02:00 -> 2026-10-31T22:00:00+00:00."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def payment_status(estado: int, comprador: str, valor: int, deposited_at: int, terms: dict) -> str:
    """Precedência do status (ver docstring do módulo). `terms` vem do Fuseki."""
    if estado == _ESTADO_NENHUM:
        return "not_paid"
    if comprador.lower() != terms["buyerWallet"].lower() or valor != terms["priceUnits"]:
        return "mismatch"
    if estado == _ESTADO_LIBERADO:
        return "released"
    if estado == _ESTADO_REEMBOLSADO:
        return "refunded"
    deposited = datetime.fromtimestamp(deposited_at, tz=timezone.utc)
    if not (_to_utc(terms["validFrom"]) <= deposited <= _to_utc(terms["validUntil"])):
        return "outside_window"
    return "paid"


def _load_terms(ref: str) -> dict | None:
    query = (f'PREFIX : <{BEE}>\nPREFIX agritrust: <{AGRITRUST}>\n'
             f'SELECT ?w ?p ?f ?u WHERE {{ ?c :contractRef "{ref}" ; :buyerWallet ?w ; :priceUnits ?p ; '
             f'agritrust:validFrom ?f ; agritrust:validUntil ?u }}')
    response = requests.post(
        f"{FUSEKI_URL}/query", data={"query": query}, headers={"Accept": "application/sparql-results+json"},
        auth=(FUSEKI_ADMIN_USER, FUSEKI_ADMIN_PASSWORD), timeout=15,
    )
    response.raise_for_status()
    rows = response.json()["results"]["bindings"]
    if not rows:
        return None
    r = rows[0]
    return {"buyerWallet": r["w"]["value"], "priceUnits": int(r["p"]["value"]),
            "validFrom": r["f"]["value"], "validUntil": r["u"]["value"]}


def _read_deposit(ref: str) -> tuple[str, int, int, int]:
    """eth_call depositos(ref). SEM chave. Levanta em node/contrato indisponível."""
    rpc, chain_id, address = delivery.venda_dados_config()
    if not address:
        raise RuntimeError("VendaDados sem endereço configurado")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
    if w3.eth.chain_id != chain_id:
        raise RuntimeError(f"chainId {w3.eth.chain_id} em {rpc}, esperado {chain_id}")
    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=_DEPOSITOS_ABI)
    comprador, valor, depositado_em, estado = contract.functions.depositos(bytes.fromhex(ref[2:])).call()
    return comprador, valor, depositado_em, estado


@router.get("/data-contract/{contract_ref}/payment")
def get_data_contract_payment(contract_ref: str):
    if not _CONTRACT_REF_RE.match(contract_ref):
        return _error(404, "not_found", "contrato não encontrado")
    try:
        terms = _load_terms(contract_ref)
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.error("GET /data-contract/%s/payment: leitura do Fuseki falhou: %s", contract_ref, exc)
        return _error(503, "service_unavailable", SERVICE_UNAVAILABLE_MSG)
    if terms is None:
        return _error(404, "not_found", "contrato não encontrado")
    try:
        comprador, valor, depositado_em, estado = _read_deposit(contract_ref)
    except Exception as exc:  # noqa: BLE001 - node parado, RPC lento, revert, chainId errado: tudo vira 503 fixo
        logger.error("GET /data-contract/%s/payment: leitura do VendaDados falhou: %s", contract_ref, exc)
        return _error(503, "service_unavailable", SERVICE_UNAVAILABLE_MSG)
    return {"contractRef": contract_ref, "buyerWallet": terms["buyerWallet"],
            "status": payment_status(estado, comprador, valor, depositado_em, terms)}
