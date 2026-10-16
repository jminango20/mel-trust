# language: pt
Funcionalidade: Contrato de Dados no coop-a (POST /data-contract)
  O coop-a registra os termos de venda de dados (consumidor, finalidade, preço, carteira
  do comprador, validade) como um agritrust:DataContract no Fuseki, identificado por um
  contractRef (bytes32) derivado dos termos. Esse mesmo bytes32 será a `ref` do
  VendaDados.depositar. Nada aqui usa a Besu nem o VendaDados.

  @sem_besu
  Cenário: Contrato válido é criado com contractRef determinístico
    Dado os termos de um contrato de dados válido com um consumidor sintético
    Quando o coop-a recebe POST /data-contract com esses termos
    Então a resposta é 201 com contractRef em bytes32 hexadecimal
    E o contractRef é o keccak256 do JSON canônico dos termos
    E o Fuseki do coop-a guarda um DataContract com esse contractRef

  @sem_besu
  Esquema do Cenário: Termos inválidos são rejeitados e nada é inserido
    Dado os termos de um contrato de dados válido com um consumidor sintético
    E o campo "<campo>" alterado para "<valor>"
    Quando o coop-a recebe POST /data-contract com esses termos
    Então a resposta é 400 com error shacl_violation
    E o Fuseki do coop-a não tem DataContract desse consumidor

    Exemplos:
      | campo      | valor                |
      | priceUnits | ausente              |
      | priceUnits | 0                    |
      | priceUnits | -5                   |
      | validUntil | igual ao validFrom   |
      | validUntil | anterior ao validFrom |

  @sem_besu
  Cenário: Repetir o mesmo contrato dá 409 com o contractRef existente
    Dado os termos de um contrato de dados válido com um consumidor sintético
    E o coop-a já recebeu POST /data-contract com esses termos
    Quando o coop-a recebe POST /data-contract com esses termos
    Então a resposta é 409 com o contractRef do primeiro POST
    E o Fuseki do coop-a tem exatamente 1 DataContract desse consumidor
