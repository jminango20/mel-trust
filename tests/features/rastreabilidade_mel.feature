# language: pt
# Baseado na narrativa oficial do lote MS-2026-042 (produtor João da Silva,
# mel silvestre de Aroeira) — ver
# docs/notas-arquitetura/narrativa-como-criterio-mvp.md. MS-2026-042 é o
# lote CANÔNICO do coop-a, cadastrado via POST /lote (migrado de
# pipeline.py+fixture — ver CHANGELOG.md e docs/known-issues.md). Não é
# criado por este teste (idempotência de negócio impede recadastro, ver
# tests/features/lote_idempotente.feature) — só verificado como
# pré-existente.
#
# O cenário de rejeição SHACL por medição (antigo Cenário 2 deste arquivo,
# via `pipeline.py` + fixture inválida) foi REMOVIDO: pipeline.py não existe
# mais (ver docs/known-issues.md), e essa cobertura já está garantida em
# tests/features/lab_certifier.feature (Cenário 2, mel de Jataí fora do
# limite). O único gate SHACL que resta ao produtor via /lote é o
# estrutural (batchNumber/ownedBy/geo/licença de meliponário), já testado
# em lote_idempotente.feature, pre_authorized_code.feature e
# lote_meliponary.feature.
#
# Pré-condição para rodar: coop-a (porta 8001) e verifier-demo (porta 8004)
# no ar, com MS-2026-042 já cadastrado via POST /lote. Os testes NÃO sobem
# nenhum serviço.


Funcionalidade: Rastreabilidade de lote de mel do produtor até a verificação federada

  Cenário: Lote de mel de Apis dentro do padrão é rastreado de ponta a ponta
    Dado o lote MS-2026-042 já cadastrado no coop-a
    Quando o coop-a emite a credencial via POST /credential
    Então o SD-JWT VC contém floralOrigin e demais claims disclosable corretos
    Quando o verifier-demo consulta GET /verify com propósito "Compliance EUDR"
    Então retorna 200 com o dado agregado correto

  @futuro
  Cenário: Contratos de dados multi-política restringem acesso por propósito e ator
    Dado o lote MS-2026-042 com um Contrato de Dados CONTRACT-HONEY-EU-2026-042 para o importador Honig Import GmbH, propósito "Compliance EUDR"
    E o lote MN-2026-015 com um segundo Contrato de Dados CONTRACT-RESEARCH-MN-2026-015 para a Universidade de Brasília, propósito pesquisa científica
    Quando o importador consulta GET /verify com propósito "Compliance EUDR"
    Então recebe dado do lote e geolocalização, mas o contrato proíbe redistribuição e comercialização da geolocalização
    Quando um pesquisador da UnB consulta o mesmo lote com propósito de pesquisa
    Então recebe dado agregado do lote de mel de Jataí, sem atribuição nominal a João sem consentimento adicional

  @futuro
  Cenário: Pesquisador da UnB acessa dado agregado sob contrato de pesquisa
    Dado o Contrato de Dados CONTRACT-RESEARCH-MN-2026-015 autoriza leitura e agregação de dados do lote de mel de Jataí (MN-2026-015) para fins científicos
    Quando o pesquisador da UnB consulta o lote via canal federado
    Então recebe os dados agregados, sem nome do produtor exposto
    E qualquer tentativa de atribuir os dados nominalmente a João é rejeitada sem consentimento adicional registrado

  @futuro
  Cenário: Migração de colmeias vinculada a Guia de Trânsito Animal (GTA)
    Dado que João migra 50 colmeias de Apis mellifera para um pasto em Montes Claros, Minas Gerais, antes da florada da aroeira
    Quando o evento de Migração de Colmeias é registrado no sistema
    Então fica vinculado à GTA nº GTA-DF-2026-004567, emitida pela Defesa Sanitária Animal
    E a rastreabilidade completa do mel passa a incluir a sanidade do plantel migrado, desde a florada até a mesa do consumidor
