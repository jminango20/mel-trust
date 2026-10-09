# language: pt
# Cada cenário roda para coop-a e coop-b (fixture `node` parametrizada nos step defs).
Funcionalidade: Ancoragem pendente é concluída por retry (outbox), sem reenviar transação já feita

  Cenário: Ancoragem pendente é concluída por retry, sem reenviar transação já feita
    Dado um lote cadastrado no coop-a cuja ancoragem está marcada como pendente e que ainda NÃO existe on-chain
    Quando o retry de ancoragem roda
    Então a transação é enviada, o lote passa a "anchored" e o txHash é registrado

    Dado um lote cuja transação já foi confirmada on-chain, mas que continua marcado como pendente (recibo perdido)
    Quando o retry de ancoragem roda
    Então nenhuma transação é reenviada (o retry consulta getAsset primeiro, vê que o hash já bate e só marca como "anchored")

    Quando o retry roda uma segunda vez
    Então não há nada a processar e nada é reenviado

  Cenário: Hash divergente on-chain é reportado como mismatch e nunca vira anchored
    Dado um lote ancorado on-chain cujo conteúdo no Fuseki foi alterado depois da ancoragem, com a marca pendente
    Quando o retry de ancoragem roda
    Então o resultado do lote é "mismatch", a tarefa não vira "anchored" e nenhuma transação é enviada

    Quando o retry roda novamente
    Então o lote continua reportado como "mismatch", a tarefa segue "mismatch" e nada é reenviado

  Cenário: Transação em voo não é falha: o retry responde in_flight e conclui na passada seguinte
    Dado uma tarefa pending cuja transação está em voo (o envio responde Known transaction)
    Quando o retry de ancoragem roda com o envio respondendo Known transaction
    Então a resposta é outcome in_flight, nenhum ERROR é logado e a tarefa continua pending

    Quando a transação em voo é minerada e o retry roda novamente
    Então a tarefa vira "anchored" sem reenviar nada

  Cenário: Revert real nunca é tratado como in_flight
    Dado uma tarefa pending de um lote cujo tipo não tem processo registrado on-chain (a ancoragem reverte)
    Quando o retry de ancoragem roda
    Então o resultado é "failed" com o erro do contrato, nunca "in_flight", e a tarefa continua pending
