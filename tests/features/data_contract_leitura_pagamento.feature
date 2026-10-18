# language: pt
Funcionalidade: Leitura do pagamento de um Contrato de Dados (coop-a)
  O coop-a lê no VendaDados (depositos(ref), só leitura, sem chave) o estado do pagamento do
  contrato e o traduz em um status. A ref é o contractRef do contrato. Precisa do node do
  VendaDados (8546) no ar, exceto no cenário do node parado; não usa a Besu.

  @sem_besu
  Cenário: Sem depósito o status é not_paid
    Dado um contrato de dados válido agora criado via POST /data-contract
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 200 com status "not_paid"

  @sem_besu
  Cenário: Depósito do comprador certo com o valor exato é paid
    Dado um contrato de dados válido agora criado via POST /data-contract
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 200 com status "paid"

  @sem_besu
  Cenário: Depósito de outra carteira com a mesma ref é mismatch
    Dado um contrato de dados válido agora criado via POST /data-contract
    E uma OUTRA carteira depositou o priceUnits exato com a ref do contrato
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 200 com status "mismatch"

  @sem_besu
  Cenário: Valor diferente do priceUnits é mismatch
    Dado um contrato de dados válido agora criado via POST /data-contract
    E o comprador do contrato depositou um valor diferente do priceUnits com a ref do contrato
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 200 com status "mismatch"

  @sem_besu
  Esquema do Cenário: Depósito feito agora fora da janela de validade é outside_window
    Dado um contrato de dados com a janela toda no <janela> criado via POST /data-contract
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 200 com status "outside_window"

    Exemplos:
      | janela  |
      | passado |
      | futuro  |

  @sem_besu
  Esquema do Cenário: Depois de liberar ou reembolsar o status acompanha o estado
    Dado um contrato de dados válido agora criado via POST /data-contract
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    E a cooperativa <acao> a ref no VendaDados
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 200 com status "<status>"

    Exemplos:
      | acao       | status   |
      | liberou    | released |
      | reembolsou | refunded |

  @sem_besu
  Cenário: contractRef que não existe no Fuseki devolve 404
    Dado um contractRef bytes32 que nenhum contrato do coop-a tem
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 404

  # Só roda com -m node_parado e o node da 8546 PARADO (o cenário confere que está).
  @sem_besu @node_parado
  Cenário: Com o node do VendaDados parado a leitura devolve 503 com mensagem fixa
    Dado que o node do VendaDados está parado
    E um contrato de dados válido agora criado via POST /data-contract
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 503 com mensagem fixa e sem texto de exceção

  @sem_besu
  Esquema do Cenário: A janela é comparada em UTC, não pelo relógio escrito na data
    Dado um contrato de dados com <campo> 1 hora <direcao> de agora escrito com o fuso <fuso>
    E o comprador do contrato depositou o priceUnits exato com a ref do contrato
    Quando o coop-a lê o pagamento do contrato
    Então a resposta é 200 com status "outside_window"

    Exemplos:
      | campo      | direcao | fuso   |
      | validUntil | antes   | +05:00 |
      | validFrom  | depois  | -05:00 |
