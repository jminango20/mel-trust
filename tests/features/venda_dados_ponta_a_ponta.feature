# language: pt
Funcionalidade: Venda de dados de ponta a ponta (coop-a, VendaDados, verifier-demo)
  Percorre o caminho todo com as peças reais: entrega dos lotes por POST /lote (ancora na Besu e
  registra a entrega no VendaDados), Contrato de Dados, depósito do comprador, acesso aos dados com
  desafio assinado, liberação do pagamento e saque de cada produtor. Precisa da Besu (túnel em
  127.0.0.1:8545) e do node do VendaDados (8546), além de coop-a, verifier-demo, Fuseki do coop-a e
  do coop-b/certifier (o GET /verify consulta a federação). Cada cenário implanta um VendaDados próprio
  com parte da cooperativa de 10% (o deployments/local.json aponta para ele durante o cenário) e usa
  um consumidor ConsumidorTeste-<12 hex>; o comprador é a conta nº 1 do Hardhat. Os lotes e os
  produtores são sintéticos (MS-2026-VENDA-*, producerDID novo por execução).

  Cenário: Venda concluída: acesso com o pagamento retido, liberação, repartição e saques
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E o produtor A entregou um lote sintético de 1.5 litros por POST /lote
    E o produtor B entregou um lote sintético de 0.5 litros por POST /lote
    E um Contrato de Dados de 10000000 unidades criado por POST /data-contract
    Quando o comprador pede e assina um desafio e pede o acesso ao lote do produtor A
    Então a resposta é 402 com error "payment_required" e status "not_paid"
    Quando o comprador deposita o preço exato com a ref do contrato
    Então GET /data-contract/<contractRef>/payment responde "paid"
    Quando o comprador pede e assina um desafio e pede o acesso ao lote do produtor A
    Então a resposta é 200
    E o corpo é igual ao do GET /verify do lote do produtor A
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 200 com status "released"
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 200 com status "already_released"
    E o saldoTotalSacavel do produtor A é 6750000 e o do produtor B é 2250000
    E o saldoTotalSacavel da cooperativa é 1000000 e a soma dos três é 10000000
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw do produtor A
    Então a resposta é 200 com status "withdrawn" e amount 6750000
    E o saldo de USDT da carteira custodial do produtor A sobe exatamente 6750000
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw do produtor B
    Então a resposta é 200 com status "withdrawn" e amount 2250000
    E o saldo de USDT da carteira custodial do produtor B sobe exatamente 2250000
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw do produtor A
    Então a resposta é 200 com status "nothing_to_withdraw"
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw do produtor B
    Então a resposta é 200 com status "nothing_to_withdraw"

  Cenário: Venda reembolsada: o acesso é negado e os produtores não têm o que sacar
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E o produtor A entregou um lote sintético de 1.5 litros por POST /lote
    E o produtor B entregou um lote sintético de 0.5 litros por POST /lote
    E um Contrato de Dados de 10000000 unidades criado por POST /data-contract
    E o comprador deposita o preço exato com a ref do contrato
    Quando a cooperativa reembolsa o depósito direto no contrato
    Então GET /data-contract/<contractRef>/payment responde "refunded"
    Quando o comprador pede e assina um desafio e pede o acesso ao lote do produtor A
    Então a resposta é 402 com error "payment_required" e status "refunded"
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw do produtor A
    Então a resposta é 200 com status "nothing_to_withdraw"
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw do produtor B
    Então a resposta é 200 com status "nothing_to_withdraw"
