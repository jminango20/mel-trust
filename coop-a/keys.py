"""
Gestão da chave de assinatura do issuer coop-a — M3.

Contexto/decisão (não veio de nenhum M0 pré-existente no repo — verifiquei
com grep amplo por `sd_jwt`, `.pem`, `Ed25519`, `private_key` em todo o
projeto antes de escrever este arquivo e não encontrei nada: nem a
dependência `sd-jwt-python`/`sd_jwt` em nenhum requirements.txt, nem
material de chave em nenhum nó. Este módulo é infraestrutura NOVA gerada
para o M3, não reaproveitamento de algo já validado):

- Algoritmo: Ed25519 (JWA `EdDSA`), não ES256 (default da lib `sd-jwt`).
  Motivo: o resto do projeto já usa Ed25519 em todo lugar que aparece chave
  assimétrica — `did:key:z6Mk...` em ontology/instances.ttl e
  coop-a/data/sample-instance.ttl usa multibase `z6Mk`, prefixo característico
  de chave pública Ed25519; Ed25519Signature2020 é citado em
  ontology/instances.ttl como o suite de assinatura da versão anterior do
  projeto. Manter Ed25519 aqui evita introduzir um segundo tipo de curva
  (P-256/ES256) sem necessidade, e alinha com o que M5 (did:web/did:key
  reais) provavelmente vai assumir.
- Formato de armazenamento: um único arquivo PEM (PKCS8, sem senha),
  `coop-a/keys/coop-a.pem`, path configurável via env var
  `ISSUER_KEY_PATH` (config.py) — nunca hardcoded. PEM foi escolhido em vez
  de, por ex., salvar o JWK como JSON solto, porque é o formato padrão de
  interoperabilidade para chaves privadas (qualquer lib consegue ler/gerar)
  e é o que a lib `jwcrypto` (dependência de `sd-jwt`) sabe importar/exportar
  nativamente via `JWK.from_pem`/`export_to_pem`, sem precisar de mais
  nenhuma dependência (não precisamos adicionar `cryptography` directly ao
  requirements.txt — `jwcrypto` já a traz transitivamente).
- Geração: se o arquivo não existir, um novo par Ed25519 é gerado e
  persistido nesse path na primeira chamada (lazy, tipicamente no startup
  do FastAPI). Isso é uma simplificação deliberada de MVP: em produção, a
  chave do issuer teria uma cerimônia de geração própria (HSM, rotação,
  publicação da chave pública associada ao documento did:web em M5) — aqui
  só precisamos de ALGO estável entre reinícios do processo para assinar
  SD-JWTs de forma consistente durante testes manuais/E2E.
- Escopo do que este módulo NÃO faz: não publica a chave pública em
  `/.well-known/did.json` (isso é M5, resolução real de did:web) — por
  enquanto a chave pública correspondente só existe implicitamente (quem
  verificar a assinatura do SD-JWT em M3 teria que confiar "fora de banda").
"""
import os

from jwcrypto.jwk import JWK

from config import ISSUER_KEY_PATH

_cached_key: JWK | None = None


def _generate_and_persist(path: str) -> JWK:
    key = JWK.generate(kty="OKP", crv="Ed25519")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pem = key.export_to_pem(private_key=True, password=None)
    with open(path, "wb") as f:
        f.write(pem)
    return key


def get_issuer_key() -> JWK:
    """
    Retorna a chave Ed25519 do issuer coop-a como um objeto `jwcrypto.jwk.JWK`
    (formato que a lib `sd_jwt.issuer.SDJWTIssuer` espera para o parâmetro
    `issuer_key`), carregando de `ISSUER_KEY_PATH` ou gerando+persistindo se
    o arquivo ainda não existir. Cacheada em processo após a primeira
    chamada — não relê o arquivo a cada requisição.
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    path = ISSUER_KEY_PATH
    if os.path.isfile(path):
        with open(path, "rb") as f:
            _cached_key = JWK.from_pem(f.read())
    else:
        _cached_key = _generate_and_persist(path)

    return _cached_key
