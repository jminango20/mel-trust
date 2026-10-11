"""
Helper de teste (NÃO é código de produção): roda `retry_anchoring()` de
`<nó>/lote_endpoint.py` em um processo próprio, com o interpretador (venv) e
o diretório do nó, injetando UM erro de rede na camada mais baixa: o
`Eth.send_raw_transaction` responde como o Besu responde a uma transação que
já está na fila: `Web3RPCError {'code': -32000, 'message': 'Known transaction'}`
(texto observado na chain real).

Todo o resto é real: Fuseki, CONSTRUCT, hash, `getAsset` on-chain,
`estimate_gas` e `anchor_batch` (incluindo o que ele loga). Só o envio da tx
é simulado, porque a janela "tx em voo" da chain real (bloco de 2s) é menor
que a latência do próprio retry antes de enviar (~2,5s medidos: Fuseki +
CONSTRUCT + getAsset), então não dá para reproduzi-la de forma determinística.

Uso:  <nó>/.venv/Scripts/python.exe tests/support/run_retry_injected.py [módulo]   (cwd = <nó>)
`módulo` = onde está `retry_anchoring()`; padrão `lote_endpoint` (coop-a/coop-b);
o lab usa `analise_endpoint`.
Saída: uma linha  RESULT_JSON:{"retry": <resposta>, "logs": [{"level","msg"}...]}
com todos os registros de log >= WARNING emitidos durante o retry.
"""
import importlib
import json
import logging
import sys

sys.path.insert(0, ".")  # cwd = diretório do nó


class _Grab(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        self.records.append({"level": record.levelname, "msg": record.getMessage()[:400]})


grab = _Grab()
logging.getLogger().addHandler(grab)

le = importlib.import_module(sys.argv[1] if len(sys.argv) > 1 else "lote_endpoint")
from web3.eth import Eth  # noqa: E402
from web3.exceptions import Web3RPCError  # noqa: E402


def _known_transaction(self, *args, **kwargs):
    raise Web3RPCError({"code": -32000, "message": "Known transaction"})


Eth.send_raw_transaction = _known_transaction

retry = le.retry_anchoring()
print("RESULT_JSON:" + json.dumps({"retry": retry, "logs": grab.records}))
