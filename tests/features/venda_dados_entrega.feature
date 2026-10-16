# language: pt
Funcionalidade: Registro da entrega no VendaDados (coop-a)
  Quando o lote tem quantidade, o coop-a registra a entrega no contrato VendaDados
  (cotas = mL para a carteira custodial do produtor). A marca de entrega fica num
  recurso separado no Fuseki (DeliveryTask), como a AnchoringTask.

  Cenário: Entrega com quantidade é registrada no VendaDados
    Dado um novo lote válido com quantityLiters 1.8 e um produtor novo
    Quando a cooperativa cadastra o lote via POST /lote
    Então a resposta traz deliveryStatus "registered" e anchoringStatus "anchored"
    E entregas do batchRef devolve a carteira custodial do produtor e 1800
    E o saldo de cotas da carteira custodial é 1800

  Cenário: Lote sem quantidade não registra entrega
    Dado um novo lote válido sem quantityLiters e um produtor novo
    Quando a cooperativa cadastra o lote via POST /lote
    Então a resposta traz deliveryStatus "skipped" e anchoringStatus "anchored"
    E entregas do batchRef devolve o endereço zero e 0

  @sem_besu
  Cenário: Entrega pendente é concluída por retry
    Dado um lote de 1800 mL no Fuseki do coop-a com entrega pendente
    Quando o retry de entrega do coop-a roda
    Então o resultado do lote é "sent" e a tarefa de entrega vira "registered"
    E entregas do batchRef devolve a carteira custodial do produtor e 1800
    Quando o retry de entrega do coop-a roda de novo
    Então o lote não aparece no resultado e o saldo de cotas continua 1800

  @sem_besu
  Cenário: Recibo perdido é reconciliado e divergência nunca vira registered
    Dado um lote de 1800 mL no Fuseki do coop-a com entrega pendente
    E a entrega já está on-chain com o mesmo produtor e 1800 mL
    Quando o retry de entrega do coop-a roda
    Então o resultado do lote é "already_registered" sem transação e a tarefa vira "registered"
    E o saldo de cotas continua 1800
    Dado um lote de 1800 mL no Fuseki do coop-a com entrega pendente
    E a entrega já está on-chain com o mesmo produtor e 900 mL
    Quando o retry de entrega do coop-a roda
    Então o resultado do lote é "mismatch" e a tarefa continua "mismatch"
    Quando o retry de entrega do coop-a roda de novo
    Então o resultado do lote é "mismatch" e a tarefa continua "mismatch"
    E entregas do batchRef continua com 900 mL

  # Só roda com -m node_parado e o node da 8546 PARADO (o cenário confere que está).
  @node_parado
  Cenário: Com o node do VendaDados parado o lote continua ancorado e a entrega fica pendente
    Dado que o node do VendaDados está parado
    E um novo lote válido com quantityLiters 1.8 e um produtor novo
    Quando a cooperativa cadastra o lote via POST /lote
    Então a resposta traz deliveryStatus "pending" e anchoringStatus "anchored"
    E a tarefa de entrega do lote está "pending"
