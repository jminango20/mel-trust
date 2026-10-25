# language: pt
# RED: não existe script de implantação do VendaDados em rede pública (só o deploy-local.js, que recusa tudo
# que não é chainId 31337 e usa a conta nº 0 do node de dev, cuja chave é PÚBLICA). Proposta:
# `node contracts/venda-dados/scripts/deploy-publico.js`, configurado só por variáveis de ambiente:
#   DEPLOY_REDE                 nome do arquivo de saída deployments/<rede>.json; só [a-z0-9-]; nunca "local"
#   DEPLOY_RPC_URL              URL do RPC (pode levar chave de API: nunca impressa nem gravada)
#   DEPLOY_CHAIN_ID_ESPERADO    chainId que o node tem de ter; 31337 é recusado (essa é a rede de dev)
#   VENDA_DADOS_COOP_KEY_PATH   arquivo da chave que implanta e É a cooperativa do contrato
#   PARTE_COOPERATIVA_PERCENTUAL  0 a 100 (padrão 0)      PRAZO_REEMBOLSO_S  inteiro > 0 (padrão 2592000)
#   BESU_KEY_PATH (opcional)    se definida, a chave da cooperativa NÃO pode ter o mesmo endereço
# Saída deployments/<rede>.json: rede, chainId, cooperativa, usdt, vendaDados, blocoUsdt, blocoVendaDados,
# txUsdt, txVendaDados (sem URL, host nem chave). O script nunca toca em deployments/local.json.
#
# O script é chamado como PROCESSO (node), com chaves geradas pelo teste num diretório temporário. A URL
# leva um sentinela de chave de API em três formatos (caminho, query, senha). Nenhuma rede real: os casos
# de recusa apontam para uma porta onde NADA escuta (prova de que recusou antes de conectar: o erro não é de
# conexão); os de integração usam um node local do Hardhat subido pelo próprio teste (chainId 424242, porta
# livre), financiado a partir da conta nº 0 do node (só no teste).

Funcionalidade: Implantação do VendaDados em rede pública

  @sem_besu
  Esquema do Cenário: Recusa antes de qualquer conexão: <caso>
    Dado o caso de recusa "<caso>"
    Quando o script de implantação é executado com cada um dos 3 formatos de URL do RPC
    Então o script recusa com código de saída diferente de zero e uma mensagem clara
    E o erro não é de conexão nem de módulo ausente
    E a saída não contém o sentinela, o host, a chave em hex nem o caminho do arquivo da chave

    Exemplos:
      | caso                                  |
      | chave igual à conta nº 0 do Hardhat   |
      | chave igual à conta nº 1 do Hardhat   |
      | chainId esperado 31337                |
      | DEPLOY_REDE local                     |
      | DEPLOY_REDE com maiúscula             |
      | DEPLOY_REDE com barra                 |
      | DEPLOY_REDE com ponto                 |
      | chave ausente                         |
      | chave inexistente                     |
      | chave com conteúdo inválido           |
      | chave com o mesmo endereço da Besu    |
      | percentual 101                        |
      | percentual -1                         |
      | percentual abc                        |
      | arquivo da rede já existe             |

  # --- Integração: node local do Hardhat em outra porta, chainId 424242 (subido pelo teste) ---

  @sem_besu
  Cenário: ChainId do node diferente do esperado: recusa e não envia nenhuma transação
    Dado um node local de teste com chainId 424242 em outra porta
    E uma chave de teste com 10 ETH no node
    Quando o script de implantação é executado esperando o chainId 424243, com cada um dos 3 formatos de URL do RPC
    Então o script recusa com código de saída diferente de zero e uma mensagem que cita "chainId"
    E o nonce da chave e o número de blocos do node não mudaram
    E a saída não contém o sentinela, o host, a chave em hex nem o caminho do arquivo da chave

  @sem_besu
  Esquema do Cenário: Saldo insuficiente (<wei> wei): recusa com mensagem clara e sem transação
    Dado um node local de teste com chainId 424242 em outra porta
    E uma chave de teste com <wei> wei no node
    Quando o script de implantação é executado com cada um dos 3 formatos de URL do RPC, no chainId 424242
    Então o script recusa com código de saída diferente de zero e uma mensagem que cita "saldo"
    E o nonce da chave e o número de blocos do node não mudaram
    E a saída não contém o sentinela, o host, a chave em hex nem o caminho do arquivo da chave

    Exemplos:
      | wei           |
      | 0             |
      | 1000000000000 |

  @sem_besu
  Cenário: Sucesso: implanta MockUSDT e VendaDados com os parâmetros das variáveis
    Dado um node local de teste com chainId 424242 em outra porta
    E o conteúdo atual de deployments/local.json foi anotado
    Quando o script de implantação é executado com sucesso em 3 redes de nomes novos, nos 3 formatos de URL
    Então o VendaDados de cada implantação tem cooperativa igual ao endereço da chave, o percentual e o prazo das variáveis
    E o arquivo da rede tem rede, chainId, cooperativa, usdt, vendaDados, blocos e hashes das transações e nenhuma URL, host nem chave
    E o deployments/local.json tem os mesmos bytes de antes
    E o stdout traz os endereços e o aviso de que o MockUSDT tem mint aberto, só para teste
    E a saída não contém o sentinela, o host, a chave em hex nem o caminho do arquivo da chave

  @sem_besu
  Cenário: A chave da implantação é a cooperativa do contrato; outra chave não é
    Dado um node local de teste com chainId 424242 em outra porta
    E o script de implantação foi executado com sucesso
    Quando a chave da implantação chama liberar de um ref sem depósito
    E uma chave diferente chama liberar do mesmo ref
    Então a chave da implantação reverte com SemDeposito e não com NaoEhCooperativa
    E a chave diferente reverte com NaoEhCooperativa
