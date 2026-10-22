"""
Relato (NÃO é teste, NÃO é coletado pelo pytest e nunca falha): em DEBUG, o sentinela do RPC aparece
em logs de bibliotecas (urllib3, web3, requests...)?

Uso (da raiz do repo, com um venv que tenha web3, requests, fastapi e python-dotenv):
    certifier/.venv/Scripts/python.exe tests/support/relato_logs_debug.py

Complementa tests/features/venda_dados_logs_sem_url.feature, que só vale para INFO ou acima (o padrão do
uvicorn). Para cada "rede" do feature, roda `delivery.deliver_on_post_lote` com um lote sintético e junta,
por nome de logger, quantos registros em DEBUG (ou acima) contêm o sentinela ou o host. Precisa do Fuseki
do coop-a; o node 8546 só para a rede "chainId errado". Apaga a DeliveryTask criada.
"""
import logging
import os
import shutil
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from load_node_module import load_node_module  # noqa: E402

SENTINELA = "CHAVE-SINTETICA-" + uuid.uuid4().hex
HOST = "rpc-inexistente.invalid"
REDES = {
    "url com chave no caminho": (f"https://{HOST}/v2/{SENTINELA}", 31337, HOST),
    "chave na query": (f"https://{HOST}/?apikey={SENTINELA}", 31337, HOST),
    "senha no usuário": (f"https://usuario:{SENTINELA}@{HOST}/", 31337, HOST),
    "chainId errado": (f"http://127.0.0.1:8546/?apikey={SENTINELA}", 11155111, "127.0.0.1:8546"),
}


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.registros = []

    def emit(self, record):
        self.registros.append((record.name, record.levelname, logging.Formatter("%(message)s").format(record)))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="relato-logs-"))
    os.environ.update(PRODUCERS_KEY_DIR=str(tmp / "producers"), VENDA_DADOS_ADDRESS="0x" + "11" * 20)
    try:
        delivery = load_node_module("coop-a", "delivery.py", "delivery_relato")
        raiz = logging.getLogger()
        coletor = Coletor()
        raiz.addHandler(coletor)
        raiz.setLevel(logging.DEBUG)
        for nome, (url, chain_id, host) in REDES.items():
            os.environ.update(VENDA_DADOS_RPC_URL=url, VENDA_DADOS_CHAIN_ID=str(chain_id))
            coletor.registros.clear()
            batch = f"MS-2026-ENTREGA-{uuid.uuid4().hex[:12]}"
            delivery.deliver_on_post_lote(batch, f"did:key:zRelato{uuid.uuid4().hex[:8]}", 1000)
            delivery._fuseki("update", {"update": f"DELETE WHERE {{ <{delivery._task_uri(batch)}> ?p ?o }}"})
            achados = defaultdict(lambda: [0, 0, set()])  # logger -> [com sentinela, só com host, níveis]
            for logger_name, nivel, msg in coletor.registros:
                if SENTINELA.lower() in msg.lower():
                    achados[logger_name][0] += 1
                    achados[logger_name][2].add(nivel)
                elif host.lower() in msg.lower():
                    achados[logger_name][1] += 1
                    achados[logger_name][2].add(nivel)
            print(f"\n[{nome}] {len(coletor.registros)} registros em DEBUG ou acima")
            if not achados:
                print("  (nenhum logger carrega o sentinela nem o host)")
            for logger_name, (com_sent, so_host, niveis) in sorted(achados.items()):
                print(f"  {logger_name}: sentinela em {com_sent}, só o host em {so_host} (níveis: {', '.join(sorted(niveis))})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
