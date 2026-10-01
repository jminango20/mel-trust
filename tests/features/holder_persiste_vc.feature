# language: pt
Funcionalidade: Cooperativa guarda a VC de certificação recebida como Holder

  Cenário: Cooperativa guarda a VC de certificação recebida como Holder
    Dado que a coop-a retira a VC de certificação do certifier (fluxo já implementado hoje: pre-authorized-code → /token → /credential)
    Quando a coop-a recebe a credencial com sucesso
    Então o SD-JWT completo é guardado no Fuseki da coop-a, vinculado ao batchNumber (como literal, não RDF estruturado)
    E uma consulta SPARQL pelo batchNumber recupera a credencial guardada
