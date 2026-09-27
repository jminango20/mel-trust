"""Gera pares de chave secp256k1 (endereco Ethereum) para os nos do MVP
(coop-a, coop-b, certifier) autenticarem na rede Besu "Semear" (chain ID 648).

Contexto: cada no do AgriTrust hoje so tem uma chave Ed25519 (<node>/keys/<node>.pem,
usada para assinar VC/did:web). Para interagir com os contratos on-chain
(AccessChannelManager.addChannelMember, TransactionOrchestrator, etc.) cada no
precisa alem disso de uma conta Ethereum (par secp256k1) para assinar transacoes.
Este script gera essa segunda chave, uma por no, sem tocar na Ed25519 existente.

Layout de saida (relativo a raiz do repo, nao hardcoded como caminho absoluto):
    <node>/keys/<node>-besu.key

Formato do arquivo: chave privada em hex, SEM prefixo "0x", uma linha, sem newline
final extra. Escolhido "sem 0x" para ficar no formato mais comum aceito diretamente
por `Account.from_key()`/`web3.py` (que aceita com ou sem prefixo, mas grava-se sem
para evitar ambiguidade entre os 3 arquivos).

Idempotencia (regra de seguranca): se o arquivo de chave do no ja existir, o script
NUNCA sobrescreve — apenas avisa e segue para o proximo no. Gerar uma chave nova por
cima destruiria o acesso a qualquer role/fundo ja associado ao endereco antigo (ex.
um addChannelMember ja concedido aquele endereco), mesmo estando em rede de teste.

Sigilo: a chave privada NUNCA e impressa no terminal, em nenhum momento, completa ou
truncada. Somente o endereco publico (account.address) aparece no output, na tabela
final que serve de input para o proximo passo (chamar addChannelMember para cada
endereco).
"""

from __future__ import annotations

import sys
from pathlib import Path

from eth_account import Account

# Raiz do repo = dois niveis acima deste arquivo (scripts/blockchain/../..).
REPO_ROOT = Path(__file__).resolve().parents[2]

NODES = ["coop-a", "coop-b", "certifier"]


def key_path(node: str) -> Path:
    return REPO_ROOT / node / "keys" / f"{node}-besu.key"


def load_existing_address(path: Path) -> str:
    """Le a chave privada do arquivo so para derivar o endereco publico.
    A chave privada em si nunca e impressa nem retornada para fora desta funcao.
    """
    private_key_hex = path.read_text(encoding="utf-8").strip()
    account = Account.from_key(private_key_hex)
    return account.address


def generate_and_save(node: str, path: Path) -> str:
    account = Account.create()
    path.parent.mkdir(parents=True, exist_ok=True)
    # hex() do eth_account inclui prefixo 0x; removemos para o formato escolhido.
    private_key_hex = account.key.hex()
    if private_key_hex.startswith("0x"):
        private_key_hex = private_key_hex[2:]
    path.write_text(private_key_hex, encoding="utf-8")
    return account.address


def main() -> int:
    results: dict[str, str] = {}

    for node in NODES:
        path = key_path(node)
        rel_path = path.relative_to(REPO_ROOT)
        if path.exists():
            print(f"[{node}] {rel_path} ja existe, pulando (chave nao sobrescrita).")
            results[node] = load_existing_address(path)
        else:
            address = generate_and_save(node, path)
            print(f"[{node}] chave gerada em {rel_path} (chave privada nao exibida).")
            results[node] = address

    print()
    print("Enderecos publicos por no (input para addChannelMember):")
    print(f"{'no':<12} {'endereco':<44}")
    for node in NODES:
        print(f"{node:<12} {results[node]:<44}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
