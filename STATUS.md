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

**Pendências conhecidas dentro de M1-M4** (não bloqueiam milestones
seguintes, registradas em `docs/known-issues.md`): escape SPARQL simples
(não hardening completo), `certifier` com 4 claims ainda hardcoded
(`certificateType`, `certificateNumber`, `analysisActivity`,
`certificateReference` — aguardando extensão PROV-O em M5.5).

## verifier-demo — M9.5: 3 dos 4 passos do fluxo implementados

Fluxo de verificação definido no `CLAUDE.md` (auditor/comprador):

| # | Passo | Status |
|---|-------|--------|
| 1 | SPARQL federado real (`SERVICE`) contra coop-a + coop-b + certifier | ✅ testado e confirmado (2026-09-17) |
| 2 | Checagem ODRL de propósito (`X-Access-Purpose` vs. política real em `ontology/instances.ttl`) | ✅ testado e confirmado (2026-09-18) |
| 3 | Validação SHACL da resposta agregada (`VerifierResponseShape`, arquivo próprio) | ✅ testado e confirmado (2026-09-18) |
| 4 | Confere hash no `AssetRegistry.sol` | ⛔ não iniciado — depende de M8 (blockchain/contrato ainda não existe) |

Passos 2 e 3 testados manualmente por Juan em 2026-09-18 contra dado real
(`MS-2026-042`, não só caso sintético): ODRL — `400` (header ausente),
`403` (propósito errado), `200` (`"Compliance EUDR"`) confirmados; SHACL
da resposta agregada — `200` confirmado com o mesmo lote.

## Fora do escopo até agora (roadmap futuro, ver CLAUDE.md)

M5 (resolução did:key/did:web real), M5.5 (PROV-O de certificação), M6-M8
(revogação, SPARQL federado — já parcialmente antecipado em M9.5,
blockchain/Besu/`AssetRegistry.sol`), M10-M11 não iniciados.
