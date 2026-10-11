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

## M5 — resolução de DID real (did:web dos nós, did:key dos produtores) — ✅ testado e confirmado

Escopo deliberadamente só de RESOLUÇÃO de identificador; verificação de
assinatura de credencial fica pra M7 (OID4VP) — ver
`docs/notas-arquitetura/m5-verificacao-assinatura-adiada.md`.

**did:web dos issuers** (coop-a, coop-b, certifier): `did_endpoint.py` novo em
cada nó, `GET /.well-known/did.json`. Publica o DID Document (DID Core,
`verificationMethod` do tipo `JsonWebKey2020`, `publicKeyJwk` Ed25519) usando
a MESMA chave de `keys.py` que assina os SD-JWT VC em `/credential` — o
DID Document é consistente com as credenciais realmente emitidas. `DID_WEB`
em `config.py` (`did:web:localhost%3A800N`) passa a ser resolvível, não só
uma string no claim `iss`. Desvio de ambiente documentado: dev local em HTTP
puro (a spec did:web exige HTTPS).

**did:key dos produtores**: `scripts/generate_did_key.py` gera par Ed25519
real por produtor (`fixtures/producer-keys/*.pem`, git-ignorado) e deriva o
`did:key` (multicodec `0xed01` + base58btc, prefixo multibase `z`). Os
`did:key` de exemplo dos `.ttl` (coop-a, coop-b) agora são derivados de chave
real, não string inventada. Dependência nova em `coop-a/requirements.txt`:
`base58` (só usada pelo script, não pelo runtime).

**Remoção de hardcodes sem dado real**: `certificateReference` saiu da
credencial do coop-a; `certificateReference`, `certificateType`,
`certificateNumber` e `analysisActivity` saíram da do certifier. Antes eram
literais fixos; agora ficam ausentes até M5.5 (PROV-O de certificação). Ver
`docs/known-issues.md` e `docs/notas-arquitetura/certifier-claims-hardcoded-m4.md`.

**Ferramenta de teste**: `scripts/test_credential_flow.py` executa o fluxo
OID4VCI completo (token → nonce → proof JWT assinada com a chave do produtor
→ credential) e decodifica header, payload e disclosures. Flag `--demo` dá um
resumo legível para tela projetada.

**Correção pós-entrega**: `sample-instance.ttl` (coop-a e coop-b) usava blank
node no `geo:Point`, o que duplicava a geometria no Fuseki a cada execução do
`pipeline.py`; agora o ponto tem URI fixa (reinserir é idempotente). O
arquivo do coop-a também tinha comandos PowerShell colados por engano no
final, que quebravam o parse.

**Pendente (M5.5, não iniciado)**: registro de produtor com rotação de
chaves; PROV-O para a atividade de análise do certifier (volta
`certificateType`/`certificateNumber`/`analysisActivity`/`certificateReference`).

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

## M9.5 (parte 2) — checagem ODRL de propósito + validação SHACL da resposta agregada — ✅ testado e confirmado

Duas das partes que a entrega anterior deixou explicitamente de fora,
implementadas em cima do mesmo endpoint `GET /verify/{batch_number}`.

**Checagem ODRL de propósito** (`verifier-demo/verify_endpoint.py`):
header `X-Access-Purpose` obrigatório, comparado (string local, sem motor
ODRL) contra o literal `"Compliance EUDR"` — mesmo `odrl:rightOperand` de
`:Politica_Acesso_EUDR_Silvestre` em `ontology/instances.ttl` (linhas
~568-594). Ausente → 400; valor errado → 403. Cobre só a metade "purpose"
da política real, não a metade "assignee" (checagem de identidade do
requisitante fica pra depois, com autenticação real).

Correção pós-entrega registrada: a primeira versão dessa checagem usava
`"EUDR_verification"`, valor de uma mensagem avulsa de chat que nunca foi
commitada no repo — engano do assistente (Claude) ao não conferir a fonte
versionada antes de implementar. Corrigido no commit `853893c`. Lição
completa em `docs/known-issues.md`.

**Validação SHACL da resposta agregada**: shape nova
`VerifierResponseShape` em `ontology/verifier-response-shape.ttl` — arquivo
SEPARADO de `ontology/validation-shapes.ttl` (o canônico do Ivan valida
regra de negócio sobre PRODUTO; esta valida a FORMA da resposta que o
verifier-demo monta, não uma regra legal sobre mel). Exige
`agritrust:batchNumber` e `productType`; se `certification` estiver
presente na resposta, exige `legalStatus` e `qualityStatus` dentro dela.
`verify_endpoint.py` monta um grafo RDF em memória (nunca persistido em
Fuseki) a partir do resultado da query federada e roda `pyshacl.validate`
contra essa shape antes de serializar o JSON — `conforms=False` vira `500`
(erro de sistema: dado incompleto vindo dos outros nós, não erro do
requisitante).

Pergunta registrada pro Ivan em `docs/perguntas-para-ivan.md` (item 3):
se separar a shape de resposta da shape de produto faz sentido, ou se
deveria reusar/estender as shapes de produto.

**Dependências novas** em `verifier-demo/requirements.txt`: `rdflib`,
`pyshacl` (mesmas libs já usadas em coop-a/coop-b/certifier pro pipeline
SHACL de entrada).

**Testado por Juan em 2026-09-18**, contra dado real (`MS-2026-042`, não
só caso sintético): ODRL — `400` (header ausente), `403` (propósito
errado), `200` (`"Compliance EUDR"`) confirmados; validação SHACL da
resposta agregada — `200` confirmado com o mesmo lote.

## Ajuste pós-demo — floralOrigin migrado de certifier para coop-a/coop-b

`floralOrigin` (origem floral do lote) saiu de `certifier/credential_endpoint.py`
(e do fixture `certifier/data/sample-lab-result-*.ttl`) e passou a ser lido de
`:hasFloralOrigin` no recurso do LOTE em `coop-a`/`coop-b`
(`credential_endpoint.py` + `data/sample-instance.ttl`), como claim
disclosable junto de `producerName`/`producerDID`. A query federada de
`verifier-demo/verify_endpoint.py` também migrou: a busca saiu do bloco
`SERVICE` do certifier e entrou nos dois ramos do `UNION` (coop-a local e
coop-b via `SERVICE`); na resposta JSON, o campo saiu de `certification` e
passou a ficar dentro de `batch`.

**Motivo**: a narrativa canônica do Ivan (relato do lote MS-2026-042) mostra
o produtor declarando a origem floral por voz na colheita ("mel silvestre da
aroeira") — o laboratório nunca confirma isso na história. Campos de
qualidade físico-química (umidade, HMF, acidez, diástase) permanecem no
certifier, sem mudança — são sempre medição de laboratório, confirmado tanto
pela ontologia (`rdfs:domain` genérico, mas o conteúdo é sempre
físico-químico/analítico) quanto pela narrativa.

**Testado por Juan** contra os 4 nós reais: `coop-a --demo` mostra
`floralOrigin` nas disclosures; `certifier --demo` não mostra mais;
`GET /verify/MS-2026-042` (`X-Access-Purpose: Compliance EUDR`) devolve
`floralOrigin` dentro de `batch`, com o valor real.

## M8 (ancoragem blockchain) — progresso inicial

Confirmados os 5 contratos na rede Semear (chain ID 648, zero gas) via
`AddressDiscovery` — `AccessChannelManager`, `SchemaRegistry`,
`ProcessRegistry`, `TransactionOrchestrator`, `AssetRegistry` — todos com
bytecode real (`eth_getCode`) e `getVersion()='1.0.0'` nos 4 que implementam
essa interface. Canal `"mel-trust"` criado on-chain com sucesso (tx
`0xe3bf7847fba0e36292969ef1219967148d33fbc11899142a92eb7254c414b1a4`), conta
admin `0xf39Fd6...2266` com `CHANNEL_AUTHORITY_ROLE` e
`CHANNEL_ADMIN_ROLE` confirmados via `hasRole()`. Detalhes e o que falta:
`docs/notas-arquitetura/m8-ancoragem-blockchain.md`.

## Holder da VC de certificação — certifier ganha pre_authorized_code por lote

`certifier` ganha `pre_authorized_code` por lote
(`POST /lote/{batch_number}/pre-authorized-code`, GET trocado por POST na
mesma entrega — efeito colateral não deveria ser GET), gerado só após
consulta federada + SHACL regulatório aprovar o lote. Cobertura E2E
confirmada com mel nativo + meliponário + licença ambiental. `known-issues.md`
atualizado com 2 pendências conscientes: bypass legado do certifier sem
uso único, e ausência de autenticação inter-nó no novo endpoint.

## M8 — ancoragem real dos 3 eventos (lote, laudo, certificação) — completo

Schema (`honey-validation-shapes`, `SchemaRegistry`) e processos
(`honey-batch-registration`, `honey-lab-analysis`,
`honey-certification-document` — um por `natureId`, `HoneyApis`/
`HoneyNativeStinglessBee`) registrados no `ProcessRegistry`, canal
`"mel-trust"`. `lab` ganhou conta Besu própria
(`lab/keys/lab-besu.key`) e foi adicionado como membro do canal
(`addChannelMembers`, assinado pela conta admin) — 4 contas de nó membros agora
(coop-a, coop-b, certifier, lab).

**Ancoragem real via `TransactionOrchestrator.submitTransaction`** (ABI
void, `outputs=[]` — achado desta entrega: a suposição inicial de retorno
`bytes32` causava `BadFunctionCallOutput`, não revert):
- `coop-a`/`coop-b`: `POST /lote` ancora o lote como asset (`createAsset`,
  `processId="honey-batch-registration"`) depois do insert no Fuseki.
- `lab`: `POST /analise` ancora o laudo como documento filho
  (`createDocument`, `processId="honey-lab-analysis"`,
  `parentAssetId=keccak256(batchNumber)` — vincula ao asset do lote).
- `certifier`: `POST /credential` ancora a VC de certificação como
  documento filho (`processId="honey-certification-document"` — processo
  NOVO, `action=9`/`CREATE_DOCUMENT`; o processo `"honey-certification"`
  original, criado com `action=0`/`CREATE_ASSET` por engano numa etapa
  anterior desta mesma entrega, fica ÓRFÃO, documentado, sem uso
  planejado — contrato não tem delete de processo).

Resposta dos 3 endpoints ganha `anchoringStatus` explícito (`"anchored"`
com `txHash`, ou `"anchoring_failed"` com `anchoringError`, nunca ausente
silenciosamente) — falha de ancoragem NUNCA reverte a inserção no Fuseki
nem bloqueia a emissão da credencial (decisão do usuário).

**Verificação de integridade on-chain** em `verifier-demo` (`GET
/verify/{batch_number}` ganha campo `onChainIntegrity`): lê os 3 assetIds
via `AssetRegistry.getAsset()` (assinatura confirmada nesta entrega, struct
de 16 campos) e, para lote/laudo, recalcula o hash a partir de uma
reconstrução via SPARQL `CONSTRUCT` contra o Fuseki de origem, comparando
com `dataHashes[0]` do contrato (`"confirmed"`/`"mismatch"`/
`"not_anchored"`). Certificação é marcada `"not_verifiable_via_query"` por
design — o hash ancorado é do SD-JWT completo (salt aleatório por
disclosure, nunca persistido em nenhum Fuseki), só verificável quando o
holder apresenta a credencial (OID4VP, ainda não implementado — ver
`docs/notas-arquitetura/m5-verificacao-assinatura-adiada.md`).

**2 bugs de hash corrigidos** (`coop-a` e depois `coop-b`,
`lote_endpoint.py`):
1. Não-determinismo: `rdflib.Graph.serialize(format="nt")` não garante
   ordem de linha entre processos Python — o mesmo grafo serializado em
   processos diferentes produzia hashes diferentes. Corrigido com
   `hash_utils.py`/`deterministic_nt_hash` (ordena as linhas N-Triples
   antes de hashear).
2. Incompletude: o grafo em memória só ganhava as triplas do produtor
   (`rdf:type`/`foaf:name`/`hasDID`) quando ele era NOVO — quando
   reaproveitado (`find_existing_producer`), o hash ancorado refletia só
   3 triplas (lote), não as 6 que um verificador externo reconstrói via
   `CONSTRUCT` (produtor existe no Fuseki de qualquer forma). Corrigido
   ancorando o hash de um grafo SEMPRE reconstruído via o mesmo
   `CONSTRUCT` que o verifier usa, não o objeto `g` em memória.

`docs/referencia-contratos/README.md` criado nesta entrega — cópia
literal das interfaces confirmadas dos 6 contratos (AddressDiscovery,
AccessChannelManager, SchemaRegistry, ProcessRegistry,
TransactionOrchestrator, AssetRegistry), cada trecho marcado CONFIRMADO
(testado contra o node real) ou NÃO CONFIRMADO.

18 cenários BDD passando (`tests/features/*.feature`), suíte confirmada
repetível (2 rodadas seguidas, resultado idêntico, sem reiniciar nada
entre elas — a rede Semear é real e persistente entre execuções, não um
mock que reseta).

## M7 — OID4VP: apresentação e validação de VC com prova de posse fresca — completo

Fecha o ciclo que M8 deixou aberto para certificação (`"not_verifiable_via_query"`):

- **coop-a (Holder)**: `POST /lote/{batch_number}/retirar-certificacao` —
  roda o fluxo completo contra o certifier (pre-authorized-code → /token
  → /nonce → /credential, assinado com a própria chave do issuer da
  coop-a, simplificação de MVP documentada) e persiste o SD-JWT completo
  como LITERAL no próprio Fuseki, vinculado ao lote via
  `agritrust:hasCertificationCredential` (não RDF estruturado — a VC já é
  auto-contida/assinada).
- **verifier-demo**: `POST /presentation-request/{batch_number}` — gera
  um `nonce` único por solicitação (mesmo mecanismo de `c_nonce`,
  `secrets.token_urlsafe`), vinculado ao `batch_number` e a um
  `request_id`, `usado: False`.
- **coop-a**: `POST /present/{batch_number}` — busca a VC guardada,
  monta a VP real no formato SD-JWT (`<Credential-JWT>~<Disclosures>~<KB-JWT>`):
  Key Binding JWT (`typ: "kb+jwt"`) assinado com a MESMA chave cuja parte
  pública já está em `cnf.jwk` da credencial (confirmado no código antes
  de implementar — não é suposição), payload `{aud, nonce, iat, sd_hash}`.
  `sd_hash` calculado conforme a spec SD-JWT (SHA-256 sobre
  `"<Credential-JWT>~<Disclosures>~"`, base64url sem padding) — inclusão
  confirmada como não tendo bloqueio técnico (sem dependência circular),
  implementada de verdade, não omitida.
- **verifier-demo**: `POST /present-response/{batch_number}` — recebe a
  VP e valida, nessa ordem: nonce (existe, não usado, corresponde ao
  lote — consumido definitivamente aqui, mesma decisão já aplicada ao
  `c_nonce`); `sd_hash` recalculado vs. o do KB-JWT; assinatura do KB-JWT
  verificada contra `cnf.jwk` (extraído do Credential-JWT); e só então
  recalcula o hash do Credential-JWT+Disclosures (mesma string do
  `sd_hash`, hasheada com `keccak256` — confirmado ser exatamente o que
  `certifier/anchoring.py` ancorou) e compara com `dataHashes[0]` lido via
  `AssetRegistry.getAsset()` (reaproveita a leitura on-chain de M8).
  Resultado substitui `onChainIntegrity.certificacao`:
  `"confirmed"` (bateu), `"mismatch"` (não bateu),
  `"invalid_presentation"` (nonce/sd_hash/assinatura inválidos — distinto
  de `"not_verifiable_via_query"`, que significava "não dava pra
  verificar por este meio", situação de antes de qualquer VP existir).

Testado de ponta a ponta com dado real (não só asserção de teste):
`{"lote": "confirmed", "laudo": "confirmed", "certificacao": "confirmed"}`
confirmado manualmente via chamada direta aos 5 passos do fluxo.

22 cenários BDD passando, suíte confirmada repetível (2 rodadas seguidas,
resultado idêntico, sem reiniciar nada entre elas).

## Outbox de ancoragem (coop-a e coop-b) — retry sem reenvio — ✅ testado e confirmado

Falha de ancoragem em `POST /lote` (`anchoring_failed`) deixava o lote no
Fuseki sem nenhum caminho de recuperação. Agora há uma marca de ancoragem
por lote e um retry manual que não reenvia transação já feita.

- **`AnchoringTask` em recurso SEPARADO** (`:AnchoringTask_<slug(batchNumber)>`,
  status `pending` | `anchored` | `mismatch`, `:anchoringTxHash` quando
  houver), nunca no recurso do lote. O hash ancorado é o keccak do grafo de
  `_LOTE_CONSTRUCT_TEMPLATE`, que seleciona triplas por padrão fixo; a
  tarefa fica fora desse padrão, então gravar/atualizar status NÃO altera o
  hash e `GET /verify` (`verificacao_hash_onchain.feature`) segue GREEN. A
  tarefa liga ao lote por `:anchoringBatch`, não por `agritrust:batchNumber`
  — `find_existing_lote` casa qualquer recurso com esse predicado e trataria
  a tarefa como lote duplicado.
- **Ordem em `POST /lote`**: grava `pending` → ancora → grava `anchored`.
  Se o processo cair entre a tx e a atualização, o retry cobre. O contrato
  da resposta (`anchored` / `anchoring_failed`) não mudou.
- **`POST /anchoring/retry`** (manual, sem scheduler na PoC): para cada
  tarefa `pending` recalcula o hash do lote (mesmo CONSTRUCT +
  `deterministic_nt_hash` de hoje) e consulta `AssetRegistry.getAsset()`
  (`anchoring.get_onchain_data_hash`) ANTES de enviar qualquer coisa:
  asset existe e hash bate → marca `anchored` sem reenviar
  (`already_onchain`); não existe → envia a tx e registra o `txHash`
  (`sent`); falha de consulta ao RPC propaga como erro daquela tarefa
  (nunca reenvia às cegas); lote sumido do Fuseki → `lote_not_found`.
- **`mismatch`**: asset on-chain com hash DIFERENTE do recalculado nunca
  vira `anchored` — a tarefa é marcada `mismatch` com os dois hashes no
  `detail` e o erro é logado. É REEXAMINADO a cada retry (re-reportado, sem
  reenviar nada); só vira `anchored` se o dado for corrigido e o hash
  passar a bater.
- **Cobertura**: `tests/features/ancoragem_outbox.feature` (3 blocos:
  pendente ainda não on-chain, já ancorado com recibo perdido, segundo
  retry sem nada a fazer; + cenário de mismatch), com os step defs
  parametrizados por nó (`[coop-a]`/`[coop-b]`). "Nada reenviado" é
  verificado pelo nonce da conta Besu do nó, não pela resposta do endpoint.
  O mismatch é plantado sem adulterar código de produção: `POST /lote` real
  ancora H1 e o `rdf:type` do lote é trocado direto no Fuseki (H2 ≠ H1).
- **`tests/conftest.py`**: limpa `AnchoringTask` órfãs (sem lote) antes e
  depois de cada teste, restrito a uma allowlist dos batchNumbers dos
  testes. Necessário porque testes com batchNumber fixo (ex.: `MN-2026-015`)
  apagam o lote mas o asset on-chain persiste; na rodada seguinte a
  ancoragem reverte e a tarefa fica `pending` sem lote.

Suíte: 26 passed, 3 deselected (`@futuro`) — os 22 anteriores + 4 do outbox
(2 cenários × coop-a/coop-b). Estável em 5 rodadas seguidas e em execuções
posteriores (última: `26 passed in 311s`).

**Confirmado por Juan em 2026-10-09** (leu o resultado `26 passed` da suíte).

**Ressalva — dependência de ambiente**: a suíte só passa com a máquina
acordada e o túnel SSH do Besu ativo (`127.0.0.1:8545`). Suspensão do Windows
ou queda do túnel derruba a rodada; o preflight de `tests/conftest.py` agora
encerra a suíte na primeira falha com "RPC/túnel não respondeu", e os módulos
de ancoragem esperam o recibo no máximo 60 s por transação. Duas rodadas
anteriores falharam sem causa comprovada. Ver `docs/known-issues.md`.

**Fora do escopo / pendências conhecidas**: `lab` e `certifier` ainda sem
outbox; allowlist de `tests/conftest.py` exige o prefixo de todo teste novo
com batchNumber sintético; o retry pode correr contra uma tx ainda em voo
(`Known transaction` — fica `pending`, correção `in_flight` proposta, não
implementada). Tudo em `docs/known-issues.md`.

Atualização (2026-10-11): o `in_flight` foi implementado (ver a entrada do
`in_flight`) e o outbox do lab também (ver a entrada do lab). O certifier
continua sem outbox.

## Outbox: retry trata "Known transaction" como in_flight (coop-a e coop-b) — ✅ testado e confirmado

Antes, um retry que encontrasse a tx original ainda na fila do Besu
(`getAsset` só enxerga tx minerada) reenviava, recebia `Known transaction` e
respondia `outcome: "failed"` com ERROR no log — ruído, a tarefa seguia
`pending`. Agora, em `_retry_one`, quando o envio falha o retry reconsulta
`getAsset`: asset existe => `already_onchain`/`mismatch`; não existe e o erro é
`Known transaction` => `in_flight` (tarefa segue `pending`, log em INFO);
qualquer outro erro => `failed` como antes. Revert real nunca vira `in_flight`.
A lógica de "asset já existe" foi extraída para `_resolve_existing`, usada nos
dois pontos. `anchoring.anchor_batch` deixou de logar ERROR para esse caso.

Feito em BDD: 2 cenários novos em `ancoragem_outbox.feature` (RED confirmado
antes: `outcome 'failed'` com o erro `Known transaction`). **Exceção
documentada**: a janela "em voo" da chain real não é reproduzível (retry leva
~2,5 s até enviar vs. bloco de 2 s), então o cenário injeta só a resposta do nó
ao envio, via `tests/support/run_retry_injected.py` (`retry_anchoring()` real em
processo próprio); detalhes e medições em `docs/known-issues.md`.

Suíte: 30 passed, 3 deselected — 2 rodadas seguidas (os 26 anteriores + 4
novos: 2 cenários × coop-a/coop-b).

**Confirmado por Juan em 2026-10-11** (leu `35 passed` nas duas rodadas da suíte
completa, que inclui estes 4 testes).

Mesma ressalva de ambiente: depende de máquina acordada e túnel do Besu ativo.

## Outbox de ancoragem do laudo (lab) — ✅ testado e confirmado

`lab` ganha o mesmo outbox do lote: `POST /analise` grava uma `AnchoringTask`
em recurso SEPARADO no Fuseki do lab (`pending` → ancora → `anchored`; o hash é
o do CONSTRUCT do laudo, que a tarefa não alcança) e `POST /anchoring/retry`
(manual) reconcilia: asset já on-chain com hash igual → `anchored` sem reenviar
(`already_onchain`); hash diferente → `mismatch` (nunca anchored, reexaminado a
cada retry); não existe → envia (`sent`, txHash registrado); mesma lógica de
`in_flight` do lote (envio responde `Known transaction`, asset não existe → a
tarefa segue `pending`, INFO no log).

Específico do laudo (documento filho, `assetId = keccak256(batchNumber +
"-lab-analysis")`, `parentAssetId = keccak256(batchNumber)`):
- **`waiting_parent`**: se o asset do lote ainda não existe on-chain, o retry
  NÃO envia (a tx reverteria); a tarefa segue `pending`.
- **natureId indisponível**: o `productType` vem de consulta federada ao
  coop-a/coop-b; se não resolve, `failed` com "natureId indisponível: …", nada
  enviado, tarefa `pending`.
- **Um caminho de hash só**: `POST /analise` passou a ancorar o hash de
  `compute_laudo_hash` (CONSTRUCT no Fuseki do lab + `deterministic_nt_hash`),
  a MESMA função do retry, em vez do hash do grafo em memória. Retry e POST não
  podem divergir; a checagem independente continua sendo o `GET /verify` do
  verifier-demo (réplica própria do CONSTRUCT).

Feito em BDD (`tests/features/ancoragem_outbox_lab.feature`, 5 cenários; RED
confirmado antes: 404 em `POST /anchoring/retry`). A classificação do retry foi
comparada com a do coop-a: a parte pós-envio é idêntica; diferem só o nome
`laudo_not_found` (vs. `lote_not_found`), `waiting_parent` e a falha de natureId,
que existem só no lab. `in_flight` usa a mesma exceção de teste do lote (só a
resposta do nó ao envio é simulada).

`tests/conftest.py`: a limpeza de tarefas órfãs agora cobre também o Fuseki do
lab; allowlist com `MS-2026-LABOUT[1-6]-…` e, corrigindo uma lacuna antiga,
`MS-2026-OUTBOX[1-5]-…` (os testes de in_flight/revert do lote usam `OUTBOX4/5`
e a allowlist só tinha `[123]`).

Suíte: 35 passed, 3 deselected — 2 rodadas seguidas (os 30 anteriores + 5 do
outbox do lab). **Confirmado por Juan em 2026-10-11** (leu `35 passed` nas duas
rodadas). Uma tentativa anterior falhou na 2ª
rodada (2 failed) por asserções globais vendo tarefas de testes antigos de
batchNumber fixo (MS-2026-042, MN-2026-015, MS-2026-042-LAB-REJEITADO, que
repostam o laudo a cada rodada). Resolvido a pedido do Juan: o `conftest.py`
limpa no Fuseki do lab todas as tarefas da allowlist (órfãs ou não), e as
asserções "nada a processar"/"nenhum ERROR" do lab voltaram a valer para o
Fuseki inteiro; 2 rodadas seguidas, 35 passed (ver `docs/known-issues.md`).
Mesma ressalva de ambiente: depende de máquina acordada e túnel do Besu ativo.
**Fora do escopo**: `certifier` ainda sem outbox.

