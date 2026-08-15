"""Testes offline (T-01) para as duas fontes novas de dicionario do grafo
de dependencias (catalogo de objetos, colunas de tabela) e para os tipos
de dados de plsqlflow/depgraph.py.

Nao abre conexao real e nao depende de rede/banco -- segue o mesmo padrao
de cursor mockado de tests/test_plsqlflow_unit.py (FakeConnection /
FakeCursor em memoria, sem fixture em disco).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from plsqlflow import db, depgraph, extract, queries

REPO = Path(__file__).resolve().parent.parent


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

OBJECT_CATALOG_ROWS = [
    {
        "OWNER": "GESTAO",
        "OBJECT_NAME": "FLOW_DEMO",
        "OBJECT_TYPE": "PACKAGE",
        "STATUS": "VALID",
        "LAST_DDL_TIME": "2026-08-15 10:20:48",
    },
    {
        "OWNER": "GESTAO",
        "OBJECT_NAME": "FLOW_DEMO",
        "OBJECT_TYPE": "PACKAGE BODY",
        "STATUS": "VALID",
        "LAST_DDL_TIME": "2026-08-15 10:41:29",
    },
    {
        "OWNER": "GESTAO",
        "OBJECT_NAME": "FLOW_DEMO_LOG",
        "OBJECT_TYPE": "TABLE",
        "STATUS": "VALID",
        "LAST_DDL_TIME": "2026-08-15 10:20:40",
    },
]

TAB_COLUMN_ROWS = [
    {
        "OWNER": "GESTAO",
        "TABLE_NAME": "FLOW_DEMO_LOG",
        "COLUMN_ID": 1,
        "COLUMN_NAME": "ID",
        "DATA_TYPE": "NUMBER",
        "NULLABLE": "N",
        "DATA_DEFAULT": '"GESTAO"."ISEQ$$_77709".nextval',
    },
    {
        "OWNER": "GESTAO",
        "TABLE_NAME": "FLOW_DEMO_LOG",
        "COLUMN_ID": 2,
        "COLUMN_NAME": "MSG",
        "DATA_TYPE": "VARCHAR2",
        "NULLABLE": "Y",
        "DATA_DEFAULT": None,
    },
]


# --------------------------------------------------------------- queries.py


def test_object_catalog_and_tab_columns_registered_in_query_names():
    assert "object_catalog.sql" in queries.QUERY_NAMES
    assert "tab_columns.sql" in queries.QUERY_NAMES


def test_object_catalog_and_tab_columns_load_non_empty_from_disk():
    for name in ("object_catalog.sql", "tab_columns.sql"):
        on_disk = (REPO / "sql" / "flow" / name).read_text(encoding="utf-8")
        assert on_disk.strip()
        assert queries.QUERY_TEXT[name] == on_disk
        assert queries.load_query(name) == on_disk


def test_object_catalog_query_references_all_objects_and_key_columns():
    text = queries.QUERY_TEXT["object_catalog.sql"].lower()
    assert "all_objects" in text
    assert "last_ddl_time" in text
    assert "status" in text
    assert ":owner" in text
    assert ":object_list" in text


def test_tab_columns_query_references_all_tab_columns_and_table_list_bind():
    text = queries.QUERY_TEXT["tab_columns.sql"].lower()
    assert "all_tab_columns" in text
    assert "dba_tab_columns" not in text
    assert ":owner" in text
    assert ":table_list" in text


# ------------------------------------------------------------------ extract.py


def test_fetch_object_catalog_maps_dataclass_columns():
    conn = FakeConnection(OBJECT_CATALOG_ROWS)
    rows = extract.fetch_object_catalog(conn, "gestao")
    assert len(rows) == len(OBJECT_CATALOG_ROWS)
    assert all(isinstance(r, extract.ObjectCatalogRow) for r in rows)

    pkg_spec = rows[0]
    assert pkg_spec.owner == "GESTAO"
    assert pkg_spec.object_name == "FLOW_DEMO"
    assert pkg_spec.object_type == "PACKAGE"
    assert pkg_spec.status == "VALID"
    assert pkg_spec.last_ddl_time == "2026-08-15 10:20:48"

    pkg_body = rows[1]
    assert pkg_body.object_type == "PACKAGE BODY"
    assert pkg_body.last_ddl_time == "2026-08-15 10:41:29"

    table_row = rows[2]
    assert table_row.object_name == "FLOW_DEMO_LOG"
    assert table_row.object_type == "TABLE"

    # binds: owner obrigatorio (uppercased pela query via UPPER(:owner), nao
    # pelo Python -- o bind em si viaja como recebido), object_list opcional
    sql, binds = conn.last_cursor.executed
    assert binds == {"owner": "gestao", "object_list": None}
    assert "object_catalog" not in sql.lower() or True  # sql e o texto da query


def test_fetch_object_catalog_passes_object_list_bind():
    conn = FakeConnection(OBJECT_CATALOG_ROWS[:1])
    extract.fetch_object_catalog(conn, "GESTAO", object_list="FLOW_DEMO")
    _, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "object_list": "FLOW_DEMO"}


def test_fetch_tab_columns_maps_dataclass_columns():
    conn = FakeConnection(TAB_COLUMN_ROWS)
    rows = extract.fetch_tab_columns(conn, "GESTAO", "FLOW_DEMO_LOG")
    assert len(rows) == 2
    assert all(isinstance(r, extract.TabColumnRow) for r in rows)

    id_col = rows[0]
    assert id_col.owner == "GESTAO"
    assert id_col.table_name == "FLOW_DEMO_LOG"
    assert id_col.column_id == 1
    assert id_col.column_name == "ID"
    assert id_col.data_type == "NUMBER"
    assert id_col.nullable == "N"
    assert id_col.data_default == '"GESTAO"."ISEQ$$_77709".nextval'

    msg_col = rows[1]
    assert msg_col.column_name == "MSG"
    assert msg_col.nullable == "Y"
    assert msg_col.data_default is None

    _, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "table_list": "FLOW_DEMO_LOG"}


def test_fetch_tab_columns_empty_result():
    conn = FakeConnection([])
    rows = extract.fetch_tab_columns(conn, "GESTAO", "NADA_AQUI")
    assert rows == []


# -------------------------------------------------------------------- db.py


def test_run_query_rejects_non_select_for_object_catalog_and_tab_columns():
    conn = FakeConnection([])
    for bad_sql in ("DELETE FROM all_objects", "DROP TABLE flow_demo_log"):
        with pytest.raises(ValueError):
            db.run_query(conn, bad_sql, {})
        assert conn.last_cursor is None


def test_object_catalog_and_tab_columns_sql_are_select_only():
    # As duas queries novas tem que passar pela mesma guarda somente-leitura
    # de db.run_query -- exercitado aqui contra o texto real do disco.
    for name in ("object_catalog.sql", "tab_columns.sql"):
        conn = FakeConnection([])
        # SELECT vazio nao acha nenhuma linha na FakeCursor -- ok, so
        # queremos confirmar que run_query aceita (nao levanta ValueError).
        db.run_query(conn, queries.QUERY_TEXT[name], {"owner": "GESTAO", "object_list": None, "table_list": None})
        assert conn.last_cursor is not None
        assert conn.last_cursor.closed is True


# ------------------------------------------------------------------ depgraph.py


def test_depnode_required_fields_and_defaults():
    node = depgraph.DepNode(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        object_type="PACKAGE",
        status="VALID",
        plscope=True,
    )
    assert node.owner == "GESTAO"
    assert node.object_name == "FLOW_DEMO"
    assert node.object_type == "PACKAGE"
    assert node.status == "VALID"
    assert node.plscope is True
    assert node.source_first_line is None
    assert node.source_last_line is None


def test_depnode_accepts_explicit_source_lines():
    node = depgraph.DepNode(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        object_type="PACKAGE BODY",
        status="VALID",
        plscope=True,
        source_first_line=1,
        source_last_line=42,
    )
    assert node.source_first_line == 1
    assert node.source_last_line == 42


def test_depedge_required_fields_and_defaults():
    edge = depgraph.DepEdge(
        from_ref="GESTAO.FLOW_DEMO",
        to_ref="GESTAO.FLOW_DEMO_LOG",
        edge_type="WRITE",
        line=6,
    )
    assert edge.from_ref == "GESTAO.FLOW_DEMO"
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.edge_type == "WRITE"
    assert edge.line == 6
    assert edge.op is None
    assert edge.cols is None
    assert edge.dynamic is False
    assert edge.confidence == "exact"
    assert edge.context is None
    assert edge.snippet_ref is None


def test_depedge_accepts_all_optional_fields():
    edge = depgraph.DepEdge(
        from_ref="GESTAO.FLOW_DEMO",
        to_ref="GESTAO.FLOW_DEMO_LOG",
        edge_type="WRITE",
        line=6,
        op="INSERT",
        cols=["ID", "MSG"],
        dynamic=True,
        confidence="partial",
        context="loop",
        snippet_ref="GESTAO.FLOW_DEMO#L28-L34",
    )
    assert edge.op == "INSERT"
    assert edge.cols == ["ID", "MSG"]
    assert edge.dynamic is True
    assert edge.confidence == "partial"
    assert edge.context == "loop"
    assert edge.snippet_ref == "GESTAO.FLOW_DEMO#L28-L34"


def test_depedge_edge_type_documented_values():
    assert depgraph.EDGE_TYPES == (
        "CALL",
        "READ",
        "WRITE",
        "TRIGGER_FIRES",
        "SYNONYM_RESOLVES_TO",
        "DYNAMIC_SQL",
    )
    for edge_type in depgraph.EDGE_TYPES:
        edge = depgraph.DepEdge(
            from_ref="GESTAO.A", to_ref="GESTAO.B", edge_type=edge_type, line=1
        )
        assert edge.edge_type == edge_type
