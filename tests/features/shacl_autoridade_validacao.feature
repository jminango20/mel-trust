# language: pt
# HISTÓRICO: POST /lote (coop-a/lote_endpoint.py) rejeitava batchNumber
# ausente com uma checagem Python genérica (_REQUIRED_FIELDS), ANTES de
# montar o grafo — a shape SHACL HoneyApisIdentificationShape
# (validation-shapes.ttl) nunca chegava a rodar sobre essa requisição.
# _REQUIRED_FIELDS foi REMOVIDA — ver docs/known-issues.md, "POST /lote:
# gate SHACL, na prática, sempre passa". Cenário 1 documenta esse RED
# original (hoje GREEN). Cenário 2 é FECHAMENTO DE COBERTURA, não correção
# de bug: producerDID/ownedBy ausente já era rejeitado corretamente pela
# MESMA shape (ela tem sh:property pra batchNumber E pra ownedBy) assim que
# _REQUIRED_FIELDS caiu — confirmado manualmente via curl antes deste
# cenário existir. Nasce GREEN de propósito; existe pra não deixar essa
# checagem sem cobertura automatizada.

Funcionalidade: SHACL é a autoridade de validação, não checagem Python duplicada

  Cenário: SHACL, não Python, decide se campo estrutural ausente é motivo de rejeição
    Dado que a requisição POST /lote não inclui batchNumber
    Quando ela é processada pelo endpoint
    Então a rejeição vem do gate SHACL (HoneyApisIdentificationShape, minCount), não de uma checagem de campo obrigatório em Python
    E a mensagem de erro retornada contém o texto da shape SHACL, não uma mensagem genérica de "campo obrigatório ausente"

  Cenário: SHACL rejeita lote sem produtor associado
    Dado que a requisição POST /lote não inclui producerDID
    Quando ela é processada pelo endpoint
    Então a rejeição vem do gate SHACL (HoneyApisIdentificationShape), não de checagem Python
    E nenhum recurso é inserido no Fuseki (nem órfão, nem parcial)
