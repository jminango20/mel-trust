# language: pt
# CertificateShape exige descrição (rdfs:comment, texto livre, 1 exatamente) com o
# número ou o hash do certificado — campo acrescentado a pedido do Ivan Bergier.
# Teste com pyshacl direto: nenhum nó usa essa shape ainda.

Funcionalidade: Descrição obrigatória no certificado

  Cenário: Certificado sem descrição é rejeitado
    Dado um certificado completo, mas sem descrição
    Quando o grafo é validado contra validation-shapes.ttl
    Então há violação com a mensagem "Certificado precisa de uma descrição com o número ou o hash do certificado"

  Cenário: Certificado com descrição passa
    Dado um certificado completo, com descrição
    Quando o grafo é validado contra validation-shapes.ttl
    Então o grafo está conforme
