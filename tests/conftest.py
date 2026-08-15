from __future__ import annotations

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
    import os

    try:
        _c = json.loads(_CREDS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _c = {}
    if _c.get("user") and _c.get("pwd") and _c.get("dsn"):
        os.environ.setdefault("PLSQLFLOW_USER", _c["user"])
        os.environ.setdefault("PLSQLFLOW_PWD", _c["pwd"])
        os.environ.setdefault("PLSQLFLOW_DSN", _c["dsn"])
