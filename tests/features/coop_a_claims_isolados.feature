# language: pt
# FASE RED (TDD): hoje coop-a/credential_endpoint.py usa uma query SPARQL
# DINÂMICA que inclui moistureContent/hmfContent/acidityContent como claims
# disclosable SE a tripla existir no Fuseki do coop-a — não importa como ela
# chegou lá (POST /lote nunca escreve esses campos, mas nada no Fuseki
# IMPEDE que outra via — migração manual, script, edição direta — os
# insira). Este cenário planta moistureContent direto no Fuseki (bypass do
# /lote, simulando "por qualquer motivo futuro") e confirma que a
# credencial do coop-a NÃO deveria vazar esse dado de medição — hoje vaza.

Funcionalidade: coop-a nunca expõe claims de medição/laudo, mesmo que presentes no Fuseki

  Cenário: Credencial do coop-a não expõe dado de medição, mesmo quando presente no Fuseki
    Dado que o lote MS-2026-042 tem moistureContent no Fuseki do coop-a (dado de medição que pode coexistir ali por qualquer motivo futuro)
    Quando POST /credential é chamado no coop-a para esse lote
    Então moistureContent NÃO aparece nas claims da credencial (nem em claro, nem como disclosure)
    E o mesmo vale para hmfContent, acidityContent, diastaseActivity, legalStatus, qualityStatus — nenhum desses é claim do coop-a
