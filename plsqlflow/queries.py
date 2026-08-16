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
    # Irmas em lote das queries por-objeto acima (contrato
    # oracle-depgraph-scale): mesma projecao mais a coluna que identifica o
    # objeto de origem, restringidas por :object_list. Existem para a BFS por
    # nivel -- sem elas, um grafo de 20 mil objetos faz 20 mil idas ao banco.
    # As versoes por-objeto continuam registradas e em uso: sao o caminho de
    # fallback quando o extractor nao implementa os metodos batch.
    "deps_direct_batch.sql",
    "plscope_statements_batch.sql",
    "fetch_source_batch.sql",
    # Arvore crua de identificadores em lote (contrato depgraph-granular,
    # T-02): usada pela atribuicao por subprograma (attribute.py, T-01) e
    # pela BFS de subprogramas (procgraph.py, T-03).
    "plscope_tree_batch.sql",
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
