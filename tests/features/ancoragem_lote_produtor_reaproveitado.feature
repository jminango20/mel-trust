# language: pt
Funcionalidade: Hash ancorado do lote reflete o conjunto completo de triplas, mesmo com produtor reaproveitado

  Cenário: Hash ancorado inclui as triplas do produtor quando ele é reaproveitado
    Dado que um produtor já existe no coop-b (lote anterior cadastrado)
    Quando um segundo lote é cadastrado reaproveitando esse produtor
    Então o hash ancorado reflete o conjunto completo de triplas do lote (incluindo produtor), verificável via getAsset()
