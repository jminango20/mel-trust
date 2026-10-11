"""
Step defs (pytest-bdd) para tests/features/schema_sincronizado.feature.

Só leitura: chama SchemaRegistry.getActiveSchema("mel-trust", "honey-validation-shapes")
e compara o dataHash com canonical_schema_hash(ontology/validation-shapes.ttl)
(scripts/blockchain/create_schema.py). Se o .ttl mudar sem nova versão do schema, falha
com a instrução de rodar scripts/blockchain/update_schema.py.
"""
import sys
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "blockchain"))
import create_schema as cs  # noqa: E402

scenarios("../features/schema_sincronizado.feature")

RPC_URL = "http://127.0.0.1:8545"  # 127.0.0.1: "localhost" custa ~2 s por tentativa no Windows


@pytest.fixture
def ctx():
    return {}


@given("o schema honey-validation-shapes ativo on-chain")
def schema_ativo(ctx):
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 15}))
    registry = w3.eth.contract(
        address=Web3.to_checksum_address(cs.SCHEMA_REGISTRY_ADDRESS), abi=cs.GET_ACTIVE_SCHEMA_ABI
    )
    schema, exists = registry.functions.getActiveSchema(cs.CHANNEL_NAME, cs.SCHEMA_ID).call()
    assert exists, f"Schema '{cs.SCHEMA_ID}' não existe no canal '{cs.CHANNEL_NAME}' (rode scripts/blockchain/create_schema.py)"
    ctx["onchain_hash"] = bytes(schema[3])
    ctx["onchain_version"] = schema[2]


@when("calculo o hash canônico (LF) de ontology/validation-shapes.ttl")
def hash_canonico(ctx):
    ctx["local_hash"] = bytes(cs.canonical_schema_hash(cs.VALIDATION_SHAPES_PATH))


@then("o dataHash on-chain é igual a esse hash")
def hashes_iguais(ctx):
    assert ctx["onchain_hash"] == ctx["local_hash"], (
        "Schema on-chain desatualizado: rode scripts/blockchain/update_schema.py "
        f"(versão on-chain {ctx['onchain_version']}: 0x{ctx['onchain_hash'].hex()}; "
        f"hash canônico do arquivo: 0x{ctx['local_hash'].hex()})"
    )
