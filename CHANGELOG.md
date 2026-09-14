# Changelog de milestones — mel-trust

Histórico do roteiro M1→M11 (ver `CLAUDE.md`). Cada entrada: o que foi feito,
decisões relevantes, o que ficou como placeholder para milestone futuro.
Confirmação de teste é sempre do Juan — só entra aqui depois de rodado e
validado por ele.

## M1 — coop-a: metadata endpoint (OID4VCI credential issuer)

Estrutura inicial dos nós + ontologia base (`bee-extension.ttl`, SHACL
shapes). `coop-a` ganha `GET /.well-known/openid-credential-issuer`
declarando `credential_configurations_supported["HoneyBatchCredential"]`.

## M1.5 — SHACL gate + coop-b + certifier

- `coop-a`: pipeline SHACL (`pyshacl`) rodando antes do dado entrar no
  Fuseki, com cobertura de Info/Warning além de Violation.
  Ver `docs/notas-arquitetura/PadraoShaclGate.md`.
- `coop-b`: segundo nó cooperativa, mesmo padrão estrutural, escopo mel
  nativo/meliponíneo.
- `certifier`: gate SHACL sobre resultado de laboratório (consulta mock),
  padrão FastAPI + Fuseki reaproveitado dos nós cooperativa.

## M1-equivalente — verifier-demo scaffold

Scaffold do `verifier-demo` (FastAPI). Lógica federada (SPARQL SERVICE
contra coop-a + coop-b + certifier, checagem ODRL, validação SHACL +
confirmação on-chain) ainda não implementada.

## M2 — token endpoint (pre-authorized_code flow)

`POST /token` em coop-a: troca `pre-authorized_code` (fixo/hardcoded no
MVP) por `access_token` (TTL configurável via `ACCESS_TOKEN_TTL_SECONDS`).

## M2.5 — nonce endpoint + fix media type

- `POST /nonce` em coop-a: emite `c_nonce` de uso único (single-use,
  consumido na validação) com TTL curto (`C_NONCE_TTL_SECONDS`, default
  300s) — desafio de frescor para o key proof do holder em M3.
- Fix cross-node: `certifier` e `coop-b` tinham `format: "vc+sd-jwt"` no
  metadata; corrigido para `"dc+sd-jwt"` (a spec SD-JWT VC mudou o media
  type em nov/2024 — conflito de nome com o media type já registrado pelo
  W3C VC Data Model. Ver `docs/notas-arquitetura/sd-jwt-vc-media-type.md`).

## M3 — credential endpoint (emissão SD-JWT VC) — ✅ testado e confirmado

**O que foi construído** em `coop-a`:
- `keys.py`: gera/carrega chave Ed25519 do issuer (`coop-a/keys/coop-a.pem`,
  git-ignorado — chave privada nunca versionada).
- `credential_endpoint.py`: `POST /credential`. Valida `access_token`
  (Bearer, RFC 6750), valida `proof` JWT do holder (decodificado mas
  **sem** verificação de assinatura — simplificação de MVP, quem barra
  replay é o `c_nonce` single-use de M2.5, não a assinatura do proof) e
  `c_nonce` embutido nele, monta claims do lote (hardcoded neste
  milestone) e assina como SD-JWT VC (`EdDSA`, `typ: dc+sd-jwt`).
- Claims sempre-visíveis: `vct`, `iss`, `iat`, `exp`, `batchNumber`,
  `productType`.
- Claims disclosable (uma Disclosure por fato, para minimização de dados
  granular): `apiaryLocation` (objeto atômico lat/lon/descrição),
  `moistureContent`, `hmfContent`, `acidityContent` (separados —
  verificador de umidade não precisa forçar revelação de HMF/acidez
  junto), `producerName`, `producerDID` (separados — comprador quer DID
  para consulta on-chain, auditor MAPA quer nome, nem sempre os dois),
  `certificateReference`.

**Testado por Juan em 2026-09-14**: fluxo completo M1→M2→M2.5→M3 rodado
manualmente em coop-a local (`/token` → `/nonce` → proof JWT → `/credential`).
SD-JWT retornado com header/payload corretos e 7 disclosures batendo com o
desenho acima.

**Placeholders explícitos para milestones futuros:**
- Dados do lote hardcoded → M4 pluga consulta real ao Fuseki pós-SHACL.
- Proof JWT do holder sem verificação de assinatura real → M5
  (resolução did:key/did:web real).
- Chave pública do issuer não publicada em `/.well-known/did.json` → M5.

## M3 — réplica pra coop-b e certifier — ✅ testado e confirmado

Mesmo mecanismo OID4VCI (`/token`, `/nonce`, `/credential`) replicado pra
coop-b e certifier, cada um com chave Ed25519 própria
(`<node>/keys/<node>.pem`, git-ignorado).

**coop-b**: mesmo tipo de credencial `HoneyBatchCredential` de coop-a, mas
claims refletindo mel nativo (Tetragonisca angustula/Jataí, não Apis
mellifera) — `batchNumber` de `coop-b/data/sample-instance.ttl`
(`MN-2026-007`). Sem `diastaseActivity` (não é parâmetro medido pra
meliponíneo nesse fixture). Sem `certificateReference` (nenhum certificado
real emitido ainda pra esse lote — não referencia nada por enquanto, em
vez de inventar um placeholder que apontaria pra lugar nenhum).
`apiaryLocation` usa coordenada fictícia própria (diferente da de coop-a),
marcada explicitamente como dado de teste não georreferenciado.

**certifier**: tipo novo `HoneyCertificationCredential`
(`vct=.../honey-certification`), distinto de `HoneyBatchCredential` — é
uma credencial de CERTIFICAÇÃO (laudo laboratorial sobre um lote já
produzido), não de lote original. Corrige de quebra um bug herdado do M1:
o metadata do certifier declarava `HoneyBatchCredential` (copiado sem
adaptar de coop-a/coop-b), agora declara o tipo certo.

Claims mapeados de propriedades já existentes na ontologia (não inventadas
agora — ver `ontology/instances.ttl` `:Certificado_Analise_Nativa` e
`:Processo_Colheita_*`): sempre-visíveis `batchNumber` (vínculo com o lote
de coop-a) e `legalStatus`; disclosable `certificateType`,
`certificateNumber`, `moistureContent`/`hmfContent`/`acidityContent`/
`diastaseActivity` (separados), `floralOrigin`, `qualityStatus`, e um
objeto único `analysisActivity` (`startedAtTime`/`endedAtTime`/
`executedBy`) — extrapolação explícita do padrão `Processo_*` da ontologia,
já que não existe instância real de "processo de análise laboratorial".
`executedBy` usa `did:web:laboratorio-ficticio.example` (nunca
`did:agritrust:*`, formato legado — decisão de DIDs do CLAUDE.md é
`did:web` para instituições). `certificateReference` fecha o vínculo
cruzado com o placeholder já citado do lado de coop-a.

**Testado por Juan em 2026-09-14**: fluxo completo `/token`→`/nonce`→
`/credential` rodado manualmente em coop-b (porta 8002) e certifier (porta
8003), SD-JWT retornado corretamente nos dois.
