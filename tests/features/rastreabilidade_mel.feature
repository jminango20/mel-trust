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
