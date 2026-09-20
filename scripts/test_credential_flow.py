"""
Executa o fluxo OID4VCI completo contra um nó (coop-a, coop-b ou certifier) e
imprime o SD-JWT VC recebido já decodificado — para inspeção visual imediata.

Fluxo (mesma ordem dos milestones M2 -> M2.5 -> M3):
  1. GET  /.well-known/openid-credential-issuer  -> descobre credential_configuration_id
  2. POST /token   (grant pre-authorized_code)   -> access_token
  3. POST /nonce                                 -> c_nonce
  4. Monta proof JWT (header: alg=EdDSA, typ=openid4vci-proof+jwt, jwk=pública
     do produtor; payload: iss/aud/iat/nonce) ASSINADO com a chave privada do
     produtor (fixtures/producer-keys/*.pem). Isso é o que não dá para fazer
     via curl: a assinatura Ed25519 é real, não string estática.
  5. POST /credential (Bearer access_token + proof) -> SD-JWT VC
  6. Decodifica: header + payload do JWT do issuer, e cada Disclosure
     ([salt, claim_name, value]) separada por `~`.

Uso (rodar com o venv de qualquer nó; precisa de jwcrypto e requests):
    python scripts/test_credential_flow.py --port 8001 \\
        --pre-authorized-code coop-a-demo-pre-auth-code \\
        --key fixtures/producer-keys/producer-coop-a.pem

Opcionais: --host (default localhost), --batch-number, --config-id
(default: primeiro de credential_configurations_supported).
"""
import argparse
import base64
import json
import sys
import time

import requests
from jwcrypto.jwk import JWK
from jwcrypto.jws import JWS

PROOF_JWT_TYP = "openid4vci-proof+jwt"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:pre-authorized_code"


def b64url_json(segment: str) -> object:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def show(title: str, obj: object) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def step_fail(step: str, resp: requests.Response) -> None:
    print(f"\n[ERRO] {step}: HTTP {resp.status_code}\n{resp.text}", file=sys.stderr)
    sys.exit(1)


def build_proof_jwt(key: JWK, audience: str, c_nonce: str) -> str:
    public_jwk = json.loads(key.export_public())
    header = {"alg": "EdDSA", "typ": PROOF_JWT_TYP, "jwk": public_jwk}
    payload = {"aud": audience, "iat": int(time.time()), "nonce": c_nonce}
    jws = JWS(json.dumps(payload).encode())
    jws.add_signature(key, alg="EdDSA", protected=json.dumps(header))
    return jws.serialize(compact=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--pre-authorized-code", required=True)
    ap.add_argument("--key", required=True, help="PEM PKCS8 Ed25519 do produtor")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--batch-number", default=None)
    ap.add_argument("--config-id", default=None)
    args = ap.parse_args()

    base = f"http://{args.host}:{args.port}"

    with open(args.key, "rb") as f:
        key = JWK.from_pem(f.read())

    # 1. Metadata
    r = requests.get(f"{base}/.well-known/openid-credential-issuer", timeout=10)
    if not r.ok:
        step_fail("metadata", r)
    meta = r.json()
    config_id = args.config_id or next(iter(meta["credential_configurations_supported"]))
    print(f"issuer: {meta['credential_issuer']}\ncredential_configuration_id: {config_id}")

    # 2. Token
    r = requests.post(
        f"{base}/token",
        data={"grant_type": GRANT_TYPE, "pre-authorized_code": args.pre_authorized_code},
        timeout=10,
    )
    if not r.ok:
        step_fail("token", r)
    access_token = r.json()["access_token"]
    print(f"access_token: {access_token}")

    # 3. Nonce
    r = requests.post(f"{base}/nonce", timeout=10)
    if not r.ok:
        step_fail("nonce", r)
    c_nonce = r.json()["c_nonce"]
    print(f"c_nonce: {c_nonce}")

    # 4. Proof JWT
    proof_jwt = build_proof_jwt(key, meta["credential_issuer"], c_nonce)

    # 5. Credential
    body = {"credential_configuration_id": config_id, "proof": {"proof_type": "jwt", "jwt": proof_jwt}}
    if args.batch_number:
        body["batch_number"] = args.batch_number
    r = requests.post(
        f"{base}/credential",
        json=body,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not r.ok:
        step_fail("credential", r)
    sdjwt = r.json()["credential"]

    # 6. Decodifica
    parts = sdjwt.split("~")
    issuer_jwt, disclosures = parts[0], [p for p in parts[1:] if p]
    h, p, _sig = issuer_jwt.split(".")
    show("SD-JWT header", b64url_json(h))
    show("SD-JWT payload (claims em claro + _sd digests)", b64url_json(p))
    print(f"\n=== Disclosures ({len(disclosures)}) — [salt, claim, valor] ===")
    for d in disclosures:
        print(json.dumps(b64url_json(d), ensure_ascii=False))
    print(f"\n=== SD-JWT bruto ===\n{sdjwt}")


if __name__ == "__main__":
    main()
