# language: pt
Funcionalidade: Apresentação de VC de certificação com prova de posse fresca (OID4VP)

  Cenário: Cooperativa apresenta a VC de certificação com prova de posse fresca
    Dado que o verifier-demo gerou um presentation request com nonce para o lote MS-2026-042
    Quando a coop-a chama POST /present/{batch_number} com esse nonce
    Então ela busca a VC de certificação guardada no próprio Fuseki
    E monta um Key Binding JWT assinado com a chave do holder (cnf.jwk da credencial), contendo o nonce e a audiência do verificador
    E retorna a VP completa: <Credential-JWT>~<Disclosures escolhidas>~<KB-JWT>
