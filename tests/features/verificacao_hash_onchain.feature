# language: pt
Funcionalidade: Verificador confere integridade dos hashes ancorados on-chain

  Cenário: Verificador confere que os 3 hashes ancorados batem com o dado recebido
    Dado que um lote foi registrado, analisado e certificado, com os 3 assets ancorados on-chain (lote, laudo como documento filho, certificação como documento filho)
    Quando o verifier-demo consulta GET /verify com propósito válido
    Então ele lê via AssetRegistry.getAsset() os 3 assetIds: o do lote (keccak256(batchNumber)), o do laudo (keccak256(batchNumber + "-lab-analysis")), e o da certificação (keccak256(batchNumber + "-certification"))
    E confirma que os 2 documentos têm parentAssetId apontando para o asset do lote
    E compara cada dataHashes[] com o hash recalculado do dado correspondente recebido via SPARQL federado
    E a resposta inclui um campo por evento: lote e laudo com integridade "confirmed" (comparação real e determinística), certificação com "not_verifiable_via_query" (hash do SD-JWT só verificável via apresentação da credencial, OID4VP, ainda não implementado)
