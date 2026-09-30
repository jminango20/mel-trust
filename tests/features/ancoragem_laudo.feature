# language: pt
Funcionalidade: Ancoragem do laudo de análise laboratorial na blockchain

  Cenário: Análise do laboratório é ancorada como documento vinculado ao lote
    Dado que o lote MS-2026-042 já foi ancorado on-chain pelo coop-a
    Quando o lab avalia a amostra via POST /analise
    Então o hash do laudo é ancorado on-chain via createDocument, com parentAssetId apontando para o asset do lote
    E a resposta de POST /analise inclui o txHash da ancoragem
