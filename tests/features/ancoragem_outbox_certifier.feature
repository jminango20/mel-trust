# language: pt
# Outbox de ancoragem da VC de certificação (certifier, POST /credential), no molde de
# ancoragem_outbox_lab.feature. A VC é um documento filho:
# assetId = keccak256(batchNumber + "-certification"), parentAssetId = keccak256(batchNumber).
# O hash ancorado é keccak256 do SD-JWT COMPLETO emitido (salts aleatórios nas disclosures),
# portanto NÃO é reconstruível a partir de nenhum dado: o retry reenvia o hash GUARDADO na tarefa.
#
# Desvio do molde do lab: "recibo perdido" + "segundo retry sem nada a processar" ficam num
# cenário PRÓPRIO (no lab eram 3 blocos num cenário só), para que cada falha do RED apareça
# isolada em vez de ficar escondida atrás do primeiro bloco que falha.
Funcionalidade: Ancoragem pendente da VC de certificação é concluída por retry (outbox do certifier), sem reenviar transação já feita

  Cenário: Tarefa pending com hash guardado cujo asset ainda não existe on-chain: o retry envia o hash guardado
    Dado uma tarefa pending da VC de certificação com hash guardado, cujo asset ainda não existe on-chain
    Quando o retry de ancoragem do certifier roda
    Então a transação é enviada com o hash guardado, a tarefa passa a "anchored" e o txHash é registrado

  Cenário: Recibo perdido: a VC já existe on-chain com o mesmo hash, o retry não reenvia, e o segundo retry não tem nada a processar
    Dado uma VC de certificação emitida de verdade por POST /credential e ancorada on-chain, com a tarefa devolvida para pending
    Quando o retry de ancoragem do certifier roda
    Então nenhuma transação é reenviada (o retry consulta getAsset primeiro, vê que o hash guardado já bate e só marca como "anchored")

    Quando o retry do certifier roda uma segunda vez
    Então não há nada a processar no certifier e nada é reenviado

  Cenário: Hash on-chain diferente do hash guardado é reportado como mismatch e nunca vira anchored
    Dado uma VC de certificação ancorada on-chain cuja tarefa guarda um hash DIFERENTE do ancorado, marcada como pending
    Quando o retry de ancoragem do certifier roda
    Então o resultado da VC é "mismatch", a tarefa não vira "anchored" e nenhuma transação é enviada

    Quando o retry do certifier roda novamente
    Então a VC continua reportada como "mismatch", a tarefa segue "mismatch" e nada é reenviado

  Cenário: Lote pai ainda não ancorado: o retry não envia e responde waiting_parent
    Dado uma tarefa pending da VC de certificação cujo lote (pai) ainda não foi ancorado on-chain
    Quando o retry de ancoragem do certifier roda
    Então o resultado é "waiting_parent", nenhuma transação é enviada e a tarefa continua pending

  Cenário: Transação da VC em voo não é falha: o retry responde in_flight e conclui na passada seguinte
    Dado uma tarefa pending da VC de certificação cuja transação está em voo (o envio responde Known transaction)
    Quando o retry de ancoragem do certifier roda com o envio respondendo Known transaction
    Então a resposta é outcome in_flight, nenhum ERROR é logado e a tarefa da VC continua pending

    Quando a transação em voo da VC é minerada e o retry do certifier roda novamente
    Então a tarefa da VC vira "anchored" sem reenviar nada

  Cenário: Reemissão de certificação para um lote que já tem VC é bloqueada com 409
    Dado um lote que já tem certificação emitida (AnchoringTask existente)
    Quando POST /credential é chamado de novo para o mesmo lote
    Então responde 409, não emite nova VC e não cria nova AnchoringTask

