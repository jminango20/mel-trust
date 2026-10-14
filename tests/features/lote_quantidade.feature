# language: pt
# RED: quantidade do lote ainda não existe em POST /lote. Hoje o campo é
# ignorado e a ancoragem usa amount=1 fixo (coop-a/anchoring.py, operation_data).
# Decisão: a API recebe litros (`quantityLiters`, até 3 casas decimais); o nó
# converte para mililitros inteiros (`:quantityMilliliters`, xsd:integer) e
# ancora esse inteiro como `amount` do createAsset. Regra de negócio (> 0) é
# do SHACL; formato (casas decimais) é erro de Python.

Funcionalidade: Quantidade do lote em POST /lote

  Cenário: Quantidade em litros é guardada em mililitros inteiros e ancorada
    Dado um novo lote válido com quantityLiters 1.8
    Quando a cooperativa cadastra o lote via POST /lote
    Então o Fuseki guarda quantityMilliliters 1800 como inteiro
    E o createAsset on-chain usa amount 1800

  Esquema do Cenário: Quantidade zero ou negativa é rejeitada pelo gate SHACL
    Dado um novo lote válido com quantityLiters <litros>
    Quando a cooperativa tenta cadastrar o lote via POST /lote
    Então retorna 400 com shacl_violation e nada é inserido no Fuseki

    Exemplos:
      | litros |
      | 0      |
      | -1.5   |

  Cenário: O retry de ancoragem usa o mesmo amount da quantidade do lote
    Dado um lote de 1800 mL no Fuseki do coop-a com ancoragem pendente e ainda fora da blockchain
    Quando o retry de ancoragem do coop-a roda
    Então o asset on-chain tem amount 1800

  Cenário: Mais de 3 casas decimais é erro de formato
    Dado um novo lote válido com quantityLiters 1.2345
    Quando a cooperativa tenta cadastrar o lote via POST /lote
    Então retorna 400 invalid_request e nada é inserido no Fuseki
