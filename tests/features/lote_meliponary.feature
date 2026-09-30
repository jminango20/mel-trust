# language: pt
# GREEN: POST /lote aceita meliponaryId/environmentalLicenseNumber.
# :MeliponaryLicenseShape (validation-shapes.ttl, Art. 68 da Portaria
# 795/2023) antes só era coberta pelo fixture coop-b/data/sample-instance.ttl
# (via pipeline.py, removido — ver docs/known-issues.md e CHANGELOG.md sobre
# a migração de MN-2026-007 pra /lote com esses campos).

Funcionalidade: POST /lote aceita meliponário e licença ambiental (mel nativo)

  Cenário: Lote com meliponário e licença ambiental é aceito
    Dado um novo lote de mel nativo com meliponaryId e environmentalLicenseNumber
    Quando a cooperativa cadastra o lote via POST /lote
    Então o gate SHACL aprova e o meliponário fica registrado com a licença vinculada

  Cenário: Meliponário sem licença ambiental é rejeitado pelo gate SHACL
    Dado um novo lote de mel nativo com meliponaryId mas sem environmentalLicenseNumber
    Quando a cooperativa tenta cadastrar o lote via POST /lote
    Então retorna 400 com violação de MeliponaryLicenseShape
