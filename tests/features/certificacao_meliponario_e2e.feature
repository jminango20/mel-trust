# language: pt
Funcionalidade: Fluxo completo de certificação para mel nativo com meliponário

  Cenário: Fluxo completo de certificação para mel nativo com meliponário
    Dado que o produtor João cadastra o lote MN-2026-015 via POST /lote no coop-b, incluindo meliponaryId e environmentalLicenseNumber
    E o laboratório analisa a amostra (umidade 25.4%, acidez dentro do limite condicional de meliponíneos)
    Quando a coop-b usa POST /lote/MN-2026-015/pre-authorized-code no certifier
    Então o código é gerado (SHACL regulatório de mel nativo aprova, mesmo com umidade acima do limite de Apis)
    E a coop-b retira a credencial de certificação com sucesso
