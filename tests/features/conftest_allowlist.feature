# language: pt
# Proteção da allowlist de tests/conftest.py: a limpeza de AnchoringTask órfã só pode apagar
# tarefas de batchNumber de TESTE; tarefa de batchNumber fora da lista (dado real) nunca.
# Cada cenário roda para coop-a e coop-b (fixture `node` parametrizada nos step defs).
Funcionalidade: A limpeza de tarefas de teste respeita a allowlist e não apaga tarefa de lote real

  Cenário: Tarefa órfã fora da allowlist sobrevive à limpeza; a da allowlist é apagada
    Dado uma AnchoringTask órfã com batchNumber fora da allowlist e outra órfã com batchNumber da allowlist, plantadas direto no Fuseki
    Quando a limpeza de teste roda
    Então a tarefa fora da allowlist continua no Fuseki e a da allowlist foi apagada
    E ao final só a tarefa plantada fora da lista é removida e o Fuseki não fica com resíduo
