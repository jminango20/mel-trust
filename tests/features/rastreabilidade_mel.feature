# language: pt
# Baseado na narrativa oficial do lote MS-2026-042 (produtor João da Silva,
# mel silvestre de Aroeira) — ver
# docs/notas-arquitetura/narrativa-como-criterio-mvp.md. Os dois cenários
# usam DADO REAL já presente no Fuseki do coop-a (via fixture
# coop-a/data/sample-instance.ttl / sample-instance-invalid.ttl,
# inseridas por pipeline.py) — não inventam lote sintético.
#
# Pré-condição para rodar: coop-a (porta 8001) e verifier-demo (porta 8004)
# no ar, com os fixtures de coop-a já inseridos no Fuseki (ver
# docs/demo-ivan.md, seção 0/1). Os testes NÃO sobem nenhum serviço.


Funcionalidade: Rastreabilidade de lote de mel do produtor até a verificação federada

  Cenário: Lote de mel de Apis dentro do padrão é rastreado de ponta a ponta
    Dado um lote de mel de Apis mellifera com umidade 18.5%
    Quando o produtor cadastra o lote via POST /lote no coop-a
    Então o gate SHACL aprova e o lote é inserido no Fuseki
    Quando o coop-a emite a credencial via POST /credential
    Então o SD-JWT VC contém floralOrigin e demais claims disclosable corretos
    Quando o verifier-demo consulta GET /verify com propósito "Compliance EUDR"
    Então retorna 200 com o dado agregado correto

  Cenário: Lote de mel de Apis fora do padrão é rejeitado pelo gate SHACL
    Dado um lote de mel de Apis mellifera com umidade 24.5%
    Quando o produtor tenta cadastrar esse lote via pipeline.py no coop-a
    Então o gate SHACL rejeita e nada é inserido no Fuseki

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
