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

`lab`: 5º nó real (FastAPI + Fuseki próprio, `POST /analise` — gate SHACL de
forma + persistência), consultado pelo `certifier` via SPARQL federado
(`SERVICE`). Não segue a lista M1-M4 abaixo (não emite VC nem faz OID4VCI —
escopo próprio, ver `CLAUDE.md`).

- M1 — metadata endpoint (`/.well-known/openid-credential-issuer`)
- M1.5 — gate SHACL antes da inserção no Fuseki
- M2 — `/token` (pre-authorized_code flow)
- M2.5 — `/nonce` (c_nonce single-use)
- M3 — `/credential` (emissão SD-JWT VC, Ed25519/EdDSA)
- M4 — claims via query SPARQL real contra o Fuseki (não mais hardcoded)

Todos testados e confirmados por Juan (datas em `CHANGELOG.md`).

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
`pre-authorized_code` fixo por nó; `pipeline.py` como script CLI.

## verifier-demo — M9.5: 3 dos 4 passos do fluxo implementados

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

## Suíte de testes BDD (`tests/`)

22 cenários passando (`tests/features/*.feature`, `pytest tests/`), zero
falha, suíte confirmada repetível (2 rodadas seguidas sem reiniciar nada,
resultado idêntico). 3 cenários adicionais marcados `@futuro`
(deselecionados por padrão via `pytest.ini`).

## Fora do escopo até agora (roadmap futuro, ver CLAUDE.md)

M5.5 (registro de produtor/rotação de chaves + PROV-O de certificação), M6
(revogação — SPARQL federado já antecipado em M9.5, não mais pendente),
M10-M11 não iniciados.

**M8 (ancoragem blockchain): completo.** Canal `"mel-trust"` criado
on-chain, 4 contas membros (coop-a, coop-b, certifier, lab). Schema +
processos registrados (`SchemaRegistry`/`ProcessRegistry`). Ancoragem real
via `TransactionOrchestrator.submitTransaction` nos 3 eventos (lote via
`POST /lote`, laudo via `POST /analise`, certificação via
`POST /credential`), cada resposta com `anchoringStatus`/`txHash`
explícitos. Verificação de integridade on-chain em `verifier-demo`
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
