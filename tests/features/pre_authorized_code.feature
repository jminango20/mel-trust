# language: pt
# Fase RED (TDD): hoje POST /lote sempre devolve o mesmo pre_authorized_code
# fixo (config.PRE_AUTHORIZED_CODE, ex. "coop-a-demo-pre-auth-code"), não um
# código único por lote, e não há consumo de uso único nem endpoint de
# reemissão — ver coop-a/token_endpoint.py (docstring do módulo) e
# coop-a/lote_endpoint.py (resposta não tem campo pre_authorized_code). Ver
# docs/known-issues.md, "pre-authorized_code fixo por nó, sem amarração à
# entrega/lote".

Funcionalidade: Código de acesso único por entrega de lote

  Cenário: Cada entrega gera um código de acesso único à credencial
    Dado que o produtor João entrega um novo lote de mel silvestre
    Quando a cooperativa cadastra o lote via POST /lote
    Então a resposta inclui um pre_authorized_code único para esse lote

    Quando esse código é usado uma vez via /credential
    Então a credencial é emitida com sucesso
    E uma segunda tentativa de usar o MESMO código é rejeitada

  Cenário: Cooperativa reemite código se o produtor perde o original antes de usar
    Dado que o lote MS-2026-042 tem um pre_authorized_code ainda não utilizado
    Quando a cooperativa solicita um novo código para esse mesmo lote via POST /lote/{batchNumber}/reemitir-codigo
    Então o código antigo é invalidado (mesmo que nunca tenha sido usado)
    E um novo código é gerado, válido para o mesmo lote
    E o código antigo, se usado depois, é rejeitado
