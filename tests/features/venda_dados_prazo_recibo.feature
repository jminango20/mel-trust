# language: pt
# RED: o prazo do recibo é fixo (RECEIPT_TIMEOUT_S = 60 em coop-a/delivery.py) e, quando o recibo não vem
# a tempo, a liberação e o saque respondem 503 mesmo com a transação já enviada (e talvez minerada). Decisão
# proposta: RECEIPT_TIMEOUT_S e RECEIPT_POLL_S configuráveis (lidos a cada chamada; inválido cai no padrão) e,
# se a transação foi ENVIADA e o recibo não veio (web3.exceptions.TimeExhausted), 202 com o txHash em vez de 503.
#
# Em processo (tests/support/load_node_module.py carrega coop-a/release_endpoint.py, que importa delivery,
# data_contract_endpoint e withdraw_endpoint), com o node 8546, o Fuseki do coop-a e o coop-a (8001, só para
# criar o Contrato de Dados). Cada cenário implanta um MockUSDT + VendaDados NOVOS no node (sem tocar no
# deployments/local.json) e gera as chaves da cooperativa e do produtor num diretório temporário.
# A espera do recibo é observada (e, nos cenários de timeout, trocada) em web3.eth.Eth.wait_for_transaction_receipt.
# O RPC configurado leva um sentinela na query para provar que a resposta e os logs não o repetem.

Funcionalidade: Prazo do recibo configurável e 202 com txHash quando o recibo não veio

  @sem_besu
  Esquema do Cenário: RECEIPT_TIMEOUT_S e RECEIPT_POLL_S configuram a espera do recibo
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E RECEIPT_TIMEOUT_S "<timeout>" e RECEIPT_POLL_S "<poll>"
    Quando a entrega do lote é registrada no VendaDados
    Então wait_for_transaction_receipt recebeu o prazo <prazo> e o intervalo <intervalo>

    Exemplos:
      | timeout | poll    | prazo | intervalo |
      | ausente | ausente | 60    | 0.1       |
      | 90      | 2       | 90    | 2         |
      | 12.5    | 0.5     | 12.5  | 0.5       |
      | 90      | ausente | 90    | 0.1       |
      | ausente | 2       | 60    | 2         |
      | abc     | xyz     | 60    | 0.1       |
      | 0       | 0       | 60    | 0.1       |
      | -5      | -1      | 60    | 0.1       |
      | 5       | 10      | 60    | 0.1       |
      | nan     | inf     | 60    | 0.1       |

  @sem_besu
  Cenário: As variáveis são lidas a cada chamada, sem reiniciar
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E RECEIPT_TIMEOUT_S "30" e RECEIPT_POLL_S "1"
    Quando a entrega do lote é registrada no VendaDados
    E RECEIPT_TIMEOUT_S passa a "45" e RECEIPT_POLL_S a "3"
    E uma segunda entrega do lote é registrada
    Então a primeira espera recebeu o prazo 30 e o intervalo 1 e a segunda o prazo 45 e o intervalo 3

  @sem_besu
  Cenário: O prazo configurado chega à liberação
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um Contrato de Dados pago e com entrega registrada
    E RECEIPT_TIMEOUT_S "90" e RECEIPT_POLL_S "2"
    Quando a liberação do contrato é executada
    Então a resposta é 200 com status "released"
    E wait_for_transaction_receipt recebeu o prazo 90 e o intervalo 2

  @sem_besu
  Cenário: O prazo configurado chega ao saque e à reposição de gás
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um produtor com saldo sacável e a carteira sem gás
    E RECEIPT_TIMEOUT_S "90" e RECEIPT_POLL_S "2"
    Quando o saque do produtor é executado
    Então a resposta é 200 com status "withdrawn"
    E houve 2 esperas de recibo, todas com o prazo 90 e o intervalo 2

  # --- 202: transação enviada, recibo não veio ---

  @sem_besu
  Cenário: Liberação enviada cujo recibo não veio vira 202 com o txHash
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um Contrato de Dados pago e com entrega registrada
    E a espera do recibo levanta TimeExhausted depois de a transação ser enviada de verdade
    Quando a liberação do contrato é executada
    Então a resposta é 202 com status "pending", operation "release" e o txHash
    E o txHash existe no node com status 1
    E a leitura do pagamento passa a "released"
    E uma segunda liberação responde 200 com status "already_released"
    E a resposta e os logs não contêm URL, chave, caminho nem o texto da exceção, e o log traz o tipo TimeExhausted

  @sem_besu
  Cenário: Saque enviado cujo recibo não veio vira 202 com o txHash
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um produtor com saldo sacável e a carteira com gás
    E a espera do recibo levanta TimeExhausted depois de a transação ser enviada de verdade
    Quando o saque do produtor é executado
    Então a resposta é 202 com status "pending", operation "withdraw" e o txHash
    E o txHash existe no node com status 1
    E o saldo de USDT da carteira custodial subiu o saldo sacável de antes
    E um segundo saque responde 200 com status "nothing_to_withdraw"
    E a resposta e os logs não contêm URL, chave, caminho nem o texto da exceção, e o log traz o tipo TimeExhausted

  @sem_besu
  Cenário: Timeout na reposição de gás do saque continua 503 e o sacar() não é enviado
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um produtor com saldo sacável e a carteira sem gás
    E a espera do recibo levanta TimeExhausted depois de a transação ser enviada de verdade
    Quando o saque do produtor é executado
    Então a resposta é 503 com a mensagem fixa do saque
    E o sacar() não foi enviado
    E a resposta e os logs não contêm URL, chave, caminho nem o texto da exceção, e o log traz o tipo TimeExhausted

  # --- Controles: o que já é 503 com mensagem fixa continua, e o sucesso normal continua 200 ---

  @sem_besu
  Esquema do Cenário: Controle: <operacao> com <problema> continua 503 com mensagem fixa
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E <preparo>
    E <problema>
    Quando <acao>
    Então a resposta é 503 com a mensagem fixa <mensagem>
    E a resposta e os logs não contêm URL, chave nem caminho

    Exemplos:
      | operacao  | problema                         | preparo                                                     | acao                             | mensagem   |
      | liberação | o node do VendaDados fora do ar  | um Contrato de Dados pago e com entrega registrada          | a liberação do contrato é executada | da liberação |
      | liberação | o chainId configurado como 11155111 | um Contrato de Dados pago e com entrega registrada       | a liberação do contrato é executada | da liberação |
      | saque     | o node do VendaDados fora do ar  | um produtor com saldo sacável e a carteira com gás          | o saque do produtor é executado  | do saque   |
      | saque     | o chainId configurado como 11155111 | um produtor com saldo sacável e a carteira com gás       | o saque do produtor é executado  | do saque   |

  @sem_besu
  Cenário: Controle: liberação que reverte (chave que não é a da cooperativa do contrato) continua 503 com mensagem fixa
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um Contrato de Dados pago e com entrega registrada
    E a chave da cooperativa configurada não é a do deploy
    Quando a liberação do contrato é executada
    Então a resposta é 503 com a mensagem fixa da liberação
    E a resposta e os logs não contêm URL, chave nem caminho

  @sem_besu
  Cenário: Controle: liberação normal continua 200
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um Contrato de Dados pago e com entrega registrada
    Quando a liberação do contrato é executada
    Então a resposta é 200 com status "released"

  @sem_besu
  Cenário: Controle: saque normal continua 200
    Dado um VendaDados novo no node local, com a cooperativa e um produtor sintético
    E um produtor com saldo sacável e a carteira com gás
    Quando o saque do produtor é executado
    Então a resposta é 200 com status "withdrawn"
