# language: pt
# O schema SHACL registrado on-chain tem de corresponder ao ontology/validation-shapes.ttl do repo.
# Hash canônico = keccak256 dos bytes do arquivo com CRLF trocado por LF (canonical_schema_hash,
# scripts/blockchain/create_schema.py). Só leitura on-chain.
Funcionalidade: Schema SHACL on-chain sincronizado com o arquivo do repositório

  Cenário: O dataHash do schema ativo on-chain é o hash canônico do validation-shapes.ttl
    Dado o schema honey-validation-shapes ativo on-chain
    Quando calculo o hash canônico (LF) de ontology/validation-shapes.ttl
    Então o dataHash on-chain é igual a esse hash
