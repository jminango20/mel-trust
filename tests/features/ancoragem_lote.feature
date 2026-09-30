# language: pt
Funcionalidade: Ancoragem do registro de lote na blockchain

  Cenário: Registro de lote é ancorado na blockchain
    Dado que a coop-a cadastra um novo lote válido via POST /lote
    Quando o lote é inserido com sucesso no Fuseki
    Então o hash do dado do lote é ancorado on-chain via TransactionOrchestrator (createAsset, processId="honey-batch-registration")
    E a resposta de POST /lote inclui o txHash da ancoragem
