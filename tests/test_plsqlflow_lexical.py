"""Testes offline (T-03) para plsqlflow/dynsql.py e plsqlflow/lexical.py.

Sem conexao real -- o caso principal vem do fixture gravado
tests/fixtures/flow_demo_extract.json (pacote GESTAO.FLOW_DEMO, procedure
run_dynamic), complementado por casos sinteticos pequenos.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from plsqlflow import dynsql, lexical

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "flow_demo_extract.json"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def load_run_dynamic_rows() -> List[dynsql.FetchSourceRow]:
    fixture = load_fixture()
    lines = fixture["fetch_source"]["PACKAGE BODY"]
    return dynsql.rows_from_lines(lines)


# ------------------------------------------------------------------ dynsql.py


def test_execute_immediate_pure_literal_resolves_from_fixture():
    rows = load_run_dynamic_rows()
    result = dynsql.resolve_dynamic_sql(rows, line=28)
    assert result.resolved is True
    assert result.sql_text == "INSERT INTO flow_demo_log (msg) VALUES ('dyn-fixo')"
    assert result.fragment == result.sql_text
    assert result.unresolved_ref is None
    assert result.line == 28


def test_execute_immediate_variable_built_with_parameter_is_unresolved_from_fixture():
    rows = load_run_dynamic_rows()
    result = dynsql.resolve_dynamic_sql(rows, line=31)
    assert result.resolved is False
    assert result.sql_text is None
    assert result.unresolved_ref == "p_tag"
    assert result.fragment == "INSERT INTO flow_demo_log (msg) VALUES ('<p_tag>')"
    assert result.line == 31


def test_execute_immediate_concat_of_two_literals_resolves():
    lines = [
        "  PROCEDURE demo IS\n",
        "  BEGIN\n",
        "    EXECUTE IMMEDIATE 'SELECT * FROM ' || 'minha_tabela';\n",
        "  END demo;\n",
    ]
    rows = dynsql.rows_from_lines(lines)
    result = dynsql.resolve_dynamic_sql(rows, line=3)
    assert result.resolved is True
    assert result.sql_text == "SELECT * FROM minha_tabela"
    assert result.unresolved_ref is None


def test_package_constant_without_value_is_unresolved_and_documented():
    lines = [
        "  PROCEDURE demo IS\n",
        "    v_sql VARCHAR2(100);\n",
        "  BEGIN\n",
        "    v_sql := 'SELECT * FROM ' || c_table_name;\n",
        "    EXECUTE IMMEDIATE v_sql;\n",
        "  END demo;\n",
    ]
    rows = dynsql.rows_from_lines(lines)

    unresolved = dynsql.resolve_dynamic_sql(rows, line=5)
    assert unresolved.resolved is False
    assert unresolved.sql_text is None
    assert unresolved.unresolved_ref == "c_table_name"

    resolved = dynsql.resolve_dynamic_sql(
        rows, line=5, package_constants={"c_table_name": "minha_tabela"}
    )
    assert resolved.resolved is True
    assert resolved.sql_text == "SELECT * FROM minha_tabela"
    assert resolved.unresolved_ref is None


def test_unresolved_with_known_object_name_marks_likely():
    lines = [
        "  PROCEDURE demo IS\n",
        "    v_sql VARCHAR2(100);\n",
        "  BEGIN\n",
        "    v_sql := 'SELECT * FROM flow_demo_log WHERE tag = ''' || p_tag || '''';\n",
        "    EXECUTE IMMEDIATE v_sql;\n",
        "  END demo;\n",
    ]
    rows = dynsql.rows_from_lines(lines)
    result = dynsql.resolve_dynamic_sql(rows, line=5, known_object_names=["flow_demo_log"])
    assert result.resolved is False
    assert result.likely is True

    result_unknown = dynsql.resolve_dynamic_sql(rows, line=5, known_object_names=["outra_tabela"])
    assert result_unknown.likely is False


# ----------------------------------------------------------------- lexical.py

SYNTHETIC_BLOCK = [
    "  BEGIN\n",
    "    -- chama helper conhecido: another_call(1);\n",
    "    v_flag := known_helper(p_x);\n",
    "    /* bloco fake_call(1);\n",
    "       multi-linha comment_call(2); */\n",
    "    log_something('valor ''especial'' aqui');\n",
    "    unknown_call(p_x);\n",
    "    p_x;\n",
    "    IF v_flag > 0 THEN\n",
    "      NULL;\n",
    "    END IF;\n",
    "  END;\n",
]

LOCAL_VARS = ["v_flag", "p_x"]
DEPS_DIRECT = ["known_helper", "log_something"]


def _by_name(candidates):
    return {c.name.upper(): c for c in candidates}


def test_strip_comments_and_literals_removes_line_and_block_comments():
    clean_lines, literals = lexical.strip_comments_and_literals(SYNTHETIC_BLOCK)
    joined = "".join(clean_lines)
    assert "another_call" not in joined
    assert "fake_call" not in joined
    assert "comment_call" not in joined
    assert len(literals) == 1
    assert literals[0].line == 6
    assert literals[0].text == "'valor ''especial'' aqui'"


def test_scan_candidates_filters_keywords_and_local_vars():
    result = lexical.scan_candidates(SYNTHETIC_BLOCK, LOCAL_VARS, DEPS_DIRECT)
    names = {c.name.upper() for c in result.candidates}

    assert "IF" not in names
    assert "END" not in names
    assert "NULL" not in names
    assert "V_FLAG" not in names
    assert "P_X" not in names
    assert "ANOTHER_CALL" not in names
    assert "FAKE_CALL" not in names
    assert "COMMENT_CALL" not in names


def test_scan_candidates_confidence_for_known_and_unknown_calls():
    result = lexical.scan_candidates(SYNTHETIC_BLOCK, LOCAL_VARS, DEPS_DIRECT)
    by_name = _by_name(result.candidates)

    known_helper = by_name["KNOWN_HELPER"]
    assert known_helper.confidence == "lexical"
    assert known_helper.confirmed is True
    assert known_helper.line == 3

    log_something = by_name["LOG_SOMETHING"]
    assert log_something.confidence == "lexical"
    assert log_something.confirmed is True
    assert log_something.line == 6

    unknown_call = by_name["UNKNOWN_CALL"]
    assert unknown_call.confidence == "heuristic"
    assert unknown_call.confirmed is False
    assert unknown_call.line == 7


# ------------------------------------------------- lexical.py: modo fragmento SQL (T-03)

SQL_FRAGMENT_BLOCK = [
    "  SELECT a.id, b.val\n",
    "    FROM tabela_a a\n",
    "    JOIN esquema.tabela_b b ON b.id = a.id\n",
    "   WHERE a.id > 0;\n",
    "  -- FROM tabela_comentada nao deve aparecer\n",
    "  v_txt := 'FROM tabela_string_literal';\n",
    "  INSERT INTO owner_x.tabela_c (col1) VALUES (1);\n",
    "  UPDATE tabela_d SET col1 = 1 WHERE id = 1;\n",
    "  MERGE INTO tabela_e e USING tabela_f f ON (e.id = f.id);\n",
]


def _object_ref_names(clean_lines):
    return [(name.upper(), line) for name, line in lexical.find_object_reference_candidates(clean_lines)]


def test_find_object_reference_candidates_after_from_join_into_update():
    clean_lines, _ = lexical.strip_comments_and_literals(SQL_FRAGMENT_BLOCK)
    refs = _object_ref_names(clean_lines)

    assert ("TABELA_A", 2) in refs
    assert ("ESQUEMA.TABELA_B", 3) in refs
    assert ("OWNER_X.TABELA_C", 7) in refs
    assert ("TABELA_D", 8) in refs
    assert ("TABELA_E", 9) in refs


def test_find_object_reference_candidates_captures_qualified_owner_table_as_one_unit():
    clean_lines, _ = lexical.strip_comments_and_literals(SQL_FRAGMENT_BLOCK)
    refs = _object_ref_names(clean_lines)

    names = [name for name, _ in refs]
    assert "ESQUEMA.TABELA_B" in names
    # nao deve fragmentar em "ESQUEMA" isolado
    assert "ESQUEMA" not in names
    assert "OWNER_X.TABELA_C" in names
    assert "OWNER_X" not in names


def test_find_object_reference_candidates_ignores_comments_and_string_literals():
    clean_lines, _ = lexical.strip_comments_and_literals(SQL_FRAGMENT_BLOCK)
    refs = _object_ref_names(clean_lines)

    names = [name for name, _ in refs]
    assert "TABELA_COMENTADA" not in names
    assert "TABELA_STRING_LITERAL" not in names


def test_find_object_reference_candidates_on_raw_lines_without_stripping_would_leak():
    # Confirma que a limpeza previa (strip_comments_and_literals) e o que
    # protege contra falso positivo -- sem ela, comentario/literal vazam.
    refs = _object_ref_names(SQL_FRAGMENT_BLOCK)
    names = [name for name, _ in refs]
    assert "TABELA_COMENTADA" in names
    assert "TABELA_STRING_LITERAL" in names
