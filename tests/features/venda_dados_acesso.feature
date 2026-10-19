# language: pt
Funcionalidade: Acesso aos dados com desafio assinado (verifier-demo)
  O comprador prova que controla a carteira do Contrato de Dados assinando um desafio, e o
  verifier-demo só entrega os dados do lote se o pagamento do contrato estiver "paid" ou "released".
  Rotas propostas no verifier-demo:
    POST /data-access/challenge/<contractRef> => 200 {"nonce", "message"}; a mensagem contém o
      contractRef, o nonce e "verifier-demo"; o nonce é de uso único, com TTL, amarrado ao contractRef.
    GET /data-access/<batch_number> com X-Data-Contract, X-Challenge-Nonce, X-Challenge-Signature
      (EIP-191 da mensagem, pela carteira do comprador) e X-Access-Purpose.
  Ordem: cabeçalhos do desafio (401) -> nonce (401) -> assinatura vs buyerWallet (403) -> pagamento
  (402) -> propósito e dados, como o GET /verify. O GET /verify existente não muda.
  Cada cenário implanta um VendaDados próprio com parte da cooperativa de 10%, usa um consumidor
  ConsumidorTeste-<12 hex> e as contas nº 1 (comprador) e nº 2 (outra carteira) do Hardhat. O lote é o
  canônico MS-2026-042. Nenhum cenário depende da Besu (o GET /verify trata o RPC fora como "unavailable").

  @sem_besu
  Cenário: Desafio para contractRef inexistente devolve 404 not_found no corpo
    Dado um contractRef bem formado que nenhum Contrato de Dados tem
    Quando o verifier-demo recebe POST /data-access/challenge/<contractRef>
    Então a resposta é 404 com error "not_found" no corpo

  @sem_besu
  Cenário: Sem os cabeçalhos do desafio o acesso é recusado com 401
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 sem os cabeçalhos do desafio
    Então a resposta é 401

  @sem_besu
  Cenário: Pago, com assinatura do comprador e nonce válido, devolve o mesmo corpo do GET /verify
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E o comprador pediu e assinou o desafio do contrato
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 200
    E o corpo é igual ao do GET /verify de MS-2026-042

  @sem_besu
  Cenário: Contrato já liberado, mesmo comprador, ainda dá acesso
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E o coop-a liberou o pagamento por POST /data-contract/<contractRef>/release
    E o comprador pediu e assinou o desafio do contrato
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 200

  @sem_besu
  Cenário: Sem depósito o acesso é recusado com 402 not_paid
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um Contrato de Dados criado com a janela válida agora
    E o comprador pediu e assinou o desafio do contrato
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 402 com error "payment_required" e status "not_paid"

  @sem_besu
  Cenário: Depósito reembolsado é recusado com 402 refunded
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E a cooperativa reembolsou o depósito direto no contrato
    E o comprador pediu e assinou o desafio do contrato
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 402 com error "payment_required" e status "refunded"

  @sem_besu
  Cenário: Depósito com a janela toda no passado é recusado com 402 outside_window
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela toda no passado
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E o comprador pediu e assinou o desafio do contrato
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 402 com error "payment_required" e status "outside_window"

  @sem_besu
  Cenário: Depósito de outra carteira é recusado com 402 mismatch
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E outra carteira depositou o priceUnits exato com a ref do contrato
    E o comprador pediu e assinou o desafio do contrato
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 402 com error "payment_required" e status "mismatch"

  @sem_besu
  Cenário: Assinatura válida de outra carteira que não a do buyerWallet é recusada com 403
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E a outra carteira pediu e assinou o desafio do contrato
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 403

  @sem_besu
  Cenário: Nonce reutilizado é recusado com 401
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E o comprador pediu e assinou o desafio do contrato
    E o verifier-demo já recebeu GET /data-access/MS-2026-042 com o desafio assinado e respondeu 200
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado de novo
    Então a resposta é 401

  @sem_besu
  Cenário: Nonce emitido para outro contractRef é recusado com 401
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E um segundo Contrato de Dados criado, com o desafio dele pedido e assinado pelo comprador, mas apresentado com o contrato original
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 401

  @sem_besu
  Cenário: Nonce expirado é recusado com 401
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E um verifier-demo de teste com CHALLENGE_TTL_SECONDS=1 em outra porta
    E o comprador pediu e assinou o desafio do contrato
    E passaram 2 segundos
    Quando o verifier-demo recebe GET /data-access/MS-2026-042 com o desafio assinado
    Então a resposta é 401

  @sem_besu
  Cenário: Assinatura malformada é recusada com 401 e nunca com 500
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com uma entrega registrada direto no contrato
    E um Contrato de Dados criado com a janela válida agora
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    Quando o comprador apresenta, cada uma com um desafio novo, assinaturas não hexadecimais, curtas e longas demais
    Então todas as respostas são 401 e nenhuma é 500

  # Só roda com -m coop_a_parado e o coop-a (8001) PARADO (o cenário confere que está).
  @sem_besu @coop_a_parado
  Cenário: Com o coop-a fora do ar o desafio devolve 503 com mensagem fixa
    Dado que o coop-a está parado
    E um contractRef bem formado que nenhum Contrato de Dados tem
    Quando o verifier-demo recebe POST /data-access/challenge/<contractRef>
    Então a resposta é 503 com mensagem fixa e sem texto de exceção
