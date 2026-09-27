# language: pt
# Fase RED (TDD): estes 2 cenários são escritos e rodados ANTES de lab/
# existir e ANTES do certifier ser reestruturado — devem falhar hoje. Ver
# tests/step_defs/test_lab_certifier.py para as 3 decisões de design que
# não estavam no texto original (porta do lab, endpoint-gatilho do
# certifier, batchNumber do cenário 2), documentadas lá.

Funcionalidade: Laboratório insere análise e certificador valida contra regra regulatória

  Cenário: Laboratório insere análise e certificador valida contra regra regulatória
    Dado que o lote MS-2026-042 já existe no coop-a
    Quando o laboratório registra análise via POST /analise no lab (umidade 18.5%, HMF 12.3, acidez, diastase)
    Então o gate SHACL do lab aprova a forma do dado e insere no Fuseki do lab

    Quando o certifier consulta o Fuseki do lab via SPARQL federado para o lote MS-2026-042
    E roda o SHACL regulatório (HoneyApisMoistureShape) sobre esse dado
    Então aprova (dentro de 20%) e classifica "Aprovado para Comercialização"

  Cenário: Laboratório insere análise fora do padrão, certificador rejeita
    Quando o laboratório registra análise com 24.5% de umidade
    Então o gate SHACL do lab aprova a forma (é só validação de tipo/presença)
    Mas quando o certifier roda o SHACL regulatório sobre esse dado
    Então rejeita, e o lote não é certificado
