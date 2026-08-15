from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Credencial de dev para os testes live (gated). NUNCA em linha de comando nem
# versionada: o arquivo vive em .harness/scratch/ (gitignored, varrido por
# `harness finish`). Ausente = os testes live simplesmente pulam.
_CREDS = REPO_ROOT / ".harness" / "scratch" / "dev_creds.json"
if _CREDS.exists():
    import json

    try:
        _c = json.loads(_CREDS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _c = {}
    if _c.get("user") and _c.get("pwd") and _c.get("dsn"):
        os.environ.setdefault("PLSQLFLOW_USER", _c["user"])
        os.environ.setdefault("PLSQLFLOW_PWD", _c["pwd"])
        os.environ.setdefault("PLSQLFLOW_DSN", _c["dsn"])

# .env da raiz do repo (mesma regra de precedencia do resto do contrato
# dotenv-conn: variavel ja definida no ambiente real sempre ganha do
# arquivo). Aplicado DEPOIS do bloco de dev_creds.json acima -- os dois usam
# setdefault, entao dev_creds.json (mecanismo explicito de teste) continua
# tendo precedencia sobre o .env quando os dois definem a mesma chave.
# Ausente = os testes live simplesmente pulam, como antes.
_ENV_FILE = REPO_ROOT / ".env"
if _ENV_FILE.exists():
    from dotenv import dotenv_values

    # So chaves PLSQLFLOW_*: o .env de quem usa o repo e um arquivo do
    # PROJETO dele e costuma ter credencial de outras coisas (o desta
    # maquina tem UserId/Password/DataSource de um app .NET). Injetar tudo
    # em os.environ poria nomes genericos como `Password` no ambiente do
    # processo de teste, onde qualquer biblioteca pode le-los.
    for _key, _value in dotenv_values(_ENV_FILE).items():
        if _value is not None and _key.startswith("PLSQLFLOW_"):
            os.environ.setdefault(_key, _value)
