# language: pt
# Proteção da allowlist de tests/conftest.py: a limpeza de AnchoringTask órfã só pode apagar
# tarefas de batchNumber de TESTE; tarefa de batchNumber fora da lista (dado real) nunca.
# Cada cenário roda para coop-a e coop-b (fixture `node` parametrizada nos step defs).
Funcionalidade: A limpeza de tarefas de teste respeita a allowlist e não apaga tarefa de lote real

  Cenário: Só a tarefa órfã da allowlist é apagada; a de fora da lista e a de lote que ainda existe sobrevivem
    Dado uma AnchoringTask órfã com batchNumber fora da allowlist e outra órfã com batchNumber da allowlist, plantadas direto no Fuseki
    E uma AnchoringTask com batchNumber da allowlist cujo lote AINDA existe no Fuseki
    Quando a limpeza de teste roda
    Então a tarefa fora da allowlist continua no Fuseki e a da allowlist foi apagada
    E a tarefa da allowlist cujo lote ainda existe sobrevive
    E ao final só o que o teste plantou é removido e o Fuseki não fica com resíduo
