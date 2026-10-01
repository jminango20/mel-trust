# language: pt
Funcionalidade: Verificador cria uma solicitação de apresentação (OID4VP)

  Cenário: Verificador cria uma solicitação de apresentação
    Dado que o verifier-demo quer verificar a certificação do lote MS-2026-042
    Quando ele chama POST /presentation-request/{batch_number}
    Então recebe de volta um nonce único, vinculado a essa solicitação específica
    E esse nonce ainda não foi usado por nenhuma apresentação
