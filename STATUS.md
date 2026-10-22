# Status do projeto — mel-trust

Snapshot do roteiro M1→M11 (ver `CLAUDE.md` para o roteiro completo e regra
de gating). Histórico detalhado por milestone: `CHANGELOG.md`. Pendências
técnicas conhecidas: `docs/known-issues.md`.

**Regra de gating** (`CLAUDE.md`): nada aqui é "concluído" sem confirmação
explícita do Juan de que rodou e testou. Onde um passo foi implementado mas
ainda não confirmado, está marcado como tal, não como concluído.

## Nós cooperativa + certificador — M1 a M4 completos e testados

`coop-a`, `coop-b`, `certifier`: mesmo padrão estrutural (FastAPI + Fuseki
+ pipeline SHACL → SPARQL Update), todos com:

- M1 — metadata endpoint (`/.well-known/openid-credential-issuer`)
- M1.5 — gate SHACL antes da inserção no Fuseki
- M2 — `/token` (pre-authorized_code flow)
- M2.5 — `/nonce` (c_nonce single-use)
- M3 — `/credential` (emissão SD-JWT VC, Ed25519/EdDSA)
- M4 — claims via query SPARQL real contra o Fuseki (não mais hardcoded)

Todos testados e confirmados por Juan (datas em `CHANGELOG.md`).

**`lab` (fora da lista M1–M4 acima):** 5º nó real (FastAPI + Fuseki próprio, `POST /analise` — gate SHACL de
forma + persistência), consultado pelo `certifier` via SPARQL federado
(`SERVICE`). Não segue a lista M1-M4 acima (não emite VC nem faz OID4VCI —
escopo próprio, ver `CLAUDE.md`).

**Ajuste pós-demo, testado**: `floralOrigin` é campo do PRODUTOR (claim
disclosable de `batch`, em coop-a/coop-b — `:hasFloralOrigin` no recurso do
lote), não mais do certificador. Motivo: na narrativa canônica do Ivan, é o
produtor quem declara a origem floral na colheita; o laboratório nunca
confirma isso. Os campos físico-químicos (umidade, HMF, acidez, diástase)
continuam no certifier, sem mudança — são sempre medição de laboratório. A
query federada do verifier-demo (`/verify`) já reflete essa mudança: o campo
sai de `certification` e passa a vir dentro de `batch`.

**Pendências conhecidas dentro de M1-M4** (não bloqueiam milestones
seguintes, registradas em `docs/known-issues.md`): escape SPARQL simples
(não hardening completo); `certifier` sem 4 claims (`certificateType`,
`certificateNumber`, `analysisActivity`, `certificateReference` — removidos
no M5 por falta de dado real, voltam com a extensão PROV-O em M5.5);
`pre-authorized_code`: resolvido (código dinâmico, de uso único) para lotes
cadastrados via `POST /lote` em coop-a/coop-b e, no certifier, para o fluxo
`POST /lote/{batch}/pre-authorized-code`; o código fixo legado
(`config.PRE_AUTHORIZED_CODE`) continua aceito em `/token` como bypass de
dev/teste, documentado em `docs/known-issues.md`. `pipeline.py` foi
removido — `POST /lote` é a única via de entrada.

**Topologia**: 5 nós (coop-a, coop-b, certifier, lab, verifier-demo) e
4 contas de nó membros do canal `"mel-trust"` (coop-a, coop-b, certifier,
lab — o verifier-demo só lê, não tem conta).

## verifier-demo — M9.5: os 4 passos do fluxo implementados (o passo 4 via M8)

Fluxo de verificação definido no `CLAUDE.md` (auditor/comprador):

| # | Passo | Status |
|---|-------|--------|
| 1 | SPARQL federado real (`SERVICE`) contra coop-a + coop-b + certifier | ✅ testado e confirmado (2026-09-17) |
| 2 | Checagem ODRL de propósito (`X-Access-Purpose` vs. política real em `ontology/instances.ttl`) | ✅ testado e confirmado (2026-09-18) |
| 3 | Validação SHACL da resposta agregada (`VerifierResponseShape`, arquivo próprio) | ✅ testado e confirmado (2026-09-18) |
| 4 | Confere hash no `AssetRegistry.sol` | ✅ implementado via M8 (`onChainIntegrity` em `GET /verify`) — lote/laudo comparados de verdade, certificação `"not_verifiable_via_query"` por design |

Passos 2 e 3 testados manualmente por Juan em 2026-09-18 contra dado real
(`MS-2026-042`, não só caso sintético): ODRL — `400` (header ausente),
`403` (propósito errado), `200` (`"Compliance EUDR"`) confirmados; SHACL
da resposta agregada — `200` confirmado com o mesmo lote.

## M5 — resolução de DID real — ✅ testado e confirmado

- `did:web` dos 3 issuers: `GET /.well-known/did.json` em coop-a, coop-b e
  certifier, com a mesma chave Ed25519 que assina os SD-JWT VC.
- `did:key` dos produtores derivado de chave real
  (`scripts/generate_did_key.py`, chaves em `fixtures/producer-keys/`,
  git-ignoradas); os `.ttl` de exemplo usam esses DIDs.
- Hardcodes sem dado real removidos da credencial (`certificateReference` no
  coop-a; os 4 claims acima no certifier).
- `scripts/test_credential_flow.py` (fluxo OID4VCI completo, flag `--demo`).
- Verificação de assinatura de credencial: era fora de escopo por decisão
  até M7 (`docs/notas-arquitetura/m5-verificacao-assinatura-adiada.md`) —
  M7/OID4VP já implementado e completo (ver seção própria abaixo), a
  assinatura do KB-JWT é verificada de verdade contra `cnf.jwk` em
  `POST /present-response`.

## M7 — OID4VP (apresentação e validação de VC) — completo

coop-a (Holder) persiste a VC de certificação recebida
(`agritrust:hasCertificationCredential`, literal); verifier-demo gera
presentation request com nonce único por solicitação; coop-a monta e
apresenta a VP real (Credential-JWT + disclosures + Key Binding JWT com
`sd_hash` conforme a spec SD-JWT); verifier-demo valida a VP (nonce,
`sd_hash`, assinatura do KB-JWT contra `cnf.jwk`) e confirma o hash do
Credential-JWT contra o `AssetRegistry`. Resultado:
`onChainIntegrity.certificacao` passa de `"not_verifiable_via_query"`
(M8, sem VP) para `"confirmed"` de verdade quando uma VP real é
apresentada — confirmado manualmente ponta a ponta. Detalhes completos:
`CHANGELOG.md`.

## Outbox de ancoragem (coop-a, coop-b, lab e certifier) — ✅ testado e confirmado (2026-10-12)

`POST /lote` grava uma `AnchoringTask` (recurso separado do lote — não
altera o hash ancorado) `pending` → ancora → `anchored`. `POST
/anchoring/retry` (manual) consulta `getAsset` antes de reenviar: hash bate
→ só marca `anchored`; asset ausente → envia a tx; hash diferente →
`mismatch`, reportado a cada retry e nunca marcado `anchored`; se o envio
responde `Known transaction` (tx já na fila) o resultado é `in_flight` — a
tarefa segue `pending` e a próxima passada conclui (testado e confirmado,
2026-10-11). O `lab` tem o mesmo outbox para o LAUDO
(testado e confirmado, 2026-10-11): idem, mais `waiting_parent`
quando o lote (pai) ainda não está on-chain. O `certifier` tem o mesmo outbox
para a VC de certificação (testado e confirmado, 2026-10-12): a tarefa GUARDA o
hash do SD-JWT emitido (não recalculável) e o natureId, o retry reenvia o hash
guardado, e `POST /credential` responde 409 se o lote já tem certificação.
**Lacunas conhecidas** (`docs/known-issues.md`): tarefa do certifier perdida =
hash irrecuperável; allowlist de
`tests/conftest.py` precisa de atualização a cada teste novo com
batchNumber sintético. Detalhes: `CHANGELOG.md`.

## M8 — ancoragem blockchain: completo

Canal `"mel-trust"` criado
on-chain, 4 contas de nó membros (coop-a, coop-b, certifier, lab). Schema +
processos registrados (`SchemaRegistry`/`ProcessRegistry`). Ancoragem real
via `TransactionOrchestrator.submitTransaction` nos 3 eventos (lote via
`POST /lote`, laudo via `POST /analise`, certificação via
`POST /credential`), cada resposta com `anchoringStatus`/`txHash`
explícitos, e **os três eventos têm outbox** com `POST /anchoring/retry`
(lote em coop-a/coop-b, laudo no lab, certificação no certifier; ver a
seção do outbox acima). Verificação de integridade on-chain em `verifier-demo`
(`GET /verify` ganha `onChainIntegrity`) via `AssetRegistry.getAsset()` —
lote e laudo comparados de verdade (hash recalculado via SPARQL
`CONSTRUCT`); certificação, SEM uma VP apresentada, fica
`"not_verifiable_via_query"` por design (hash do SD-JWT só verificável
via apresentação da credencial) — **M7 (OID4VP, ver seção própria acima)
fecha essa lacuna**: quando uma VP real é apresentada e validada, o
status passa a `"confirmed"`/`"mismatch"`/`"invalid_presentation"` de
verdade. Detalhes completos:
`CHANGELOG.md`/`docs/referencia-contratos/README.md`.

**Passo 4 do fluxo de `verifier-demo`** (tabela M9.5 acima, "confere hash
no `AssetRegistry.sol`"): implementado como parte deste M8, não mais
"não iniciado".

O fechamento de escopo do M8 (`CHANGELOG.md`, "M8 fechado: escopo") segue sem confirmação do Juan.

## Venda de dados (coop-a + VendaDados) — implementada pelo agente, sem confirmação do Juan

Detalhe e números: `CHANGELOG.md` (entradas "Venda de dados: estado em 2026-10-18" e "Venda de dados: fluxo do
dinheiro e acesso aos dados em 2026-10-19") e `docs/SYNC.md`.
Nada aqui está marcado como testado e confirmado: o que existe são rodadas do agente.

- **Quantidade do lote (coop-a):** `quantityLiters` opcional em `POST /lote` vira `:quantityMilliliters`
  (mL); `HoneyBatchQuantityShape`; `amount` real no `createAsset` e no retry. Fora do hash. coop-b sem
  quantidade nem entrega.
- **`VendaDados.sol`** (`contracts/venda-dados/`): cotas por mL entregue (ERC20 não transferível),
  pagamento retido e liberado, reembolso, repartição O(1), view `saldoTotalSacavel(conta)` (saldo assentado
  + pendente; a `saldoSacavel` pública não inclui o pendente). 54 testes no Hardhat com mutações. **Só roda
  no node local efêmero (8546, chainId 31337) com `MockUSDT`; não está implantado em rede real.** A Sepolia é
  a rede decidida para a demo (informada pelo Juan), **ainda não implantada**: nada foi publicado nela.
- **Entrega (coop-a):** `POST /lote` com quantidade registra a entrega no VendaDados
  (`deliveryStatus`); `DeliveryTask` em recurso separado; `POST /delivery/retry`. Carteira custodial por
  produtor em disco (só rede de teste).
- **Contrato de Dados (coop-a):** `POST /data-contract` com `contractRef` (keccak256 do JSON canônico);
  `DataContractShape` com `priceUnits` >= 1 e `validFrom` < `validUntil`; datas ISO 8601 com hora e fuso.
- **Leitura do pagamento:** `GET /data-contract/{ref}/payment` (`not_paid`, `paid`, `mismatch`,
  `outside_window`, `released`, `refunded`), lendo `depositos(ref)` sem chave; janela em UTC pelo
  timestamp do bloco.
- **Liberação do pagamento (coop-a):** `POST /data-contract/{ref}/release` só libera com status `paid`
  (confere o chainId antes de assinar `liberar`); `already_released` idempotente; 409 `not_payable` com o
  status nos demais casos; mutações verificadas. Helper `delivery.load_coop_account()` (chave da cooperativa).
- **Saque em nome do produtor (coop-a):** `POST /producers/{did}/withdraw` com a chave custodial; nunca cria
  chave; reposição de gás só em rede de teste (`PRODUCER_GAS_TOPUP_WEI`); `nothing_to_withdraw` sem
  transação; 503 com mensagem fixa.
- **Acesso aos dados (verifier-demo):** `POST /data-access/challenge/{ref}` e `GET /data-access/{lote}` com
  desafio EIP-191 assinado pela carteira do `buyerWallet`; 401 / 403 `forbidden_wallet` / 402
  `payment_required`; nonce de uso único com prazo (`CHALLENGE_TTL_SECONDS`, padrão 300 s), em memória; o
  `GET /data-contract/{ref}/payment` do coop-a devolve também o `buyerWallet`.
- **Teste de ponta a ponta:** dois produtores (1500 e 500 mL), repartição 6750000 / 2250000 e 1000000 da
  cooperativa, saques, mais o cenário de reembolso. Precisa da Besu e do node 8546.
- **Limites:** um contrato pago libera qualquer lote; o dado é entregue com o pagamento retido; o sistema
  não prova a entrega nem impede o repasse do dado; a liberação e o saque não autenticam quem chama (só
  rede local de demo, e isso precisa ser dito na demo). Reembolso: não existe rota no coop-a; no teste de
  ponta a ponta a cooperativa chama `reembolsar` direto no contrato e o sistema só lê o resultado (status
  `refunded`); o comprador pode recuperar o valor sozinho depois do prazo (`recuperarReembolso`), também
  direto no contrato. Ver `docs/known-issues.md`.
- **Rodada mais recente do agente (2026-10-19; sem confirmação do Juan):**
  `1 failed, 108 passed, 8 deselected in 307.08s (0:05:07)`; a única falha é o teste de schema, vermelho de
  propósito.
- **URLs de conexão em `127.0.0.1`** (testes, `CERTIFIER_URL`, scripts, `.env.example`): suíte de
  ~10–11 min para ~3,5 min. `did:web`, `BASE_URL` e `aud` não mudaram.

## Suíte de testes BDD (`tests/`)

Rodada mais recente (executada pelo agente, 2026-10-18; **sem confirmação do Juan**):
`1 failed, 77 passed, 5 deselected in 210.41s (0:03:30)`. A única falha é o teste de sincronização do
schema, **vermelho de propósito** (ver abaixo). Coleta conferida em 2026-10-18 com
`pytest tests/ --collect-only -q` (sem executar): 83 testes no total, 78 selecionados e 5
deselecionados (fora da suíte padrão via `pytest.ini`) = 3 `@futuro` (todos em
`test_rastreabilidade.py`) + 2 `@node_parado` (`test_venda_dados_entrega.py` e
`test_data_contract_leitura_pagamento.py`); a união de `futuro` e `node_parado` também dá 5, sem
sobreposição.

**Schema sincronizado — vermelho de propósito:** `schema_sincronizado.feature` compara o `dataHash` do
`honey-validation-shapes` ativo on-chain com o hash canônico do `ontology/validation-shapes.ttl`. Em
2026-10-12 os dois eram iguais (v2). Desde então o `.ttl` mudou 3 vezes (`HoneyBatchQuantityShape`,
descrição obrigatória na `CertificateShape`, `DataContractShape` com `priceUnits` e `sh:lessThan`) e o
`scripts/blockchain/update_schema.py` não foi rodado: o on-chain segue na v2 (`0x01ca…`) e o hash do
arquivo é outro. O teste fica vermelho até a reimplantação do schema.

Marco anterior, confirmado por Juan (`44 passed, 3 deselected`, 2026-10-12): 44 testes passando
(`tests/features/*.feature`, `pytest tests/`), zero falha. Eram os 41
anteriores (22 cenários originais + outbox de ancoragem em coop-a/coop-b +
`in_flight`/revert real + outbox do laudo no lab + outbox da VC no certifier,
ver seção acima) + 1 de schema sincronizado (que então passava) + 2 da proteção da
limpeza do `tests/conftest.py` (`conftest_allowlist.feature`, 1 cenário ×
coop-a/coop-b; regex da allowlist e `FILTER NOT EXISTS`, com mutação
verificada). De lá para cá entraram os testes da quantidade do lote, da entrega no VendaDados, do
Contrato de Dados, da leitura do pagamento e da troca de URLs (seção "Venda de dados" acima).
**Só passa com a máquina acordada, o túnel do Besu ativo e o node do VendaDados (8546) com o deploy feito**
(ver `docs/known-issues.md` e a seção Testes do `CLAUDE.md`).
3 cenários adicionais marcados `@futuro` (deselecionados por padrão via
`pytest.ini`).

## Fora do escopo até agora (roadmap futuro, ver CLAUDE.md)

- M5.5 (registro de produtor/rotação de chaves + PROV-O de certificação): não iniciado
- M6 (revogação, StatusList2021): não iniciado
- M10–M11: não iniciados
