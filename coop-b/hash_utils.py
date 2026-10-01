"""
Serialização determinística de grafo RDF para cálculo de hash de ancoragem
on-chain (usado por `lote_endpoint.py` antes de chamar `anchoring.anchor_batch`).

ACHADO REAL (sessão de integração M8/M9, confirmado empiricamente): o mesmo
grafo `rdflib.Graph`, serializado com `graph.serialize(format="nt")` em
processos Python DIFERENTES (ex.: o processo do coop-a ao ancorar vs. um
processo do verifier-demo tentando reconstruir o hash via CONSTRUCT), produz
STRINGS DIFERENTES — a ordem das linhas N-Triples não é estável entre
processos (rdflib não documenta nenhuma garantia de ordem de serialização;
na prática depende de iteração sobre estruturas internas cuja ordem varia
por processo). Serializando o MESMO grafo 3x em processos separados nesta
sessão, saíram 3 ordens de linha diferentes — ver
docs/notas-arquitetura/m8-ancoragem-blockchain.md.

Consequência prática: o hash ancorado via `Web3.keccak(g.serialize(format="nt"))`
simples NÃO era reconstruível por um verificador externo que monte o MESMO
conjunto de triplas via SPARQL CONSTRUCT em outro processo — o conjunto de
triplas seria idêntico, mas a ordem (e portanto o hash) não.

CORREÇÃO (mudança de comportamento deliberada, decisão do usuário): depois
de serializar como N-Triples, as LINHAS são ORDENADAS (`sorted()`) antes do
hash. Como N-Triples é uma linha por tripla (sem blank nodes neste
projeto — todas as URIs são determinísticas, ver `_slug` em
`lote_endpoint.py`), ordenar as linhas é equivalente a ordenar o conjunto de
triplas por sua representação textual: determinístico independente de
quem/quando serializa, desde que o CONJUNTO de triplas seja o mesmo.

Hashes ancorados ANTES desta correção (dados de demo gerados nesta sessão)
NÃO são mais reconstruíveis com esta função — aceito explicitamente pelo
usuário: são dados de teste/demo, não produção.

`verifier-demo/onchain_verification.py` replica EXATAMENTE esta mesma lógica
(duplicada, não importada — cada nó deste projeto já é um processo/venv
separado, mesmo padrão de duplicação já usado por `anchoring.py` em
coop-a/lab/certifier) para recalcular o hash a partir do CONSTRUCT e
comparar com `dataHashes[0]` lido do `AssetRegistry`. Qualquer mudança nesta
função precisa ser replicada lá também, ou a verificação de integridade
quebra silenciosamente (hashes deixam de bater por divergência de
normalização, não por dado realmente alterado).
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
