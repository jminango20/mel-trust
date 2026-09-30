# language: pt
# RED antes de pre_authorized_code (ver tests/features/pre_authorized_code.feature):
# POST /lote hoje reafirma as MESMAS triplas se o batchNumber já existe com a
# MESMA URI determinística (Lote_{slug(batchNumber)}), mas não REJEITA um
# batchNumber já cadastrado — só insere de novo (idempotente na tripla, não
# no negócio). Efeito colateral real encontrado ao testar
# pre_authorized_code.feature: reafirmar MS-2026-042 recriou um recurso
# duplicado (:Lote_MS_2026_042 vs :Lote_Mel_Silvestre_2026_042 do fixture
# original) — ver docs/known-issues.md.

Funcionalidade: POST /lote rejeita batchNumber já cadastrado

  Cenário: Cadastro repetido do mesmo lote é rejeitado
    Dado que o lote MS-2026-042 já existe
    Quando a cooperativa tenta cadastrar via POST /lote de novo com o mesmo batchNumber
    Então retorna 409 e nenhum recurso duplicado é criado
