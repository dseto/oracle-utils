"""Testes offline (T-01, contrato oracle-depgraph-scale) para as tres
queries batch de sql/flow/ (deps_direct_batch.sql, plscope_statements_batch.sql,
fetch_source_batch.sql) e para os fetchers/dataclasses correspondentes em
plsqlflow/extract.py.

Nao abre conexao real e nao depende de rede/banco -- mesmo padrao de cursor
mockado de tests/test_plsqlflow_unit.py e tests/test_depgraph_unit.py
(FakeConnection / FakeCursor em memoria, sem fixture em disco).

As tres queries sao carregadas pelo registro central
(queries.QUERY_NAMES / QUERY_TEXT), como todas as outras -- nao ha loader
paralelo em extract.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from plsqlflow import db, extract, queries

REPO = Path(__file__).resolve().parent.parent
SQL_FLOW_DIR = REPO / "sql" / "flow"

BATCH_SQL_FILES = (
    "deps_direct_batch.sql",
    "plscope_statements_batch.sql",
    "fetch_source_batch.sql",
)


# --------------------------------------------------------------- fakes


class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.description = None
        self.executed = None
        self.closed = False

    def execute(self, sql, binds=None):
        self.executed = (sql, binds)
        columns = list(self._rows[0].keys()) if self._rows else []
        self.description = [(col.upper(),) for col in columns]
        self._tuples = [tuple(row[col] for col in columns) for row in self._rows]

    def fetchall(self):
        return self._tuples

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = FakeCursor(self._rows)
        return self.last_cursor


# --------------------------------------------------------------- fixtures

DEPS_DIRECT_BATCH_ROWS = [
    {
        "NAME": "PKG_A",
        "REFERENCED_OWNER": "GESTAO",
        "REFERENCED_NAME": "PKG_B",
        "REFERENCED_TYPE": "PACKAGE",
        "DEPENDENCY_TYPE": "HARD",
    },
    {
        "NAME": "PKG_B",
        "REFERENCED_OWNER": "GESTAO",
        "REFERENCED_NAME": "FLOW_DEMO_LOG",
        "REFERENCED_TYPE": "TABLE",
        "DEPENDENCY_TYPE": "HARD",
    },
]

PLSCOPE_STATEMENTS_BATCH_ROWS = [
    {
        "OBJECT_NAME": "PKG_A",
        "LINE": 6,
        "STMT_TYPE": "INSERT",
        "SQL_ID": "5rhw9u2hz7b12",
        "HAS_INTO_RECORD": "NO",
        "TEXT": "INSERT INTO flow_demo_log (msg) VALUES ('x')",
    },
    {
        "OBJECT_NAME": "PKG_B",
        "LINE": 28,
        "STMT_TYPE": "EXECUTE IMMEDIATE",
        "SQL_ID": None,
        "HAS_INTO_RECORD": "NO",
        "TEXT": None,
    },
]

FETCH_SOURCE_BATCH_ROWS = [
    {"NAME": "PKG_A", "TYPE": "PACKAGE BODY", "LINE": 1, "TEXT": "PACKAGE BODY pkg_a IS"},
    {"NAME": "PKG_B", "TYPE": "PACKAGE BODY", "LINE": 1, "TEXT": "PACKAGE BODY pkg_b IS"},
]


# --------------------------------------------------------- (a) queries no disco


def test_batch_sql_files_exist_on_disk():
    for name in BATCH_SQL_FILES:
        assert (SQL_FLOW_DIR / name).exists(), name


def test_batch_sql_files_load_non_empty():
    for name in BATCH_SQL_FILES:
        text = (SQL_FLOW_DIR / name).read_text(encoding="utf-8")
        assert text.strip(), "{} carregou vazio".format(name)


def test_batch_sql_files_document_object_list_bind():
    for name in BATCH_SQL_FILES:
        text = (SQL_FLOW_DIR / name).read_text(encoding="utf-8")
        assert ":object_list" in text, name
        assert ":owner" in text, name


def test_deps_direct_batch_references_all_dependencies_and_name_column():
    text = (SQL_FLOW_DIR / "deps_direct_batch.sql").read_text(encoding="utf-8").lower()
    assert "all_dependencies" in text
    assert "d.name" in text


def test_plscope_statements_batch_references_all_statements_and_object_name():
    text = (SQL_FLOW_DIR / "plscope_statements_batch.sql").read_text(encoding="utf-8").lower()
    assert "all_statements" in text
    assert "s.object_name" in text


def test_fetch_source_batch_references_all_source_object_type_and_name():
    text = (SQL_FLOW_DIR / "fetch_source_batch.sql").read_text(encoding="utf-8").lower()
    assert "all_source" in text
    assert "s.name" in text
    assert ":object_type" in text


def test_batch_sql_registered_in_central_registry():
    # As batch passam pelo MESMO registro das outras 13 queries: nome errado
    # falha na importacao do modulo, nao so quando o fetcher e chamado.
    for name in BATCH_SQL_FILES:
        assert name in queries.QUERY_NAMES
        on_disk = (SQL_FLOW_DIR / name).read_text(encoding="utf-8")
        assert queries.QUERY_TEXT[name] == on_disk


# --------------------------------------------------------- (b) fetchers


def test_fetch_deps_direct_batch_binds_and_maps_dataclass():
    conn = FakeConnection(DEPS_DIRECT_BATCH_ROWS)
    rows = extract.fetch_deps_direct_batch(conn, "gestao", "PKG_A,PKG_B")

    assert len(rows) == 2
    assert all(isinstance(r, extract.DepsDirectBatchRow) for r in rows)
    assert rows[0].name == "PKG_A"
    assert rows[0].referenced_name == "PKG_B"
    assert rows[1].name == "PKG_B"
    assert rows[1].referenced_name == "FLOW_DEMO_LOG"

    sql, binds = conn.last_cursor.executed
    assert binds == {"owner": "gestao", "object_list": "PKG_A,PKG_B"}
    # db.run_query tira o ";" final (convencao SQLcl) antes de executar.
    assert sql == queries.QUERY_TEXT["deps_direct_batch.sql"].rstrip().rstrip(";")


def test_fetch_deps_direct_batch_object_list_optional():
    conn = FakeConnection([])
    extract.fetch_deps_direct_batch(conn, "GESTAO", None)
    _, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "object_list": None}


def test_fetch_plscope_statements_batch_binds_and_maps_dataclass():
    conn = FakeConnection(PLSCOPE_STATEMENTS_BATCH_ROWS)
    rows = extract.fetch_plscope_statements_batch(conn, "GESTAO", "PKG_A,PKG_B")

    assert len(rows) == 2
    assert all(isinstance(r, extract.PlscopeStatementBatchRow) for r in rows)

    insert_row = rows[0]
    assert insert_row.object_name == "PKG_A"
    assert insert_row.line == 6
    assert insert_row.stmt_type == "INSERT"
    assert insert_row.sql_id == "5rhw9u2hz7b12"

    dynamic_row = rows[1]
    assert dynamic_row.object_name == "PKG_B"
    assert dynamic_row.stmt_type == "EXECUTE IMMEDIATE"
    assert dynamic_row.sql_id is None

    sql, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "object_list": "PKG_A,PKG_B"}
    assert sql == queries.QUERY_TEXT["plscope_statements_batch.sql"].rstrip().rstrip(";")


def test_fetch_source_batch_binds_and_maps_dataclass():
    conn = FakeConnection(FETCH_SOURCE_BATCH_ROWS)
    rows = extract.fetch_source_batch(conn, "GESTAO", "PKG_A,PKG_B")

    assert len(rows) == 2
    assert all(isinstance(r, extract.FetchSourceBatchRow) for r in rows)
    assert rows[0].name == "PKG_A"
    assert rows[0].type == "PACKAGE BODY"
    assert rows[1].name == "PKG_B"

    sql, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "object_list": "PKG_A,PKG_B", "object_type": None}
    assert sql == queries.QUERY_TEXT["fetch_source_batch.sql"].rstrip().rstrip(";")


def test_fetch_source_batch_passes_object_type_bind():
    conn = FakeConnection(FETCH_SOURCE_BATCH_ROWS[:1])
    extract.fetch_source_batch(conn, "GESTAO", "PKG_A", object_type="PACKAGE BODY")
    _, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "object_list": "PKG_A", "object_type": "PACKAGE BODY"}


def test_batch_fetchers_run_query_only_select_guard():
    # As tres queries batch tem que passar pela mesma guarda somente-leitura
    # de db.run_query -- exercitado aqui contra o texto real do disco.
    for name in BATCH_SQL_FILES:
        conn = FakeConnection([])
        db.run_query(
            conn,
            queries.QUERY_TEXT[name],
            {"owner": "GESTAO", "object_list": None, "object_type": None},
        )
        assert conn.last_cursor is not None
        assert conn.last_cursor.closed is True


# --------------------------------------------------------- (c) chunk_names


def test_chunk_names_splits_above_batch_chunk_size():
    names = ["OBJ_{:04d}".format(i) for i in range(450)]
    chunks = extract.chunk_names(names)
    assert len(chunks) == 3  # 450 nomes / 200 por chunk = 3 pedacos (200, 200, 50)
    assert chunks[0].count(",") == extract.BATCH_CHUNK_SIZE - 1
    assert chunks[2].count(",") == 49


def test_chunk_names_normalizes_uppercase_dedupes_and_sorts():
    chunks = extract.chunk_names(["pkg_b", "PKG_A", "pkg_a", " pkg_c ", "PKG_B"])
    assert chunks == ["PKG_A,PKG_B,PKG_C"]


def test_chunk_names_deterministic_regardless_of_input_order():
    first = extract.chunk_names(["PKG_C", "PKG_A", "PKG_B"])
    second = extract.chunk_names(["PKG_B", "PKG_C", "PKG_A"])
    assert first == second == ["PKG_A,PKG_B,PKG_C"]


def test_chunk_names_ignores_blank_entries():
    chunks = extract.chunk_names(["PKG_A", "", "  ", "PKG_B"])
    assert chunks == ["PKG_A,PKG_B"]


def test_chunk_names_respects_custom_size():
    chunks = extract.chunk_names(["PKG_A", "PKG_B", "PKG_C"], size=2)
    assert chunks == ["PKG_A,PKG_B", "PKG_C"]


def test_chunk_names_empty_input_returns_empty_list():
    assert extract.chunk_names([]) == []
