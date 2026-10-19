# language: pt
Funcionalidade: Saque em nome do produtor (coop-a)
  O coop-a, que guarda a chave custodial do produtor, saca o rendimento do produtor no VendaDados
  (`sacar()` assinado pela carteira custodial, que paga ao `msg.sender`, ou seja, à própria
  carteira). A cooperativa só repõe o gás nativo; o USDT do saque vai para a carteira do produtor.
  Precisa do node do VendaDados (8546) com o deploy feito (e com `saldoTotalSacavel`); não usa a
  Besu. Cada cenário usa um producerDID sintético novo e apaga a chave custodial no fim.

  @sem_besu
  Cenário: Produtor com saldo saca e a carteira custodial recebe o USDT
    Dado um producerDID sintético com carteira custodial criada pelo helper do coop-a
    E o produtor tem uma entrega registrada no VendaDados e uma venda liberada
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw
    Então a resposta é 200 com status "withdrawn", amount igual ao saldo total sacável de antes e txHash
    E o saldo de USDT da carteira custodial sobe exatamente o amount
    E o saldoTotalSacavel da carteira custodial fica 0
    E o saldo de USDT da cooperativa não muda

  @sem_besu
  Cenário: Produtor com carteira e sem venda não envia nenhuma transação
    Dado um producerDID sintético com carteira custodial criada pelo helper do coop-a
    E os nonces da cooperativa e da carteira do produtor foram anotados
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw
    Então a resposta é 200 com status "nothing_to_withdraw"
    E o nonce da cooperativa e o da carteira do produtor não mudaram

  @sem_besu
  Cenário: Segundo saque logo depois do primeiro não tem nada a sacar
    Dado um producerDID sintético com carteira custodial criada pelo helper do coop-a
    E o produtor tem uma entrega registrada no VendaDados e uma venda liberada
    E o coop-a já recebeu POST /producers/<producerDID>/withdraw com status "withdrawn"
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw
    Então a resposta é 200 com status "nothing_to_withdraw"

  @sem_besu
  Cenário: DID sem carteira devolve 404 not_found e não cria chave
    Dado um producerDID sintético que não tem carteira custodial
    E a quantidade de chaves custodiais foi anotada
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw
    Então a resposta é 404 com error "not_found" no corpo
    E nenhum arquivo novo apareceu em coop-a/keys/producers/

  @sem_besu
  Cenário: A resposta nunca contém a chave privada nem o caminho da chave
    Dado um producerDID sintético com carteira custodial criada pelo helper do coop-a
    E o produtor tem uma entrega registrada no VendaDados e uma venda liberada
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw
    E o coop-a recebe POST /producers/<producerDID>/withdraw de novo
    Então nenhuma das respostas contém a chave privada em hex nem o caminho da chave

  @sem_besu
  Cenário: O saque do produtor não altera o saldo sacável da cooperativa (parte da cooperativa a 10%)
    Dado um deploy próprio do VendaDados com parte da cooperativa de 10% usado pelo coop-a nesse cenário
    E um producerDID sintético com carteira custodial criada pelo helper do coop-a
    E o produtor tem uma entrega registrada no VendaDados e uma venda liberada
    E o saldo sacável da cooperativa foi anotado
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw
    Então a resposta é 200 com status "withdrawn", amount igual ao saldo total sacável de antes e txHash
    E o saldoTotalSacavel da cooperativa não mudou e é maior que 0
    E o saldo de USDT da cooperativa não muda

  # Só roda com -m node_parado e o node da 8546 PARADO (o cenário confere que está).
  @sem_besu @node_parado
  Cenário: Com o node do VendaDados parado o saque devolve 503 com mensagem fixa
    Dado que o node do VendaDados está parado
    E um producerDID sintético com carteira custodial criada pelo helper do coop-a
    Quando o coop-a recebe POST /producers/<producerDID>/withdraw
    Então a resposta é 503 com mensagem fixa e sem texto de exceção
