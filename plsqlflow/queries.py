from __future__ import annotations

from pathlib import Path
from typing import Dict

QUERY_DIR = Path(__file__).resolve().parent.parent / "sql" / "flow"

QUERY_NAMES = (
    "resolve_target.sql",
    "plscope_check.sql",
    "plscope_calls.sql",
    "plscope_statements.sql",
    "fetch_source.sql",
    "deps_direct.sql",
    "triggers_for_tables.sql",
    "triggers_any_status.sql",
    "fk_cascade.sql",
    "type_hierarchy.sql",
    "resolve_synonym.sql",
    "object_catalog.sql",
    "tab_columns.sql",
)


def load_query(name: str) -> str:
    if name not in QUERY_NAMES:
        raise ValueError(
            "query desconhecida: {!r}. Nomes validos: {}".format(name, ", ".join(QUERY_NAMES))
        )
    path = QUERY_DIR / name
    if not path.exists():
        raise FileNotFoundError("arquivo de query ausente: {}".format(path))
    return path.read_text(encoding="utf-8")


QUERY_TEXT: Dict[str, str] = {name: load_query(name) for name in QUERY_NAMES}
