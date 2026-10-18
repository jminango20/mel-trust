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

**Atualização (2026-10-18):** implementado depois, nas entradas abaixo: federação, ODRL e SHACL em
"M9.5" e "M9.5 (parte 2)"; confirmação on-chain em "M8 — ancoragem real dos 3 eventos"; OID4VP em
"M7". Texto original mantido.

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

**Atualização (2026-10-18):** ODRL e SHACL da resposta entraram em "M9.5 (parte 2)". A conferência de
hash on-chain foi entregue no **M8** (`onChainIntegrity` em `GET /verify`; ver "M8 — ancoragem real dos
3 eventos"), não no M11: o rótulo "M11" acima está desatualizado. Texto original mantido.

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

**Atualização (2026-10-18):** o OID4VP foi implementado depois: ver a entrada "M7 — OID4VP: apresentação
e validação de VC com prova de posse fresca". Texto original mantido.

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

**Atualização (2026-10-18):** o certifier também ganhou o outbox (ver "Outbox de ancoragem da VC de
certificação (certifier)"), então os quatro nós (coop-a, coop-b, lab e certifier) têm outbox com
`in_flight`. O "continua sem outbox" acima está desatualizado. Texto original mantido.

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

Atualização (2026-10-12): o `certifier` também ganhou o outbox (ver a entrada
do certifier). Com isso os três eventos ancorados têm retry.

## Outbox de ancoragem da VC de certificação (certifier) — ✅ testado e confirmado

**Confirmado por Juan em 2026-10-12** (leu `41 passed` nas duas rodadas).

`POST /credential` do certifier ganha o mesmo outbox do lote e do laudo, com
duas diferenças de fundo, ambas por causa do que é ancorado: o keccak256 do
**SD-JWT completo** emitido (`<Credential-JWT>~<disclosures>~`).

- **Hash guardado na emissão.** Cada disclosure leva um salt aleatório, então
  o hash do SD-JWT NÃO é recalculável a partir de nenhum dado. A `AnchoringTask`
  (recurso separado no Fuseki do certifier) GUARDA, quando a VC é emitida: o
  hash (`:anchoringDataHash`), o batchNumber e o natureId/productType
  (`:anchoringNature`) — o natureId guardado dispensa a consulta federada no
  retry. Ordem: assina o SD-JWT → grava `pending` (com hash + natureId) →
  ancora (`createDocument`, `assetId = keccak256(batchNumber + "-certification")`,
  pai `keccak256(batchNumber)`) → `anchored`. Se gravar a tarefa falhar, a VC NÃO
  é entregue (502): sem tarefa o hash seria irrecuperável.
- **Reenvio do hash guardado.** `POST /anchoring/retry` (manual) NUNCA
  recalcula: consulta `getAsset` antes de enviar, verifica o pai
  (`waiting_parent` se o lote ainda não está on-chain), reenvia o hash guardado
  e, se o envio falha, reconsulta `getAsset` antes de classificar. Mesma
  classificação do lab: `sent`, `already_onchain`, `mismatch`, `waiting_parent`,
  `in_flight` (`Known transaction`: tarefa segue `pending`, INFO no log) e
  `failed`.
- **Mismatch = divergência on-chain.** Asset já on-chain com hash DIFERENTE do
  guardado: tarefa `mismatch`, nunca `anchored`, reexaminada a cada retry.
- **409 na reemissão.** Um documento por lote: se já existe `AnchoringTask` para
  o batchNumber (em qualquer estado), `POST /credential` responde 409 ANTES de
  assinar e antes de consumir o `c_nonce` — nenhuma VC nova, nenhuma tarefa nova.
  Limite: se a tarefa for perdida, a reemissão volta a ser possível.
- **`tests/conftest.py` estendido ao Fuseki do certifier.** Limpa, antes e depois
  de cada teste, TODAS as tarefas dos batchNumbers da allowlist (órfãs ou não),
  agora com `CERTOUT[1-6]`. Necessário por dois motivos: os testes antigos de
  batchNumber fixo (`MS-2026-042`, `MN-2026-015`) emitem a VC a cada rodada com o
  asset já on-chain com outro hash (viraria `mismatch`), e a tarefa existente
  bloquearia a emissão da rodada seguinte (409). Batchs fora da lista não são
  tocados.

Feito em BDD (`tests/features/ancoragem_outbox_certifier.feature`, 6 cenários:
hash guardado ainda não on-chain; recibo perdido + segundo retry vazio; mismatch;
`waiting_parent`; `in_flight`; reemissão 409). RED confirmado antes: 404 em
`POST /anchoring/retry`, tarefa não gravada pelo `POST /credential` real,
`AttributeError` no helper de injeção e `200` na reemissão. O recibo perdido e o
mismatch usam `POST /credential` REAL e conferem o hash guardado contra
`keccak256(credential)` calculado pelo próprio teste. `in_flight` usa a mesma
exceção de teste dos outros nós (só a resposta do nó ao envio é simulada).

Suíte: **41 passed, 3 deselected** — 2 rodadas seguidas (os 35 anteriores + 6 do
certifier). Mesma ressalva de ambiente: depende de máquina acordada e túnel do
Besu ativo. **Limites conhecidos** (`docs/known-issues.md`): tarefa perdida =
hash irrecuperável (a VC já entregue fica sem ancoragem); como o hash é o do
SD-JWT completo, a divulgação seletiva real (apresentar só parte das
disclosures) não passaria na verificação do M7 — não testado com apresentação
parcial real.

## Limpeza sem mudança de comportamento (2026-10-12) — ✅ testado e confirmado

**Confirmado por Juan em 2026-10-12** (leu `41 passed` nas duas rodadas).

- **Imports sem uso removidos** (5): `Web3` em `coop-a/lote_endpoint.py` e
  `lab/analise_endpoint.py`; `time`, `Web3` e `b64url_json` em três testes do
  outbox. Antes de cada remoção, grep confirmou que nenhum outro arquivo importava
  esse nome desse módulo.
- **Constantes de Besu em `config.py`** (coop-a, coop-b, lab, certifier e, para as
  quatro de leitura, verifier-demo): endereço do TransactionOrchestrator, do
  AddressDiscovery, chainId, `RPC_URL` e nome do canal, lidos de variáveis `BESU_*`
  (`BESU_RPC_URL`, `BESU_CHAIN_ID`, `BESU_TRANSACTION_ORCHESTRATOR_ADDRESS`,
  `BESU_ADDRESS_DISCOVERY_ADDRESS`, `BESU_CHANNEL_NAME`) com os MESMOS valores de antes
  como default (o RPC continua `localhost:8545`). Os `anchoring.py` e
  `onchain_verification.py` fazem `from config import …`. `scripts/` não foi tocado.
- **`tests/support/load_node_module.py`.** Os testes do outbox carregam módulos dos nós
  (`<nó>/anchoring.py`, `lab/hash_utils.py`) por caminho, e o nome `config` existe nos 5
  nós. Sem isolação, o primeiro carregamento deixa o diretório do nó em `sys.path`, o
  `config` em cache em `sys.modules` (o segundo nó recebia o do primeiro) e, pelo
  `load_dotenv()` do `config.py`, o `.env` em `os.environ` — e foi esse último que vazou o
  `.env` do coop-a para os subprocessos dos testes de `in_flight` (lab, certifier e
  coop-b liam o Fuseki do coop-a; 3 falhas, `processed: 0`). O helper isola
  `sys.path`, `sys.modules` e `os.environ` e restaura tudo no `finally`, inclusive se o
  import falhar; os 3 carregadores dos testes viraram chamadas curtas a ele.
  `tests/support/check_load_node_module.py` (manual, fora da coleta do pytest) confirma
  que o `FUSEKI_URL` de cada nó é o do próprio nó e que o estado do processo volta ao
  inicial.
- **Resultado:** sem mudança de lógica, de nomes de função nem de formato de resposta.
  Suíte: 41 passed, 3 deselected — 2 rodadas seguidas.

**Dívida registrada:** a lógica do retry existe em 4 cópias (`lote_endpoint.py` do
coop-a e do coop-b, `analise_endpoint.py` do lab, `credential_endpoint.py` do
certifier). coop-a e coop-b são idênticos por AST; lab e certifier divergem só no
esperado (`waiting_parent`, natureId resolvido por consulta federada no lab / hash e
natureId guardados na tarefa no certifier). Qualquer ajuste de classificação precisa
ser replicado nas quatro; extrair para um módulo comum fica como decisão futura.

## Schema `honey-validation-shapes` v2 on-chain, sincronização schema ↔ arquivo e proteção da limpeza do conftest — ✅ testado e confirmado

**Confirmado por Juan em 2026-10-12** (leu `44 passed, 3 deselected`).

- **Schema na versão 2 on-chain.** O `dataHash` da v1 correspondia ao
  `ontology/validation-shapes.ttl` ANTES do commit `f905fcd` (correção do `sh:Info` de
  `HoneyNativeMoistureShape` e `CertificateShape`), e o arquivo, hasheado em CRLF no Windows,
  não reproduzia em outra máquina. A v2 foi registrada via `SchemaRegistry.updateSchema`
  (`SchemaUpdateInput{schemaId, newDataHash, channelName, description}`, sem retorno), assinada
  pela conta do certifier, com `newDataHash` = **hash canônico** (keccak256 dos bytes do
  arquivo com CRLF trocado por LF, `canonical_schema_hash` em
  `scripts/blockchain/create_schema.py`) = `0x01caa3b415dd4b930f2306b57e2de9c6bba8e7f64e85eeefb482178590e75750`.
  Antes: certifier membro do canal, `getActiveSchema` com owner = certifier, versão 1, status 0;
  simulação com `.call()` sem revert. Tx `0x5e2a1c4defd80d7c8951d375eebe51ee3fc588ba8c976b134ecfe081dc32bd1d`,
  status 1, bloco 6572849. Depois: `getActiveSchema` = versão 2, ACTIVE, `dataHash` canônico;
  `getSchemaByVersion(…, 1)` = DEPRECATED. A descrição on-chain foi conferida byte a byte
  contra a enviada (idêntica; a troca de caracteres vista antes era só do console).
  Script: `scripts/blockchain/update_schema.py`. A ancoragem de lote, laudo e certificação
  continuou funcionando (`test_ancoragem_lote/laudo/certificacao.py` e a suíte completa).
  **Correção (2026-10-18):** a atribuição ao `f905fcd` acima está imprecisa quanto à `CertificateShape`.
  `git log -S "CertificateShape" -- ontology/validation-shapes.ttl` só retorna `f905fcd`, e o diff
  mostra que ele **adicionou** a `CertificateShape` (não existia antes); só o `sh:Info` de
  `HoneyNativeMoistureShape` foi **corrigido** nesse commit (`git log -S "dentro do limite para
  aproveitamento condicional"` retorna `f905fcd` e o commit inicial `16acdcc`, de onde vinha o texto).
  O restante vale: o arquivo anterior ao `f905fcd`, em CRLF, tem keccak256
  `0xb4385c37d4db5a604b36ffcdc0663a490fae7617b7bb0b6428ab7fc94d21c041`, igual ao `dataHash` da v1
  (conferido em 2026-10-18; em LF o hash daquele arquivo seria `0xf0692eaa…ae46`). Texto original mantido.
- **`.gitattributes` `*.ttl text eol=lf`.** O índice já guardava LF; o arquivo e o hash deixam
  de depender da conversão de fim de linha de cada máquina (`git add --renormalize .` não
  mudou nenhum arquivo).
- **Teste de sincronização schema ↔ arquivo** (`tests/features/schema_sincronizado.feature`):
  o `dataHash` do schema ativo on-chain tem de ser igual ao hash canônico do `.ttl`. Se o
  arquivo mudar sem nova versão, falha com "Schema on-chain desatualizado: rode
  scripts/blockchain/update_schema.py", versão e os dois hashes (mensagem exercitada por
  monkeypatch do hash, sem editar o `.ttl`).
- **Proteção da limpeza do `tests/conftest.py`** (`tests/features/conftest_allowlist.feature`,
  coop-a e coop-b): planta no Fuseki uma tarefa órfã de batchNumber fora da allowlist
  (`LOTE-REAL-PROTEGIDO-<uuid>`), uma órfã da allowlist e uma da allowlist cujo lote AINDA
  existe; a limpeza só pode apagar a órfã da allowlist. Passou de primeira (comportamento já
  correto: lacuna de cobertura fechada, `conftest.py` intocado). **Mutação verificada** com
  plugin fora do repositório: filtro de regex afrouxado para `.*` → `2 failed` ("A limpeza
  APAGOU a tarefa de batchNumber fora da allowlist"); `FILTER NOT EXISTS` removido →
  `2 failed` ("A limpeza APAGOU a tarefa de um lote que AINDA existe"); sem resíduo no Fuseki.

Suíte: **44 passed, 3 deselected** (os 41 anteriores + 1 de schema sincronizado + 2 da proteção
da limpeza, que roda uma vez por nó). Mesma ressalva de ambiente: depende de máquina acordada e
túnel do Besu ativo, que também é pré-condição do teste de schema (lê o RPC).

## Config: default de BESU_RPC_URL em 127.0.0.1 (2026-10-12) — ✅ testado e confirmado

**Confirmado por Juan em 2026-10-12** (leu `44 passed, 3 deselected`, uma rodada).

Os 5 `config.py` (coop-a, coop-b, lab, certifier e verifier-demo) passam a usar
`http://127.0.0.1:8545` como default de `BESU_RPC_URL` (antes `http://localhost:8545`); a
variável de ambiente continua sobrescrevendo. Motivo: atraso de resolução de `localhost` no
Windows (tenta `::1` antes do IPv4), medido na entrada "Suíte depende da máquina acordada e do
túnel SSH do Besu ativo" de `docs/known-issues.md`: com a porta fechada, 4,1 s com `localhost`
vs 2,05 s com `127.0.0.1`; com o túnel de pé não há diferença (~0,15 s). Ou seja, o ganho
aparece quando o RPC está fora do ar. Antes da troca, grep confirmou que o RPC só estava hardcoded nesses
5 arquivos, fora de `scripts/` e `tests/`. Os serviços foram reiniciados para a mudança valer
(o `config.py` é lido na importação). Os testes (`RPC_URL` em `test_ancoragem_outbox*.py`) e
`scripts/` continuam com `localhost`, de propósito.

**Atualização (2026-10-18):** isso deixou de valer. Os testes, o `CERTIFIER_URL` do coop-a, os scripts de
`scripts/` e os `.env.example` passaram a usar `127.0.0.1` nas URLs de conexão (commits `0d1d4a7` e
`5c194a6`); ver a entrada "Venda de dados: estado em 2026-10-18 (executada pelo agente)", item 6.
Texto original mantido.

Suíte: **44 passed, 3 deselected** — uma rodada. Mesma ressalva de ambiente (máquina acordada
e túnel do Besu ativo).

## M8 fechado: escopo (2026-10-12) — executada pelo agente

**O que o M8 cobre:**
- Ancoragem dos três eventos (lote em coop-a/coop-b, laudo no lab, certificação no certifier)
  via `TransactionOrchestrator.submitTransaction`, cada um com outbox e `POST /anchoring/retry`
  (`pending`/`anchored`/`mismatch`, `in_flight`, `waiting_parent`; hash guardado na emissão no
  certifier, com 409 na reemissão).
- Verificação on-chain de lote e laudo (`onChainIntegrity` em `GET /verify`, via
  `AssetRegistry.getAsset()`), e da certificação pelo M7 (OID4VP).
- Schema `honey-validation-shapes` na versão 2 on-chain, com hash canônico em LF, sincronizado
  com o `ontology/validation-shapes.ttl` e coberto por teste (`schema_sincronizado.feature`).
- Suíte de 44 testes (`44 passed, 3 deselected`).

**Fora do escopo, registrado:**
- Custódia e transferência de posse: ficam para a venda (DvP/2PC); ver o item de roadmap no
  `CLAUDE.md`.
- Consulta do schema on-chain pelos nós em tempo de validação (hoje a validação SHACL usa o
  arquivo local; o schema on-chain só é conferido pelo teste de sincronização).
- Divulgação seletiva real no M7 (o hash ancorado é o do SD-JWT completo; ver
  `docs/known-issues.md`).
- Autenticação entre nós.
- Agendador de retry (o `POST /anchoring/retry` é manual).

Rótulo "executada pelo agente": sem confirmação do Juan para o fechamento do escopo.

## Venda de dados: estado em 2026-10-18 (executada pelo agente)

Sem confirmação do Juan: nada abaixo está marcado como "testado e confirmado". Os resultados são das
rodadas do agente; o detalhe por tarefa está em `docs/SYNC.md`. O fechamento de escopo do M8 (entrada
acima) não foi alterado e segue sem confirmação.

1. **Quantidade do lote em mL (coop-a).** `POST /lote` aceita `quantityLiters` opcional (até 3 casas) e
   guarda `:quantityMilliliters` (inteiro, mL) no recurso do lote; `HoneyBatchQuantityShape` valida. O
   `amount` do `createAsset` e do retry de ancoragem passa a ser a quantidade real (antes, 1 fixo). A
   quantidade não entra no hash ancorado. O coop-b não tem quantidade.
2. **`VendaDados.sol`** (`contracts/venda-dados/`, Hardhat 3, OpenZeppelin 5, solc 0.8.28). ERC20 de cotas
   (1 cota = 1 mL entregue, `decimals` 0, não transferível); `registrarEntrega` (só a cooperativa;
   repetir com os mesmos dados é no-op, com dados diferentes reverte); pagamento retido e liberado:
   `depositar`, `liberar`, `reembolsar`, `recuperarReembolso` (comprador, após o prazo); repartição O(1)
   por acumulador (`acumuladoPorCota`), `sacar` e `recuperarCarteira`. `MockUSDT` só para teste.
   45 testes no Hardhat (35 do contrato + 10 do getter `depositos(ref)` e da ref no formato do coop-a),
   com mutações verificadas em cópia. Deploy e smoke em node local (chainId 31337, porta 8546);
   `scripts/deploy-local.js` escreve `deployments/local.json`. Não implantado em rede real, sem revisão
   de segurança externa, poeira de divisão sem `sweep`.
3. **Entrega no VendaDados (coop-a).** `coop-a/delivery.py`: quando o lote tem quantidade, `POST /lote`
   registra a entrega (`registrarEntrega` para a carteira custodial do produtor, `batchRef` =
   keccak256(`batchNumber`)) e a resposta traz `deliveryStatus` (`registered`, `skipped`, `pending` ou
   `mismatch`). A `DeliveryTask` fica em recurso separado no Fuseki (não altera o hash) e
   `POST /delivery/retry` consulta `entregas(batchRef)` antes de enviar (já registrado só marca
   `registered`; dados diferentes = `mismatch`). Carteira custodial por `producerDID` em
   `coop-a/keys/producers/` (gitignored; só rede de teste). Erros HTTP: mensagem fixa e detalhe só no log;
   exceção: o `mismatch` devolve o detalhe on-chain (endereços e mL), que não é segredo. Configuração: `VENDA_DADOS_RPC_URL`, `VENDA_DADOS_ADDRESS`, `VENDA_DADOS_CHAIN_ID`
   (`venda_dados_config()`).
4. **Contrato de Dados (coop-a).** `POST /data-contract` com consumidor, `purpose`, `priceUnits` (inteiro,
   menor unidade do USDT mock, 6 casas), `buyerWallet` (EIP-55) e `validFrom`/`validUntil` (ISO 8601 com
   hora e fuso). `contractRef` = keccak256 do JSON canônico dos termos, como implementado em
   `coop-a/data_contract_endpoint.py`: `terms = {k: body[k] for k in _TERM_KEYS if k in body}` (só as 6
   chaves `consumer`, `purpose`, `priceUnits`, `buyerWallet`, `validFrom`, `validUntil`, e só as que
   vieram; chaves extras são ignoradas), serializado por
   `json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` e hasheado com
   `Web3.keccak(text=canonical)` (bytes UTF-8). Ou seja: chaves em ordem alfabética, sem espaços;
   **caracteres não-ASCII NÃO são escapados** (entram como UTF-8); aspas, barra invertida, quebra de
   linha e demais controles abaixo de U+0020 **continuam escapados como em JSON** (`\"`, `\\`, `\n`,
   `\u00XX`); valores e datas exatamente como recebidos (`priceUnits` inteiro, datas em texto sem
   normalização). O mesmo bytes32 serve de `ref` no VendaDados. Respostas: 201; 409
   `already_exists` com o `contractRef`; 400 `shacl_violation`; 400 `invalid_request` (forma, inclusive
   data sem hora ou sem fuso). Regras de negócio no SHACL (`DataContractShape`: `priceUnits` >= 1,
   `validFrom` `sh:lessThan` `validUntil`). Propriedades novas `:priceUnits`, `:contractRef` e
   `:buyerWallet` em `bee-extension.ttl` (o `.owl` não foi regenerado). A ref é reproduzida byte a byte
   por um helper Node/ethers nos testes e aceita em `depositar`.
5. **Leitura do pagamento.** `GET /data-contract/{contractRef}/payment` lê `depositos(ref)` por `eth_call`
   (sem chave) e os termos do Fuseki. `status`: `not_paid`, `paid`, `mismatch`, `outside_window`,
   `released` ou `refunded`. Precedência: estado 0 = `not_paid`; comprador ou valor diferentes de
   `buyerWallet`/`priceUnits` = `mismatch`; estado 2 = `released`; estado 3 = `refunded`; estado 1 fora
   da janela = `outside_window`; senão `paid`. Janela inclusiva no código, sem teste de borda
   (`_to_utc(validFrom) <= deposited <= _to_utc(validUntil)` em `payment_status`; os testes usam janelas
   de horas ou dias, nunca exatamente o limite): o timestamp do bloco do depósito é comparado com
   `validFrom`/`validUntil` convertidos para UTC. 404 `not_found` (contrato inexistente ou
   ref mal formada); 503 `service_unavailable` com mensagem fixa (node ou Fuseki indisponível).
6. **`localhost` por `127.0.0.1` nas URLs de conexão.** `localhost` resolve `::1` primeiro e o uvicorn só
   escuta em IPv4, o que custa ~2,05 s por requisição no Windows (medido; em `test_apresentacao_vp` são
   10 saltos, ~20,5 s de ~27 s). Trocado em `coop-a/config.py` (`CERTIFIER_URL`), nas constantes dos
   testes, em `scripts/blockchain/*.py`, `scripts/test_credential_flow.py` e nos `.env.example`
   (commits `0d1d4a7` e `5c194a6`). Não mudaram: `did:web`, `BASE_URL`, `credential_issuer` nem o `aud`
   do KB-JWT (nos testes de VP, `VERIFIER_AUDIENCE` segue `http://localhost:8004`). Efeito medido:
   `test_apresentacao_vp` de 27–40 s para ~7 s; suíte completa de 614–678 s (~10–11 min) para 210 s
   (~3,5 min).

**Referências (informadas pelo Juan; não verificadas pelo agente):**
(a) o `VendaDados` é adaptado do contrato ISD do artigo (Economies, DOI 10.3390/economies14060220);
(b) Ivan, em Q2/Q3: `VerifierResponseShape` aceita; `CertificateShape` com `rdfs:comment` obrigatório
(número ou hash do certificado).

**Suíte (rodada mais recente do agente, 2026-10-18):** `1 failed, 77 passed, 5 deselected in 210.41s
(0:03:30)`. A única falha é `test_schema_sincronizado`, vermelha de propósito: o `update_schema.py` não foi
rodado desde as mudanças de shape (quantidade, descrição do certificado e contrato de dados); o schema
on-chain segue na v2 (`0x01ca…`) e o hash do arquivo é outro. Cenários `@node_parado` ficam fora da suíte
padrão (`-m node_parado`, exigem o node 8546 parado).

**Limites e pendências:** `docs/known-issues.md` (carteiras custodiais, corrida em `POST /data-contract`,
datas legadas sem fuso, `.owl` defasado) e `docs/SYNC.md`.
