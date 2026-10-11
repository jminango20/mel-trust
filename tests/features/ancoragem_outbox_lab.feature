# language: pt
# Outbox de ancoragem do LAUDO (lab, POST /analise), no mesmo molde de ancoragem_outbox.feature.
# O laudo é um documento filho: assetId = keccak256(batchNumber + "-lab-analysis"),
# parentAssetId = keccak256(batchNumber) (o asset do lote, ancorado pelo coop-a).
Funcionalidade: Ancoragem pendente do laudo é concluída por retry (outbox do lab), sem reenviar transação já feita

  Cenário: Ancoragem pendente do laudo é concluída por retry, sem reenviar transação já feita
    Dado um laudo cadastrado no lab cuja ancoragem está marcada como pendente e que ainda NÃO existe on-chain
    Quando o retry de ancoragem do lab roda
    Então a transação é enviada, o laudo passa a "anchored" e o txHash é registrado

    Dado um laudo cuja transação já foi confirmada on-chain, mas que continua marcado como pendente (recibo perdido)
    Quando o retry de ancoragem do lab roda
    Então nenhuma transação é reenviada (o retry consulta getAsset primeiro, vê que o hash já bate e só marca como "anchored")

    Quando o retry do lab roda uma segunda vez
    Então não há nada a processar no lab e nada é reenviado

  Cenário: Hash divergente do laudo on-chain é reportado como mismatch e nunca vira anchored
    Dado um laudo ancorado on-chain cujo conteúdo no Fuseki do lab foi alterado depois da ancoragem, com a marca pendente
    Quando o retry de ancoragem do lab roda
    Então o resultado do laudo é "mismatch", a tarefa não vira "anchored" e nenhuma transação é enviada

    Quando o retry do lab roda novamente
    Então o laudo continua reportado como "mismatch", a tarefa segue "mismatch" e nada é reenviado

  Cenário: Lote pai ainda não ancorado: o retry não envia e responde waiting_parent
    Dado uma tarefa pending de um laudo cujo lote (pai) ainda não foi ancorado on-chain
    Quando o retry de ancoragem do lab roda
    Então o resultado é "waiting_parent", nenhuma transação é enviada e a tarefa continua pending

  Cenário: Transação do laudo em voo não é falha: o retry responde in_flight e conclui na passada seguinte
    Dado uma tarefa pending de um laudo cuja transação está em voo (o envio responde Known transaction)
    Quando o retry de ancoragem do lab roda com o envio respondendo Known transaction
    Então a resposta é outcome in_flight, nenhum ERROR é logado e a tarefa do laudo continua pending

    Quando a transação em voo do laudo é minerada e o retry do lab roda novamente
    Então a tarefa do laudo vira "anchored" sem reenviar nada

  Cenário: Sem natureId (lote não encontrado em nenhuma cooperativa), o retry responde failed com o motivo
    Dado uma tarefa pending de um laudo cujo lote está ancorado on-chain mas não é encontrado em nenhuma cooperativa
    Quando o retry de ancoragem do lab roda
    Então o resultado é "failed" com o motivo natureId indisponível, nenhuma transação é enviada e a tarefa continua pending
