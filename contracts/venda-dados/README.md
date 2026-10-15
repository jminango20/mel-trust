# venda-dados

Contrato `VendaDados` (escrow de venda com cotas por mL) e `MockUSDT` (só teste).

## Testes (rede simulada em memória)

    npm install
    npx hardhat test

## Node local (segunda rede)

A 8545 é o túnel da Besu; este node usa **127.0.0.1:8546**, chainId **31337**.

    npx hardhat node --port 8546

## Implantar

Em outro terminal, com o node no ar:

    npx hardhat compile
    node scripts/deploy-local.js

Cooperativa = endereço derivado de `coop-a/keys/coop-a-besu.key` (a chave não é
impressa), percentual da cooperativa 0, prazo de reembolso 30 dias. A conta recebe 100 ETH
de teste. O script imprime chainId e endereços. Recusa rodar se o chainId não for 31337.

Smoke: `node scripts/smoke.js <endereço do VendaDados>`.

## Aviso sobre a chave

A mesma chave do coop-a na Besu é reutilizada aqui **somente em redes de TESTE** (node
local, endereço público conhecido do Hardhat). Nunca use essa chave em rede com valor real.
