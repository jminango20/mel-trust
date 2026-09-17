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

## M4 — coop-a: claims via query SPARQL real (Fuseki) — ✅ testado e confirmado

Substitui os claims hardcoded do M3 por uma query SPARQL real contra o
Fuseki do coop-a (dataset populado pelo gate SHACL de M1.5). Busca o lote
por `agritrust:batchNumber` (aceita `batch_number` opcional no corpo da
requisição — extensão própria do MVP, não parte da spec OID4VCI; se
ausente, usa o mesmo valor fixo do M3 como default, já que não existe
mecanismo de "escolha de lote"/credential offer no repo ainda).

**Duas mudanças de valor em relação a M3, decisões conscientes:**
- `productType` agora é o nome local da classe RDF (`"HoneyApis"`), não um
  rótulo humano (`"Apis mellifera"`) — `bee-extension.ttl` (onde vive o
  `rdfs:label` correspondente) não é carregado no Fuseki, só a instância;
  resolver isso por SPARQL puro exigiria também indexar a ontologia no
  triple store, fora de escopo aqui.
- `apiaryLocation` agora é dado real: `coop-a/data/sample-instance.ttl`
  ganhou uma geometria GeoSPARQL (`geo:Feature`/`geo:hasGeometry`/
  `geo:asWKT`, mesma convenção de `ontology/instances.ttl`), coordenada
  fictícia de teste, distinta da usada em coop-b. A query faz parse manual
  do WKT (`POINT(lon lat)`) — regex simples, não lib GIS.

`certificateReference` continua sem correspondente no grafo de coop-a
(dado pertence ao lado do certifier) — segue como placeholder, não
resolvido por esta query. Lote inexistente → `404`, não credencial vazia.

**Known issue registrado** (`docs/known-issues.md`, não versionado —
mesma situação de CLAUDE.md/CHANGELOG.md): escape SPARQL do `batch_number`
é simples (aspas/barra), não é hardening completo contra injection —
aceitável pro MVP local, precisa de parâmetros tipados/lib de escaping
robusta antes de qualquer exposição pública.

**Testado por Juan em 2026-09-17**: fluxo completo rodado contra o Fuseki
real (container `coop-a-fuseki`), incluindo o caso de erro (`batch_number`
inexistente → 404).

## M4 — coop-b e certifier: mesma migração pra query SPARQL real — ✅ testado e confirmado

**coop-b**: mesma estrutura de query de coop-a (schema RDF idêntico) —
`productType="HoneyNativeStinglessBee"` (nome da classe, mesma decisão de
coop-a), `apiaryLocation` real via geometria GeoSPARQL nova em
`coop-b/data/sample-instance.ttl` (coordenada fictícia distinta da de
coop-a), laboratório e produtor reais. Sem schema mismatch — mesma
estrutura de dado de coop-a.

**certifier**: migração PARCIAL, schema mismatch real e documentado.
Migraram pra SPARQL: `legalStatus` (sempre visível), `qualityStatus`,
`floralOrigin`, `moistureContent`/`hmfContent`/`acidityContent`/
`diastaseActivity`. Continuam hardcoded — sem tripla correspondente em
`certifier/data/sample-lab-result-valid.ttl` nem propriedade na ontologia
atual: `certificateType`, `certificateNumber`, `analysisActivity`,
`certificateReference` (ver
`docs/notas-arquitetura/certifier-claims-hardcoded-m4.md`, não
versionado — resolução prevista pra M5.5/PROV-O de certificação, não é
lacuna a fechar agora). Query do certifier ganhou uma guarda mínima
(`a ?anyType`, exige QUALQUER rdf:type) pra não casar com recurso
genérico só por ter `batchNumber`+`legalStatus` — não é filtro real de
domínio; known issue registrado (`docs/known-issues.md`) pra endurecer
antes do verifier-demo (M9.5) fazer consulta federada com mais volume.

**Testado por Juan em 2026-09-17**: fluxo completo rodado contra os
Fuseki reais de coop-b (porta 3031) e certifier (porta 3032), incluindo
caso de erro (`batch_number` inexistente → 404 nos dois).

## M9.5 — verifier-demo: `GET /verify/{batch_number}` com SPARQL federado real — ✅ testado e confirmado

Primeira fatia do papel de auditor externo do verifier-demo: dado um
`batch_number`, descobre em qual cooperativa (coop-a OU coop-b — nunca as
duas) o lote foi produzido e se já existe certificação correspondente no
certifier. NÃO implementado ainda (escopo de entrega futura, em cima
desta): checagem ODRL de propósito, validação SHACL da resposta,
conferência de hash on-chain (M11).

**SPARQL federado de verdade** (`SERVICE`), não simulação em Python: uma
única query SPARQL enviada ao Fuseki do coop-a (nó de entrada fixo,
escolha arbitrária mas consistente), com `UNION` pra achar o lote (local
em coop-a, ou via `SERVICE` remoto em coop-b) e `OPTIONAL SERVICE` pra
certificação no certifier. Nenhum dado das 3 organizações é replicado no
verifier-demo — cada leitura é direta na fonte, no momento da
verificação.

**Achado de infraestrutura relevante**: uma tentativa inicial concluiu
(erroneamente) que `SERVICE` não funcionava entre instâncias Fuseki
standalone deste projeto. Investigação mostrou que a causa real era rede
Docker — cada nó roda em `docker-compose` separado, com rede isolada;
`localhost` visto de dentro do container do coop-a aponta pro próprio
container, não pro host onde coop-b/certifier publicam suas portas.
Corrigido com `host.docker.internal` nas URLs usadas dentro das cláusulas
`SERVICE`, e `extra_hosts: host.docker.internal:host-gateway` adicionado
aos 3 `docker-compose.yml` (portabilidade Linux, onde esse hostname não
existe por padrão como no Docker Desktop). Relato completo em
`docs/notas-arquitetura/verifier-demo-service-federado.md` (não
versionado).

**Testado por Juan em 2026-09-17**: os 3 cenários via HTTP real —
`MS-2026-042` (achado em coop-a + certificação), `MN-2026-007` (achado em
coop-b, `certification: null`, sem laudo ainda — não é erro),
`LOTE-INEXISTENTE` (404).
