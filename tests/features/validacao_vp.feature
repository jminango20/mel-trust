# language: pt
Funcionalidade: Verificador valida a apresentação (OID4VP) e confirma integridade via hash real

  Cenário: Verificador valida a apresentação e confirma integridade via hash real
    Dado que a coop-a apresentou uma VP válida para o lote MS-2026-042
    Quando o verifier-demo recebe a VP via POST /present-response/{batch_number}
    Então ele valida o KB-JWT (nonce correto e não reutilizado, sd_hash bate com o Credential-JWT + disclosures recebidos, assinatura confere com cnf.jwk da credencial)
    E recalcula o hash do Credential-JWT (só essa parte, não a VP inteira)
    E compara com dataHashes[] ancorado on-chain para o asset de certificação
    E a resposta substitui "not_verifiable_via_query" por "confirmed" ou "mismatch"
