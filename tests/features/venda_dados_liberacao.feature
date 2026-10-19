# language: pt
Funcionalidade: Liberação do pagamento do Contrato de Dados (coop-a)
  Depois que o comprador deposita no VendaDados com a `ref` do Contrato de Dados, o coop-a (que
  guarda a chave da cooperativa) libera o pagamento: `liberar(ref)` assinado pela cooperativa,
  só se o status do pagamento for "paid" (mesma precedência do GET /data-contract/{ref}/payment).
  Rota proposta: POST /data-contract/<contractRef>/release. Respostas: 200 released (com txHash),
  200 already_released, 409 not_payable (com o status), 404 not_found, 503 service_unavailable.
  Cada cenário implanta um VendaDados próprio com parte da cooperativa de 10% (nenhum estado
  de outros testes entra na conta), usa um consumidor ConsumidorTeste-<12 hex> e um producerDID
  sintético novo. Precisa do node do VendaDados (8546); não usa a Besu. "Sem transação" se prova
  pelo nonce da conta da cooperativa antes e depois.

  @sem_besu
  Cenário: Depósito correto é liberado e o rateio fica com o produtor e a cooperativa
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 200 com status "released" e txHash
    E GET /data-contract/<contractRef>/payment responde "released"
    E o saldoTotalSacavel do produtor é igual ao rateio de 90% do priceUnits
    E o saldoSacavel da cooperativa é a parte de 10% do priceUnits

  @sem_besu
  Cenário: Sem depósito a liberação é recusada e nenhuma transação é enviada
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 409 com error "not_payable" e status "not_paid"
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: Depósito de outra carteira é recusado como mismatch e nenhuma transação é enviada
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    E outra carteira depositou o priceUnits exato com a ref do contrato
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 409 com error "not_payable" e status "mismatch"
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: Depósito de valor diferente é recusado como mismatch e nenhuma transação é enviada
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    E o comprador do contrato depositou um valor diferente do priceUnits com a ref do contrato
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 409 com error "not_payable" e status "mismatch"
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: Depósito com a janela toda no passado é recusado como outside_window sem transação
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela toda no passado
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 409 com error "not_payable" e status "outside_window"
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: Depósito com a janela toda no futuro é recusado como outside_window sem transação
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela toda no futuro
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 409 com error "not_payable" e status "outside_window"
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: Segunda chamada depois de liberar devolve already_released sem transação
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E o coop-a já recebeu POST /data-contract/<contractRef>/release com status "released"
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release de novo
    Então a resposta é 200 com status "already_released"
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: Depósito reembolsado pela cooperativa não pode ser liberado
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E a cooperativa reembolsou o depósito direto no contrato
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 409 com error "not_payable" e status "refunded"
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: contractRef inexistente devolve 404 not_found no corpo e nenhuma transação é enviada
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um contractRef bem formado que nenhum Contrato de Dados tem
    E o nonce da cooperativa foi anotado
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 404 com error "not_found" no corpo
    E o nonce da cooperativa não mudou

  @sem_besu
  Cenário: A resposta nunca contém a chave da cooperativa nem o caminho dela
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    E o coop-a recebe POST /data-contract/<contractRef>/release de novo
    Então nenhuma das respostas contém a chave da cooperativa em hex nem o caminho dela

  # Só roda com -m node_parado e o node da 8546 PARADO (o cenário confere que está).
  @sem_besu @node_parado
  Cenário: Com o node do VendaDados parado a liberação devolve 503 com mensagem fixa
    Dado que o node do VendaDados está parado
    E um Contrato de Dados criado por POST /data-contract com a janela válida agora
    Quando o coop-a recebe POST /data-contract/<contractRef>/release
    Então a resposta é 503 com mensagem fixa e sem texto de exceção
