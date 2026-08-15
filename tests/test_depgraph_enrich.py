"""Testes offline (T-03) para plsqlflow/depgraph_enrich.py.

100% offline -- usa o fixture real tests/fixtures/flow_demo_extract.json
(pacote GESTAO.FLOW_DEMO) para os casos principais (INSERT estatico linha
6, EXECUTE IMMEDIATE literal linha 28, EXECUTE IMMEDIATE montado em
variavel linha 31), complementado por statements sinteticos pequenos para
cobrir UPDATE/DELETE e os casos "sem alvo determinavel"/"sem coluna
reconhecivel", seguindo o mesmo padrao de tests/test_plsqlflow_lexical.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from plsqlflow import depgraph_enrich, dynsql
from plsqlflow.depgraph_enrich import UNKNOWN_TARGET
from plsqlflow.extract import PlscopeStatementRow

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "flow_demo_extract.json"

OWNER = "GESTAO"
OBJECT_NAME = "FLOW_DEMO"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def load_statements() -> List[PlscopeStatementRow]:
    fixture = load_fixture()
    return [PlscopeStatementRow(**row) for row in fixture["plscope_statements"]]


def load_source():
    fixture = load_fixture()
    lines = fixture["fetch_source"]["PACKAGE BODY"]
    return dynsql.rows_from_lines(lines)


# ------------------------------------------------------ table_edges_from_statements


def test_insert_line_6_becomes_write_edge_op_insert_target_flow_demo_log():
    statements = load_statements()
    source = load_source()

    edges = depgraph_enrich.table_edges_from_statements(statements, source, OWNER, OBJECT_NAME)

    write_edges = [e for e in edges if e.line == 6]
    assert len(write_edges) == 1
    edge = write_edges[0]
    assert edge.edge_type == "WRITE"
    assert edge.op == "INSERT"
    assert edge.line == 6
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.from_ref == "GESTAO.FLOW_DEMO"
    assert edge.dynamic is False


def test_execute_immediate_statements_are_not_emitted_as_table_edges():
    # Linhas 28/31 sao EXECUTE IMMEDIATE (SQL dinamico) -- table_edges_from_
    # statements nao deve gerar READ/WRITE para elas, isso e papel de
    # dynamic_sql_findings.
    statements = load_statements()
    source = load_source()

    edges = depgraph_enrich.table_edges_from_statements(statements, source, OWNER, OBJECT_NAME)
    lines_covered = {e.line for e in edges}
    assert 28 not in lines_covered
    assert 31 not in lines_covered


def test_insert_columns_extracted_when_present_in_text():
    statements = load_statements()
    source = load_source()
    edges = depgraph_enrich.table_edges_from_statements(statements, source, OWNER, OBJECT_NAME)

    insert_edge = next(e for e in edges if e.line == 6)
    assert insert_edge.cols == ["MSG"]


def test_update_columns_extracted_from_set_clause():
    statements = [
        PlscopeStatementRow(
            line=10,
            stmt_type="UPDATE",
            sql_id="abc",
            has_into_record="NO",
            text="UPDATE minha_tabela SET col_a = 1, col_b = func(x, y) WHERE id = :b1",
        )
    ]
    edges = depgraph_enrich.table_edges_from_statements(statements, [], OWNER, OBJECT_NAME)

    assert len(edges) == 1
    edge = edges[0]
    assert edge.edge_type == "WRITE"
    assert edge.op == "UPDATE"
    assert edge.to_ref == "GESTAO.MINHA_TABELA"
    assert edge.cols == ["col_a", "col_b"]


def test_delete_without_recognizable_columns_has_cols_none_not_error():
    statements = [
        PlscopeStatementRow(
            line=20,
            stmt_type="DELETE",
            sql_id="def",
            has_into_record="NO",
            text="DELETE FROM minha_tabela WHERE id = :b1",
        )
    ]
    edges = depgraph_enrich.table_edges_from_statements(statements, [], OWNER, OBJECT_NAME)

    assert len(edges) == 1
    edge = edges[0]
    assert edge.op == "DELETE"
    assert edge.to_ref == "GESTAO.MINHA_TABELA"
    assert edge.cols is None


def test_insert_without_column_list_has_cols_none():
    statements = [
        PlscopeStatementRow(
            line=30,
            stmt_type="INSERT",
            sql_id="ghi",
            has_into_record="NO",
            text="INSERT INTO minha_tabela VALUES (1, 2)",
        )
    ]
    edges = depgraph_enrich.table_edges_from_statements(statements, [], OWNER, OBJECT_NAME)
    assert edges[0].cols is None


def test_statement_without_determinable_target_still_produces_edge_with_unknown_marker():
    # text=None e a linha nao existe no source -- nao ha como extrair o
    # alvo, mas o statement nao pode sumir do grafo.
    statements = [
        PlscopeStatementRow(
            line=99, stmt_type="SELECT", sql_id="xyz", has_into_record="NO", text=None
        )
    ]
    edges = depgraph_enrich.table_edges_from_statements(statements, [], OWNER, OBJECT_NAME)

    assert len(edges) == 1
    assert edges[0].edge_type == "READ"
    assert edges[0].to_ref == UNKNOWN_TARGET
    assert edges[0].line == 99


def test_select_with_join_produces_one_read_edge_per_table():
    statements = [
        PlscopeStatementRow(
            line=40,
            stmt_type="SELECT",
            sql_id="jkl",
            has_into_record="NO",
            text="SELECT a.id FROM tabela_a a JOIN tabela_b b ON b.id = a.id",
        )
    ]
    edges = depgraph_enrich.table_edges_from_statements(statements, [], OWNER, OBJECT_NAME)

    assert {e.to_ref for e in edges} == {"GESTAO.TABELA_A", "GESTAO.TABELA_B"}
    assert all(e.edge_type == "READ" for e in edges)
    assert all(e.line == 40 for e in edges)


# ---------------------------------------------------------- dynamic_sql_findings


def test_execute_immediate_line_28_literal_classified_as_resolved():
    statements = load_statements()
    source = load_source()

    findings = depgraph_enrich.dynamic_sql_findings(
        statements, source, catalog_names=["FLOW_DEMO_LOG"], owner=OWNER, object_name=OBJECT_NAME
    )
    finding = next(f for f in findings if f.line == 28)

    assert finding.level == "resolved"
    assert len(finding.edges) == 1
    edge = finding.edges[0]
    assert edge.edge_type == "DYNAMIC_SQL"
    assert edge.dynamic is True
    assert edge.confidence == "exact"
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.line == 28


def test_execute_immediate_line_31_with_matching_catalog_is_partial():
    statements = load_statements()
    source = load_source()

    findings = depgraph_enrich.dynamic_sql_findings(
        statements, source, catalog_names=["FLOW_DEMO_LOG"], owner=OWNER, object_name=OBJECT_NAME
    )
    finding = next(f for f in findings if f.line == 31)

    assert finding.level == "partial"
    assert finding.candidates == ["flow_demo_log"]
    assert len(finding.edges) == 1
    edge = finding.edges[0]
    assert edge.confidence == "partial"
    assert edge.dynamic is True
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"


def test_execute_immediate_line_31_with_empty_catalog_is_opaque():
    statements = load_statements()
    source = load_source()

    findings = depgraph_enrich.dynamic_sql_findings(
        statements, source, catalog_names=[], owner=OWNER, object_name=OBJECT_NAME
    )
    finding = next(f for f in findings if f.line == 31)

    assert finding.level == "opaque"
    assert finding.candidates == []
    assert len(finding.edges) == 1
    edge = finding.edges[0]
    assert edge.confidence == "opaque"
    assert edge.edge_type == "DYNAMIC_SQL"
    assert edge.to_ref == UNKNOWN_TARGET
    assert edge.dynamic is True


def test_no_dynamic_sql_occurrence_from_fixture_is_left_unclassified():
    statements = load_statements()
    source = load_source()

    detected = depgraph_enrich.detect_dynamic_lines(statements, source)
    findings = depgraph_enrich.dynamic_sql_findings(
        statements, source, catalog_names=["FLOW_DEMO_LOG"], owner=OWNER, object_name=OBJECT_NAME
    )

    assert detected == [28, 31]
    assert len(findings) == len(detected)
    assert {f.line for f in findings} == set(detected)
    assert all(f.level in ("resolved", "partial", "opaque") for f in findings)


def test_dynamic_sql_detected_via_source_even_without_plscope_statement():
    # Objeto sem PL/Scope (plano, secao 4.1): plscope_statements vazio, mas
    # o EXECUTE IMMEDIATE ainda esta la no fonte -- tem que aparecer
    # classificado mesmo assim (reforco de deteccao via source).
    lines = [
        "  PROCEDURE demo IS\n",
        "  BEGIN\n",
        "    EXECUTE IMMEDIATE 'SELECT * FROM minha_tabela';\n",
        "  END demo;\n",
    ]
    source = dynsql.rows_from_lines(lines)

    findings = depgraph_enrich.dynamic_sql_findings(
        [], source, catalog_names=["MINHA_TABELA"], owner=OWNER, object_name=OBJECT_NAME
    )

    assert len(findings) == 1
    assert findings[0].line == 3
    assert findings[0].level == "resolved"


def test_dynamic_sql_snippet_window_bounds():
    statements = load_statements()
    source = load_source()

    findings = depgraph_enrich.dynamic_sql_findings(
        statements,
        source,
        catalog_names=["FLOW_DEMO_LOG"],
        owner=OWNER,
        object_name=OBJECT_NAME,
        window=2,
    )
    finding = next(f for f in findings if f.line == 28)
    assert finding.snippet_ref == "L26-L30"
    assert finding.snippet.count("\n") <= 5


# ------------------------------------------------------------------- determinismo


def test_table_edges_from_statements_is_deterministic():
    statements = load_statements()
    source = load_source()

    first = depgraph_enrich.table_edges_from_statements(statements, source, OWNER, OBJECT_NAME)
    second = depgraph_enrich.table_edges_from_statements(statements, source, OWNER, OBJECT_NAME)

    assert first == second


def test_dynamic_sql_findings_is_deterministic():
    statements = load_statements()
    source = load_source()

    first = depgraph_enrich.dynamic_sql_findings(
        statements, source, catalog_names=["FLOW_DEMO_LOG"], owner=OWNER, object_name=OBJECT_NAME
    )
    second = depgraph_enrich.dynamic_sql_findings(
        statements, source, catalog_names=["FLOW_DEMO_LOG"], owner=OWNER, object_name=OBJECT_NAME
    )

    assert first == second
