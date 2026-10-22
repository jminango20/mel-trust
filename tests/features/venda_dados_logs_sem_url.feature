# language: pt
# RED: logs e respostas do coop-a ainda carregam a URL do RPC do VendaDados. Uma URL pública pode levar
# chave de API (no caminho, na query ou como senha), então nem a resposta HTTP nem nenhum log em INFO ou
# acima (o padrão do uvicorn) podem conter o RPC (sentinela único por execução) nem o host. O log tem de
# continuar diagnosticável: o nome do TIPO da exceção e o batchNumber (ou a ref do contrato).
#
# Em processo (tests/support/load_node_module.py carrega coop-a/delivery.py, data_contract_endpoint.py,
# release_endpoint.py e withdraw_endpoint.py) com caplog. O RPC vem de VENDA_DADOS_RPC_URL (lida a cada
# chamada). Precisa do Fuseki do coop-a e do coop-a (POST /data-contract) no ar; os cenários de
# "chainId errado" precisam também do node 8546 no ar (rede acessível, chainId diferente). O nível DEBUG
# (urllib3/web3) é só relatado, fora da suíte: tests/support/relato_logs_debug.py.
# Os controles (release e withdraw) já logam só o tipo da exceção/etapa e devem passar desde já.

Funcionalidade: Logs e respostas do VendaDados sem a URL do RPC

  @sem_besu
  Esquema do Cenário: Entrega no POST /lote com a rede fora do ar ou errada
    Dado o RPC do VendaDados em "<rede>"
    Quando a entrega do POST /lote de um lote sintético é tentada
    Então a resposta e os logs em INFO ou acima não contêm o sentinela nem o host
    E o log diz o tipo da exceção e o batchNumber

    Exemplos:
      | rede                   |
      | url com chave no caminho |
      | chave na query         |
      | senha no usuário       |
      | chainId errado         |

  @sem_besu
  Esquema do Cenário: Retry de entrega com a rede fora do ar ou errada
    Dado o RPC do VendaDados em "<rede>"
    E um lote sintético com quantidade e uma tarefa de entrega pendente
    Quando o retry de entrega é executado
    Então a resposta e os logs em INFO ou acima não contêm o sentinela nem o host
    E o log diz o tipo da exceção e o batchNumber

    Exemplos:
      | rede                   |
      | url com chave no caminho |
      | chave na query         |
      | senha no usuário       |
      | chainId errado         |

  @sem_besu
  Esquema do Cenário: Leitura do pagamento com a rede fora do ar ou errada
    Dado o RPC do VendaDados em "<rede>"
    E um Contrato de Dados sintético
    Quando a leitura do pagamento do contrato é executada
    Então a resposta e os logs em INFO ou acima não contêm o sentinela nem o host
    E o log diz o tipo da exceção e a ref do contrato

    Exemplos:
      | rede                   |
      | url com chave no caminho |
      | chave na query         |
      | senha no usuário       |
      | chainId errado         |

  # Controles: liberação e saque já logam só o tipo da exceção (ou a etapa), então passam desde já.
  @sem_besu
  Esquema do Cenário: Controle: liberação do pagamento com a rede fora do ar ou errada
    Dado o RPC do VendaDados em "<rede>"
    E um Contrato de Dados sintético
    Quando a liberação do contrato é executada
    Então a resposta e os logs em INFO ou acima não contêm o sentinela nem o host
    E o log diz o tipo da exceção e a ref do contrato

    Exemplos:
      | rede                   |
      | url com chave no caminho |
      | chave na query         |
      | senha no usuário       |
      | chainId errado         |

  @sem_besu
  Esquema do Cenário: Controle: saque do produtor com a rede fora do ar ou errada
    Dado o RPC do VendaDados em "<rede>"
    E um produtor sintético com carteira custodial
    Quando o saque do produtor é executado
    Então a resposta e os logs em INFO ou acima não contêm o sentinela nem o host
    E o log diz o tipo da exceção ou a etapa e a carteira

    Exemplos:
      | rede                   |
      | url com chave no caminho |
      | chave na query         |
      | senha no usuário       |
      | chainId errado         |

  # As mensagens das próprias exceções (não só o log): `_contract()` e `_read_deposit` são chamadas direto.
  # Um RPC inalcançável e um chainId errado têm de dar uma exceção cujo str() não carrega o RPC nem o host.
  @sem_besu
  Esquema do Cenário: A exceção de <funcao> não carrega a URL do RPC
    Dado o RPC do VendaDados em "<rede>"
    Quando <funcao> é chamada diretamente
    Então a exceção levantada não contém o sentinela nem o host

    Exemplos:
      | funcao           | rede                     |
      | delivery._contract()          | url com chave no caminho |
      | delivery._contract()          | chave na query           |
      | delivery._contract()          | senha no usuário         |
      | delivery._contract()          | chainId errado           |
      | data_contract_endpoint._read_deposit() | url com chave no caminho |
      | data_contract_endpoint._read_deposit() | chave na query           |
      | data_contract_endpoint._read_deposit() | senha no usuário         |
      | data_contract_endpoint._read_deposit() | chainId errado           |

  # Exceção bruta DEPOIS de o _contract() passar (chainId certo, node 8546 no ar): entregas().call() levanta
  # uma ConnectionError cujo texto traz o sentinela e o host. Nem a resposta nem os logs podem repeti-los.
  @sem_besu
  Cenário: Exceção bruta depois do _contract() na entrega do POST /lote
    Dado o node local do VendaDados no ar e entregas().call() levantando uma exceção bruta com o sentinela e o host
    Quando a entrega do POST /lote de um lote sintético é tentada
    Então a resposta e os logs em INFO ou acima não contêm o sentinela nem o host
    E o log diz o tipo da exceção e o batchNumber

  @sem_besu
  Cenário: Exceção bruta depois do _contract() no retry de entrega
    Dado o node local do VendaDados no ar e entregas().call() levantando uma exceção bruta com o sentinela e o host
    E um lote sintético com quantidade e uma tarefa de entrega pendente
    Quando o retry de entrega é executado
    Então a resposta e os logs em INFO ou acima não contêm o sentinela nem o host
    E o log diz o tipo da exceção e o batchNumber
