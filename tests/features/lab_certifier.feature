# language: pt
# HISTÓRICO: este arquivo chegou a ser reescrito pra uma arquitetura
# "lab stateless" (POST /avaliar, coop-a persiste) por um turno —
# REVERTIDO. Voltou à arquitetura original: lab com Fuseki próprio
# (POST /analise), certifier consultando lab + coop-a/coop-b via SPARQL
# federado. Ver docs/known-issues.md/CHANGELOG.md.
#
# 3 DECISÕES DE DESIGN (do texto original, mantidas): porta do lab (8005,
# sequência já usada), gatilho HTTP do certifier (reusa /credential já
# existente), batchNumber do Cenário 2 dedicado
# ("MS-2026-042-LAB-REJEITADO", não colide com dado legado).

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
