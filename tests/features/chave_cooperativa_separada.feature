# language: pt
# RED: a chave da cooperativa no VendaDados ainda não é separada da chave da Besu. Hoje
# `coop-a/delivery.py` lê só BESU_KEY_PATH (COOP_KEY_PATH, na importação) e `load_coop_account()`
# nunca olha o chainId. Decisão proposta: VENDA_DADOS_COOP_KEY_PATH tem precedência; sem ela, a chave
# da Besu só vale para o chainId 31337 (rede de teste local); em qualquer outro chainId, recusa.
# Testes em processo (tests/support/load_node_module.py carregando coop-a/delivery.py): nenhum serviço
# no ar. As chaves são geradas pelo teste, escritas num diretório temporário e apagadas ao fim.

Funcionalidade: Chave da cooperativa separada da chave da Besu no VendaDados

  @sem_besu
  Cenário: Sem a variável própria e no chainId 31337, vale a chave da Besu (comportamento atual)
    Dado uma chave da Besu gerada pelo teste
    E o chainId do VendaDados configurado como 31337
    Quando o delivery.py é carregado e a conta da cooperativa é pedida
    Então a conta devolvida é a da chave da Besu

  @sem_besu
  Esquema do Cenário: Com a variável própria, vale a chave própria, diferente da Besu
    Dado uma chave da Besu gerada pelo teste
    E uma chave própria da cooperativa gerada pelo teste, em outro arquivo
    E o chainId do VendaDados configurado como <chainId>
    E VENDA_DADOS_COOP_KEY_PATH apontando para a chave própria
    Quando o delivery.py é carregado e a conta da cooperativa é pedida
    Então a conta devolvida é a da chave própria
    E a conta devolvida não é a da chave da Besu

    Exemplos:
      | chainId  |
      | 31337    |
      | 11155111 |

  @sem_besu
  Cenário: Fora do 31337 e sem a variável própria, recusa e nunca usa a chave da Besu
    Dado uma chave da Besu gerada pelo teste
    E o chainId do VendaDados configurado como 11155111
    Quando o delivery.py é carregado e a conta da cooperativa é pedida
    Então a conta é recusada com um erro que cita VENDA_DADOS_COOP_KEY_PATH
    E a chave da Besu não foi usada
    E a mensagem de erro não contém a chave em hex nem o caminho de arquivo algum
    E a mensagem de erro não contém o endereço completo da cooperativa

  @sem_besu
  Cenário: Fora do 31337 com as duas variáveis no mesmo arquivo, recusa
    Dado uma chave da Besu gerada pelo teste
    E o chainId do VendaDados configurado como 11155111
    E VENDA_DADOS_COOP_KEY_PATH apontando para o mesmo arquivo da Besu
    Quando o delivery.py é carregado e a conta da cooperativa é pedida
    Então a conta é recusada com um erro que cita VENDA_DADOS_COOP_KEY_PATH
    E a chave da Besu não foi usada
    E a mensagem de erro não contém a chave em hex nem o caminho de arquivo algum
    E a mensagem de erro não contém o endereço completo da cooperativa

  # A comparação é por endereço: ler a chave da Besu para derivá-lo é inevitável, então este cenário
  # não exige "a chave da Besu não foi usada" (o do mesmo arquivo recusa antes de ler, e exige).
  @sem_besu
  Cenário: Fora do 31337 com a mesma chave em dois arquivos diferentes, recusa
    Dado uma chave da Besu gerada pelo teste
    E o chainId do VendaDados configurado como 11155111
    E VENDA_DADOS_COOP_KEY_PATH apontando para uma cópia da chave da Besu em outro arquivo
    Quando o delivery.py é carregado e a conta da cooperativa é pedida
    Então a conta é recusada com um erro que cita VENDA_DADOS_COOP_KEY_PATH
    E a mensagem de erro não contém a chave em hex nem o caminho de arquivo algum
    E a mensagem de erro não contém o endereço completo da cooperativa

  @sem_besu
  Esquema do Cenário: Arquivo da chave inexistente ou inválido dá erro claro, sem conteúdo nem caminho
    Dado uma chave da Besu gerada pelo teste
    E o chainId do VendaDados configurado como <chainId>
    E <variavel> apontando para um arquivo <estado>
    Quando o delivery.py é carregado e a conta da cooperativa é pedida
    Então a conta é recusada com um erro que cita <variavel>
    E a mensagem de erro não contém a chave em hex nem o caminho de arquivo algum
    E a mensagem de erro não contém o endereço completo da cooperativa
    E a mensagem de erro não contém o conteúdo do arquivo

    Exemplos:
      | chainId  | variavel                  | estado       |
      | 31337    | BESU_KEY_PATH             | inexistente  |
      | 31337    | BESU_KEY_PATH             | com conteúdo inválido |
      | 11155111 | VENDA_DADOS_COOP_KEY_PATH | inexistente  |
      | 11155111 | VENDA_DADOS_COOP_KEY_PATH | com conteúdo inválido |
