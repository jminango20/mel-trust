# language: pt
Funcionalidade: Cooperativa é a holder da VC de certificação do lote

  Cenário: Cooperativa é a holder da VC de certificação do lote
    Dado que o lote MS-2026-042 foi analisado pelo lab e aprovado pelo certifier
    Quando a coop-a usa o pre_authorized_code do lote para chamar POST /credential no certifier
    Então a VC de certificação é emitida e retornada à coop-a
    E a coop-a pode armazenar essa VC como parte do dossiê do lote
