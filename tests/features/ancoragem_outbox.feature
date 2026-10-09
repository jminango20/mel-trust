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
