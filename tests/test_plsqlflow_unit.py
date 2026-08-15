"""Testes offline (T-01) para plsqlflow/queries.py, db.py e extract.py.

Nao abre conexao real e nao depende de rede/banco -- todo dado de banco vem
de fixtures/fakes em memoria ou do fixture gravado
tests/fixtures/flow_demo_extract.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from plsqlflow import db, extract, queries

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "flow_demo_extract.json"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------- queries.py

def test_all_ten_queries_load_non_empty():
    assert len(queries.QUERY_NAMES) == 10
    for name in queries.QUERY_NAMES:
        text = queries.QUERY_TEXT[name]
        assert text.strip(), "{} carregou vazio".format(name)


def test_query_text_matches_key_dictionary_view():
    expectations = {
        "resolve_target.sql": "all_arguments",
        "plscope_check.sql": "all_plsql_object_settings",
        "plscope_calls.sql": "all_identifiers",
        "plscope_statements.sql": "all_statements",
        "fetch_source.sql": "all_source",
        "deps_direct.sql": "all_dependencies",
        "triggers_for_tables.sql": "all_triggers",
        "fk_cascade.sql": "all_constraints",
        "type_hierarchy.sql": "all_type",
        "resolve_synonym.sql": "all_synonyms",
    }
    offenders = []
    for name, view in expectations.items():
        if view not in queries.QUERY_TEXT[name].lower():
            offenders.append("{} nao referencia {}".format(name, view))
    assert not offenders, offenders


def test_load_query_matches_file_on_disk():
    for name in queries.QUERY_NAMES:
        on_disk = (REPO / "sql" / "flow" / name).read_text(encoding="utf-8")
        assert queries.load_query(name) == on_disk


def test_load_query_unknown_name_raises():
    with pytest.raises(ValueError):
        queries.load_query("does_not_exist.sql")


# -------------------------------------------------------------------- db.py

def test_resolve_connection_params_env_fallback():
    env = {
        "PLSQLFLOW_USER": "gestao",
        "PLSQLFLOW_PWD": "secret",
        "PLSQLFLOW_DSN": "localhost:1521/XEPDB1",
    }
    params = db.resolve_connection_params(alias=None, env=env)
    assert params.user == "gestao"
    assert params.password == "secret"
    assert params.dsn == "localhost:1521/XEPDB1"


def test_resolve_connection_params_missing_raises_clear_error():
    with pytest.raises(db.ConnectionConfigError, match="Configuracao de conexao ausente"):
        db.resolve_connection_params(alias=None, env={})


def test_resolve_connection_params_invalid_alias_rejected():
    with pytest.raises(ValueError):
        db.resolve_connection_params(alias="dev; DROP TABLE x", env={})


def test_resolve_connection_params_invalid_user_from_env_rejected():
    env = {
        "PLSQLFLOW_USER": "gestao; DROP TABLE x",
        "PLSQLFLOW_PWD": "secret",
        "PLSQLFLOW_DSN": "localhost:1521/XEPDB1",
    }
    with pytest.raises(ValueError):
        db.resolve_connection_params(alias=None, env=env)


def test_resolve_connection_params_alias_missing_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "CONFIG_PATH", tmp_path / "does-not-exist.json")
    with pytest.raises(db.ConnectionConfigError, match="Config de conexao ausente"):
        db.resolve_connection_params(alias="dev", env={})


def test_resolve_connection_params_alias_success(monkeypatch, tmp_path):
    config_path = tmp_path / "flow-connections.json"
    config_path.write_text(
        json.dumps({"dev": {"user": "gestao", "dsn": "localhost:1521/XEPDB1"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "CONFIG_PATH", config_path)
    env = {"PLSQLFLOW_PWD_DEV": "secret"}
    params = db.resolve_connection_params(alias="dev", env=env)
    assert params.user == "gestao"
    assert params.dsn == "localhost:1521/XEPDB1"
    assert params.password == "secret"


def test_resolve_connection_params_alias_missing_password(monkeypatch, tmp_path):
    config_path = tmp_path / "flow-connections.json"
    config_path.write_text(
        json.dumps({"dev": {"user": "gestao", "dsn": "localhost:1521/XEPDB1"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "CONFIG_PATH", config_path)
    with pytest.raises(db.ConnectionConfigError, match="PLSQLFLOW_PWD_DEV"):
        db.resolve_connection_params(alias="dev", env={})


def test_resolve_connection_params_alias_unknown(monkeypatch, tmp_path):
    config_path = tmp_path / "flow-connections.json"
    config_path.write_text(json.dumps({"dev": {"user": "gestao", "dsn": "x"}}), encoding="utf-8")
    monkeypatch.setattr(db, "CONFIG_PATH", config_path)
    with pytest.raises(db.ConnectionConfigError, match="hml"):
        db.resolve_connection_params(alias="hml", env={"PLSQLFLOW_PWD_HML": "secret"})


def test_connect_uses_resolved_params_and_never_opens_real_connection(monkeypatch):
    captured = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return "fake-connection"

    monkeypatch.setattr(db.oracledb, "connect", fake_connect)
    env = {
        "PLSQLFLOW_USER": "gestao",
        "PLSQLFLOW_PWD": "secret",
        "PLSQLFLOW_DSN": "localhost:1521/XEPDB1",
    }
    result = db.connect(alias=None, env=env)
    assert result == "fake-connection"
    assert captured == {
        "user": "gestao",
        "password": "secret",
        "dsn": "localhost:1521/XEPDB1",
    }


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


def test_run_query_maps_rows_to_list_of_dict():
    conn = FakeConnection([{"OBJECT_NAME": "FLOW_DEMO", "SUBPROGRAM_ID": 1}])
    rows = db.run_query(conn, "SELECT object_name, subprogram_id FROM all_procedures", {})
    assert rows == [{"OBJECT_NAME": "FLOW_DEMO", "SUBPROGRAM_ID": 1}]
    assert conn.last_cursor.closed is True


def test_run_query_empty_result_returns_empty_list():
    conn = FakeConnection([])
    rows = db.run_query(conn, "SELECT * FROM all_procedures WHERE 1=0", {})
    assert rows == []


def test_run_query_rejects_non_select():
    conn = FakeConnection([])
    with pytest.raises(ValueError):
        db.run_query(conn, "DELETE FROM flow_demo_log", {})
    assert conn.last_cursor is None


def test_run_query_rejects_ddl():
    conn = FakeConnection([])
    with pytest.raises(ValueError):
        db.run_query(conn, "DROP TABLE flow_demo_log", {})


# ------------------------------------------------------------------ extract.py

FIXTURE = load_fixture()


def test_fetch_resolve_target_maps_dataclasses():
    conn = FakeConnection(FIXTURE["resolve_target"])
    rows = extract.fetch_resolve_target(conn, "GESTAO", "FLOW_DEMO")
    assert len(rows) == len(FIXTURE["resolve_target"])
    assert all(isinstance(r, extract.ResolveTargetRow) for r in rows)

    header = rows[0]
    assert header.object_name == "FLOW_DEMO"
    assert header.procedure_name is None
    assert header.subprogram_id == 0

    main_p_n = rows[1]
    assert main_p_n.procedure_name == "MAIN"
    assert main_p_n.argument_name == "P_N"
    assert main_p_n.data_type == "NUMBER"

    overload_1 = next(
        r for r in rows if r.procedure_name == "CALC_OVERLOAD" and r.overload == "1" and r.position == 1
    )
    assert overload_1.data_type == "NUMBER"

    overload_2 = next(
        r for r in rows if r.procedure_name == "CALC_OVERLOAD" and r.overload == "2" and r.position == 1
    )
    assert overload_2.data_type == "VARCHAR2"


def test_fetch_plscope_calls_maps_dataclasses_including_recursion():
    conn = FakeConnection(FIXTURE["plscope_calls"])
    rows = extract.fetch_plscope_calls(conn, "GESTAO", "FLOW_DEMO")
    assert len(rows) == len(FIXTURE["plscope_calls"])
    assert all(isinstance(r, extract.PlscopeCallRow) for r in rows)

    proc_a_to_b = next(r for r in rows if r.line == 13 and r.called_name == "PROC_B")
    assert proc_a_to_b.decl_type == "PROCEDURE"
    assert proc_a_to_b.decl_object_type == "PACKAGE"

    proc_b_to_a = next(r for r in rows if r.line == 20 and r.called_name == "PROC_A")
    assert proc_b_to_a.decl_owner == "GESTAO"
    assert proc_b_to_a.decl_line == 3

    overload_calls = [r for r in rows if r.called_name == "CALC_OVERLOAD"]
    assert len(overload_calls) == 2
    assert {r.line for r in overload_calls} == {49, 50}


def test_fetch_plscope_statements_maps_dataclasses():
    conn = FakeConnection(FIXTURE["plscope_statements"])
    rows = extract.fetch_plscope_statements(conn, "GESTAO", "FLOW_DEMO")
    assert len(rows) == len(FIXTURE["plscope_statements"])
    assert all(isinstance(r, extract.PlscopeStatementRow) for r in rows)

    insert_stmt = next(r for r in rows if r.stmt_type == "INSERT")
    assert insert_stmt.line == 6
    assert insert_stmt.sql_id == "5rhw9u2hz7b12"

    dynamic_stmts = [r for r in rows if r.stmt_type == "EXECUTE IMMEDIATE"]
    assert {r.line for r in dynamic_stmts} == {28, 31}
    assert all(r.sql_id is None for r in dynamic_stmts)


def test_fetch_triggers_for_tables_maps_dataclasses():
    conn = FakeConnection(FIXTURE["triggers_for_tables"])
    rows = extract.fetch_triggers_for_tables(conn, "GESTAO", "FLOW_DEMO_LOG")
    assert len(rows) == 1
    assert isinstance(rows[0], extract.TriggerRow)
    assert rows[0].table_name == "FLOW_DEMO_LOG"
    assert rows[0].trigger_name == "TRG_FLOW_DEMO_LOG"
    assert rows[0].triggering_event == "INSERT"
    assert rows[0].status == "ENABLED"


def test_fetch_fk_cascade_empty_result():
    conn = FakeConnection(FIXTURE["fk_cascade"])
    rows = extract.fetch_fk_cascade(conn, "GESTAO", "FLOW_DEMO_LOG")
    assert rows == []


def test_fetch_type_hierarchy_empty_result():
    conn = FakeConnection(FIXTURE["type_hierarchy"])
    rows = extract.fetch_type_hierarchy(conn, "GESTAO", "SOME_TYPE")
    assert rows == []


def test_fetch_resolve_synonym_empty_result():
    conn = FakeConnection(FIXTURE["resolve_synonym"])
    rows = extract.fetch_resolve_synonym(conn, "GESTAO", "FLOW_DEMO")
    assert rows == []
