# language: pt
Funcionalidade: A ref do Contrato de Dados vale fora do Python (coop-a -> Node -> VendaDados)
  O contractRef que o coop-a devolve precisa ser reproduzível por outra implementação
  (Node/ethers) a partir da regra de canonização documentada em
  coop-a/data_contract_endpoint.py, e precisa ser aceito como `ref` do depósito no
  VendaDados. Precisa do node do VendaDados (8546) no ar; não usa a Besu.

  @sem_besu
  Cenário: A ref do Contrato de Dados é reproduzível fora do Python e aceita no depósito
    Dado os termos de um contrato de dados com consumidor de nome sintético cheio de caracteres especiais e a conta de teste nº 1 do Hardhat como comprador
    Quando o coop-a recebe POST /data-contract com esses termos
    Então a resposta é 201 com contractRef em bytes32 hexadecimal
    E o helper Node reproduz exatamente o contractRef devolvido pelo coop-a
    Quando o comprador deposita o priceUnits no VendaDados com essa ref
    Então depositos da ref devolve o comprador, o priceUnits, um timestamp maior que zero e o estado 1
