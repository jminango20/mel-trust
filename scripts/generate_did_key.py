"""
Gera um par de chaves Ed25519 REAL para um produtor (apicultor/meliponicultor)
e deriva o `did:key` correspondente — M5, Tarefa 2.

Escopo: só GERAÇÃO de chave + derivação algorítmica do identificador
did:key. Não há endpoint HTTP nem registro envolvido — ao contrário de
did:web (ver <node>/main.py, GET /.well-known/did.json), um did:key é
"auto-resolúvel": qualquer verificador decodifica o próprio identificador
(multibase -> multicodec -> bytes da chave pública) sem precisar consultar
nada externo. Por isso este script não precisa expor nada em nenhum nó —
é standalone, roda uma vez por produtor e é descartado.

Uso:
    python scripts/generate_did_key.py <nome-saida-sem-extensao>

Exemplo:
    python scripts/generate_did_key.py producer-coop-a
    -> gera fixtures/producer-keys/producer-coop-a.pem (chave privada,
       PKCS8, sem senha — NUNCA commitada, ver .gitignore) e imprime o
       did:key derivado da chave pública correspondente.

Como o did:key é construído (draft did:key method / multiformats):
1. Gera o par Ed25519 via jwcrypto (mesma lib já usada no projeto para as
   chaves de issuer em <node>/keys.py — consistência, não precisa de uma
   segunda lib de curva elíptica).
2. Extrai os 32 bytes RAW da chave pública a partir do JWK exportado
   (`export_public(as_dict=True)["x"]`, base64url — mesmo campo que
   <node>/main.py usa para publicar `publicKeyJwk` no DID Document do
   issuer).
3. Prefixa esses 32 bytes com o código multicodec de "ed25519-pub":
   0xed 0x01 (varint unsigned de 0xed — cabe em 2 bytes porque 0xed > 0x7f,
   valor fixo da multicodec table, não gerado/calculado aqui).
4. Codifica `<multicodec-prefix><chave-pública-raw>` em base58btc e
   prefixa com "z" (marcador multibase de base58btc) -> "did:key:z...".

Isso é EXATAMENTE o que faz o identificador ser auto-verificável: para
validar uma assinatura feita com a chave privada correspondente, qualquer
parte decodifica o did:key (base58 -> remove prefixo multicodec -> chave
pública raw) sem consultar nenhum servidor. Não há "chave de issuer" nem
metadata OID4VCI envolvidos aqui — isso é para os PRODUTORES individuais
(claim `hasDID` do :DataProducer nos fixtures .ttl), não para os nós/issuers
(coop-a, coop-b, certifier), que usam did:web (Tarefa 1).
"""
import os
import sys

import base58
from jwcrypto.jwk import JWK
from jwcrypto.common import base64url_decode

ED25519_PUB_MULTICODEC_PREFIX = bytes([0xED, 0x01])


def generate_producer_did_key(output_path: str) -> str:
    """
    Gera um par Ed25519, persiste a chave privada (PEM, PKCS8, sem senha)
    em `output_path`, e retorna o did:key derivado da chave pública.
    """
    key = JWK.generate(kty="OKP", crv="Ed25519")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    pem = key.export_to_pem(private_key=True, password=None)
    with open(output_path, "wb") as f:
        f.write(pem)

    public_jwk = key.export_public(as_dict=True)
    raw_public_key = base64url_decode(public_jwk["x"])

    multicodec_bytes = ED25519_PUB_MULTICODEC_PREFIX + raw_public_key
    did_key = "did:key:z" + base58.b58encode(multicodec_bytes).decode("ascii")
    return did_key


def main() -> None:
    if len(sys.argv) != 2:
        print("Uso: python scripts/generate_did_key.py <nome-saida-sem-extensao>")
        sys.exit(1)

    name = sys.argv[1]
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(_this_dir)
    output_path = os.path.join(repo_root, "fixtures", "producer-keys", f"{name}.pem")

    did_key = generate_producer_did_key(output_path)
    print(f"Chave privada salva em: {output_path}")
    print(f"did:key gerado: {did_key}")


if __name__ == "__main__":
    main()
