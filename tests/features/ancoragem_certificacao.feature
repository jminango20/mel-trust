# language: pt
Funcionalidade: Ancoragem da VC de certificação na blockchain

  Cenário: Certificação do lote é ancorada como documento vinculado ao lote
    Dado que o lote foi analisado pelo lab e aprovado pelo certifier
    Quando a coop-a retira a credencial de certificação (Holder) via POST /lote/{batch}/pre-authorized-code seguido de /token e /credential no certifier
    Então o hash da VC de certificação é ancorado on-chain via createDocument, com parentAssetId apontando para o asset do lote
    E a resposta de POST /credential inclui o txHash da ancoragem
