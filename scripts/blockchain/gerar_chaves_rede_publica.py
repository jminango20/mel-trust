"""Gera os dois pares secp256k1 da venda de dados em rede pública de teste: a cooperativa (implanta e
assina liberar/reembolsar no VendaDados) e um comprador de teste.

Não conecta a nenhuma rede. Layout de saída (relativo à raiz do repo; `--saida DIR` troca o diretório):
    coop-a/keys/venda-publica-cooperativa.key
    coop-a/keys/venda-publica-comprador.key

Formato do arquivo: igual ao dos outros .key do projeto (ver generate_node_accounts.py): chave privada em
hex, 64 caracteres, SEM prefixo "0x", sem newline. O .gitignore (`**/keys/*.key`) já cobre os dois.

Idempotência: arquivo existente NUNCA é sobrescrito; o script avisa e segue com o outro (perder a chave da
cooperativa trava o contrato implantado com ela, que a fixa como imutável).

Sigilo: a chave privada nunca é impressa; só o endereço (checksum) e o NOME do arquivo, nunca o caminho.
Uma chave igual a uma das chaves públicas das contas nº 0 e nº 1 do Hardhat (qualquer um as conhece) é
descartada e outra é gerada.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eth_account import Account

REPO_ROOT = Path(__file__).resolve().parents[2]
SAIDA_PADRAO = REPO_ROOT / "coop-a" / "keys"

PAPEIS = {
    "cooperativa": "venda-publica-cooperativa.key",
    "comprador": "venda-publica-comprador.key",
}

# Mesmas chaves públicas do Hardhat que o deploy-publico.js recusa.
CHAVES_PUBLICAS_HARDHAT = {
    "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
}


def _hex(account) -> str:
    h = account.key.hex()
    return h[2:] if h.startswith("0x") else h


def nova_conta():
    """Conta nova que não seja uma das chaves públicas do Hardhat."""
    while True:
        conta = Account.create()
        if _hex(conta) not in CHAVES_PUBLICAS_HARDHAT:
            return conta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--saida", type=Path, default=SAIDA_PADRAO, help="diretório dos .key (padrão: coop-a/keys)")
    saida = ap.parse_args(argv).saida

    enderecos: dict[str, str] = {}
    for papel, nome in PAPEIS.items():
        caminho = saida / nome
        if caminho.exists():
            try:
                conta = Account.from_key(caminho.read_text(encoding="utf-8").strip())
            except Exception:  # a mensagem do eth_account pode trazer o conteúdo: não repassar
                print(f"[{papel}] {nome} ja existe mas nao e uma chave valida; nao alterado.", file=sys.stderr)
                return 1
            print(f"[{papel}] {nome} ja existe, pulando (chave nao sobrescrita).")
            enderecos[papel] = conta.address
            continue
        conta = nova_conta()
        saida.mkdir(parents=True, exist_ok=True)
        with open(caminho, "x", encoding="utf-8") as f:  # "x": falha em vez de sobrescrever
            f.write(_hex(conta))
        print(f"[{papel}] chave gerada em {nome} (chave privada nao exibida).")
        enderecos[papel] = conta.address

    print()
    print("Enderecos publicos:")
    for papel, nome in PAPEIS.items():
        print(f"{papel:<12} {enderecos[papel]}  {nome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
