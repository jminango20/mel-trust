"""
Serialização determinística de grafo RDF para cálculo de hash de ancoragem
on-chain (usado por `analise_endpoint.py` antes de chamar
`anchoring.anchor_lab_analysis`).

MESMO módulo (duplicado, não importado — cada nó deste projeto é um
processo/venv separado) de `coop-a/hash_utils.py`/`coop-b/hash_utils.py`.
Ver a docstring completa lá para o achado real que motivou esta correção:
`rdflib.Graph.serialize(format="nt")` não é determinístico em ordem de
linha entre processos Python diferentes, o que tornava o hash ancorado não
reconstruível por um verificador externo (`verifier-demo`) que monte o
MESMO conjunto de triplas via SPARQL CONSTRUCT em outro processo.

CORREÇÃO: ordena as linhas N-Triples (`sorted()`) antes de calcular o
keccak256 — determinístico, pois aqui "mesmo conjunto de triplas" sempre
implica "mesmo conjunto de linhas N-Triples" (sem blank nodes neste
projeto — `analise_uri` é determinística por `batchNumber`, ver `_slug` em
`analise_endpoint.py`).

Hashes ancorados ANTES desta correção (dados de demo desta sessão) não são
mais reconstruíveis com esta função — aceito pelo usuário, dados de teste,
não produção.

`verifier-demo/onchain_verification.py` replica esta MESMA lógica para
recalcular o hash do laudo a partir do CONSTRUCT contra o Fuseki do lab e
comparar com `dataHashes[0]`.
"""
from web3 import Web3


def deterministic_nt_hash(graph) -> bytes:
    """Serializa `graph` (rdflib.Graph) como N-Triples, ORDENA as linhas
    (`sorted()`) e calcula keccak256 sobre a string resultante (join por
    `\\n`, com `\\n` final). Determinístico entre processos — ver docstring
    do módulo."""
    raw = graph.serialize(format="nt")
    lines = [line for line in raw.split("\n") if line.strip()]
    canonical = "\n".join(sorted(lines)) + "\n"
    return Web3.keccak(text=canonical)
