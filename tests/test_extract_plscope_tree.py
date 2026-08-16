"""Testes offline (T-02, contrato depgraph-granular) para
sql/flow/plscope_tree_batch.sql (novo) e para as colunas novas de
sql/flow/plscope_statements_batch.sql (usage_id, usage_context_id,
object_type), mais os fetchers/dataclasses correspondentes em
plsqlflow/extract.py.

Nao abre conexao real e nao depende de rede/banco -- mesmo padrao de cursor
mockado de tests/test_depgraph_batch_extract.py (FakeConnection/FakeCursor
em memoria, sem fixture em disco).

Os dados de fixture reproduzem os numeros reais extraidos do banco dev
(GESTAO.FLOW_DEMO, PACKAGE BODY) documentados na secao 2 de
docs/plano-depgraph-granular.md: ALL_IDENTIFIERS comeca em
(usage_id=1, ctx=0, line=1, col=14, name=FLOW_DEMO, type=PACKAGE,
usage=DEFINITION); ALL_STATEMENTS tem usage_id=6 (ctx=3, line=6, INSERT).
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any, Dict, List

from plsqlflow import db, extract, queries

REPO = Path(__file__).resolve().parent.parent
SQL_FLOW_DIR = REPO / "sql" / "flow"

TREE_SQL_NAME = "plscope_tree_batch.sql"
STATEMENTS_SQL_NAME = "plscope_statements_batch.sql"


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
#
# Duas linhas de PACKAGE BODY (raiz DEFINITION + uma CALL), mais uma linha
# de PACKAGE (mesmo usage_id=1 da raiz do body, object_type diferente) --
# prova de que object_type e o que distingue as duas arvores que
# compartilham o mesmo espaco de usage_id (ver docstring do .sql).

PLSCOPE_TREE_BATCH_ROWS = [
    {
        "OBJECT_NAME": "FLOW_DEMO",
        "OBJECT_TYPE": "PACKAGE",
        "USAGE_ID": 1,
        "USAGE_CONTEXT_ID": 0,
        "LINE": 1,
        "COL": 14,
        "NAME": "FLOW_DEMO",
        "TYPE": "PACKAGE",
        "USAGE": "DEFINITION",
        "SIGNATURE": "11ad5598aaaa",
    },
    {
        "OBJECT_NAME": "FLOW_DEMO",
        "OBJECT_TYPE": "PACKAGE BODY",
        "USAGE_ID": 1,
        "USAGE_CONTEXT_ID": 0,
        "LINE": 1,
        "COL": 14,
        "NAME": "FLOW_DEMO",
        "TYPE": "PACKAGE",
        "USAGE": "DEFINITION",
        "SIGNATURE": "11ad5598aaaa",
    },
    {
        "OBJECT_NAME": "FLOW_DEMO",
        "OBJECT_TYPE": "PACKAGE BODY",
        "USAGE_ID": 13,
        "USAGE_CONTEXT_ID": 11,
        "LINE": 11,
        "COL": 5,
        "NAME": "LOG_MSG",
        "TYPE": "PROCEDURE",
        "USAGE": "CALL",
        "SIGNATURE": "22bd5598bbbb",
    },
]

PLSCOPE_STATEMENTS_BATCH_ROWS = [
    {
        "OBJECT_NAME": "FLOW_DEMO",
        "LINE": 6,
        "STMT_TYPE": "INSERT",
        "SQL_ID": "5rhw9u2hz7b12",
        "HAS_INTO_RECORD": "NO",
        "TEXT": "INSERT INTO flow_demo_log (msg) VALUES ('x')",
        "OBJECT_TYPE": "PACKAGE BODY",
        "USAGE_ID": 6,
        "USAGE_CONTEXT_ID": 3,
    },
    {
        "OBJECT_NAME": "FLOW_DEMO",
        "LINE": 28,
        "STMT_TYPE": "EXECUTE IMMEDIATE",
        "SQL_ID": None,
        "HAS_INTO_RECORD": "NO",
        "TEXT": None,
        "OBJECT_TYPE": "PACKAGE BODY",
        "USAGE_ID": 30,
        "USAGE_CONTEXT_ID": 25,
    },
]


# --------------------------------------------------- (a) plscope_tree_batch.sql no disco


def test_plscope_tree_batch_sql_exists_on_disk():
    assert (SQL_FLOW_DIR / TREE_SQL_NAME).exists()


def test_plscope_tree_batch_sql_loads_non_empty():
    text = (SQL_FLOW_DIR / TREE_SQL_NAME).read_text(encoding="utf-8")
    assert text.strip()


def test_plscope_tree_batch_documents_owner_and_object_list_binds():
    text = (SQL_FLOW_DIR / TREE_SQL_NAME).read_text(encoding="utf-8")
    assert ":owner" in text
    assert ":object_list" in text


def test_plscope_tree_batch_references_all_identifiers():
    text = (SQL_FLOW_DIR / TREE_SQL_NAME).read_text(encoding="utf-8").lower()
    assert "all_identifiers" in text
    assert "all_dba_identifiers" not in text  # nunca DBA_


def test_plscope_tree_batch_object_list_uses_instr_pattern_like_siblings():
    # Mesmo padrao de bind :object_list + INSTR de plscope_statements_batch.sql
    # / tab_columns.sql -- exigido pelo enunciado da tarefa.
    tree_text = (SQL_FLOW_DIR / TREE_SQL_NAME).read_text(encoding="utf-8")
    sibling_text = (SQL_FLOW_DIR / STATEMENTS_SQL_NAME).read_text(encoding="utf-8")
    assert "INSTR(" in tree_text
    assert "INSTR(" in sibling_text


def test_plscope_tree_batch_order_by_is_deterministic():
    text = (SQL_FLOW_DIR / TREE_SQL_NAME).read_text(encoding="utf-8").lower()
    order_by = text.split("order")[-1]
    assert "object_name" in order_by
    assert "object_type" in order_by
    assert "usage_id" in order_by


def test_plscope_tree_batch_registered_in_central_registry():
    assert TREE_SQL_NAME in queries.QUERY_NAMES
    on_disk = (SQL_FLOW_DIR / TREE_SQL_NAME).read_text(encoding="utf-8")
    assert queries.QUERY_TEXT[TREE_SQL_NAME] == on_disk


# --------------------------------------------------- (b) ordem de colunas
#
# Regressao de reordenacao de coluna: a projecao do .sql precisa trazer as
# colunas na MESMA ordem que o dataclass consome (**kwargs e por nome, entao
# isto nao quebra em runtime se alguem reordenar -- mas um teste barato aqui
# pega a regressao de qualquer jeito, como pedido no enunciado da tarefa).


def _select_column_aliases(sql_text: str) -> List[str]:
    """Extrai os nomes/alias das colunas de um SELECT simples de
    sql/flow/*.sql (sem subquery, sem funcao com virgula interna) -- o
    suficiente para as duas queries deste contrato."""
    match = re.search(r"SELECT\s+(.*?)\s+FROM\s", sql_text, re.IGNORECASE | re.DOTALL)
    assert match, "SELECT ... FROM nao encontrado no arquivo"
    projection = match.group(1)
    columns = [c.strip() for c in projection.split(",")]
    aliases = []
    for col in columns:
        col = re.sub(r"\s+", " ", col).strip()
        if " AS " in col.upper():
            alias = re.split(r"\s+AS\s+", col, flags=re.IGNORECASE)[-1]
        else:
            alias = col.split()[-1] if " " in col else col
        alias = alias.split(".")[-1].strip()
        aliases.append(alias.lower())
    return aliases


def test_plscope_tree_batch_projection_matches_dataclass_field_order():
    text = (SQL_FLOW_DIR / TREE_SQL_NAME).read_text(encoding="utf-8")
    aliases = _select_column_aliases(text)
    field_names = [f.name for f in dataclasses.fields(extract.PlscopeTreeRow)]
    assert aliases == field_names


def test_plscope_statements_batch_projection_matches_dataclass_field_order():
    text = (SQL_FLOW_DIR / STATEMENTS_SQL_NAME).read_text(encoding="utf-8")
    aliases = _select_column_aliases(text)
    field_names = [f.name for f in dataclasses.fields(extract.PlscopeStatementBatchRow)]
    assert aliases == field_names


def test_plscope_statements_batch_keeps_all_original_columns():
    # NAO remover nenhuma coluna atual (object_name, line, stmt_type,
    # sql_id, has_into_record, text) -- outros consumidores dependem delas.
    text = (SQL_FLOW_DIR / STATEMENTS_SQL_NAME).read_text(encoding="utf-8")
    aliases = _select_column_aliases(text)
    for original in ("object_name", "line", "stmt_type", "sql_id", "has_into_record", "text"):
        assert original in aliases


def test_plscope_statements_batch_gained_the_three_new_columns():
    text = (SQL_FLOW_DIR / STATEMENTS_SQL_NAME).read_text(encoding="utf-8")
    aliases = _select_column_aliases(text)
    for new_col in ("usage_id", "usage_context_id", "object_type"):
        assert new_col in aliases


# --------------------------------------------------- (c) fetch_plscope_tree_batch


def test_fetch_plscope_tree_batch_binds_and_maps_dataclass():
    conn = FakeConnection(PLSCOPE_TREE_BATCH_ROWS)
    rows = extract.fetch_plscope_tree_batch(conn, "GESTAO", "FLOW_DEMO")

    assert len(rows) == 3
    assert all(isinstance(r, extract.PlscopeTreeRow) for r in rows)

    package_root = rows[0]
    assert package_root.object_name == "FLOW_DEMO"
    assert package_root.object_type == "PACKAGE"
    assert package_root.usage_id == 1
    assert package_root.usage_context_id == 0
    assert package_root.line == 1
    assert package_root.col == 14
    assert package_root.name == "FLOW_DEMO"
    assert package_root.type == "PACKAGE"
    assert package_root.usage == "DEFINITION"
    assert package_root.signature == "11ad5598aaaa"

    body_root = rows[1]
    assert body_root.object_type == "PACKAGE BODY"
    assert body_root.usage_id == 1  # mesmo usage_id do spec -- arvore SEPARADA

    call_row = rows[2]
    assert call_row.usage_id == 13
    assert call_row.usage_context_id == 11
    assert call_row.usage == "CALL"

    sql, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "object_list": "FLOW_DEMO"}
    assert sql == queries.QUERY_TEXT[TREE_SQL_NAME].rstrip().rstrip(";")


def test_fetch_plscope_tree_batch_object_list_optional():
    conn = FakeConnection([])
    extract.fetch_plscope_tree_batch(conn, "GESTAO", None)
    _, binds = conn.last_cursor.executed
    assert binds == {"owner": "GESTAO", "object_list": None}


def test_fetch_plscope_tree_batch_run_query_only_select_guard():
    conn = FakeConnection([])
    db.run_query(
        conn,
        queries.QUERY_TEXT[TREE_SQL_NAME],
        {"owner": "GESTAO", "object_list": None},
    )
    assert conn.last_cursor is not None
    assert conn.last_cursor.closed is True


def test_fetch_plscope_tree_batch_chunks_above_batch_chunk_size(monkeypatch):
    # Chunking por chunk_names (contrato depgraph-granular, T-02): a funcao
    # de fetch em si nao fatia -- e o CHAMADOR (procgraph.py, T-03) que
    # agrega chunk_names + fetch_plscope_tree_batch, mesmo padrao ja usado
    # por statements_batch/source_batch em cli.py. Prova aqui que compoe
    # certo: 250 nomes / 200 por chunk = 2 chamadas, 250 linhas no total.
    calls: List[str] = []

    def fake_run_query(conn, sql_text, binds):
        calls.append(binds["object_list"])
        names = binds["object_list"].split(",")
        return [
            {
                "OBJECT_NAME": name,
                "OBJECT_TYPE": "PACKAGE BODY",
                "USAGE_ID": 1,
                "USAGE_CONTEXT_ID": 0,
                "LINE": 1,
                "COL": 1,
                "NAME": name,
                "TYPE": "PACKAGE",
                "USAGE": "DEFINITION",
                "SIGNATURE": None,
            }
            for name in names
        ]

    monkeypatch.setattr(db, "run_query", fake_run_query)

    names = ["PKG_{:04d}".format(i) for i in range(250)]
    expected_chunks = extract.chunk_names(names)
    assert len(expected_chunks) == 2  # 250 nomes / 200 por chunk

    rows: List[extract.PlscopeTreeRow] = []
    for chunk in expected_chunks:
        rows.extend(extract.fetch_plscope_tree_batch(object(), "GESTAO", chunk))

    assert calls == expected_chunks
    assert len(rows) == 250
    assert {r.object_name for r in rows} == {n.upper() for n in names}


# --------------------------------------------------- (d) colunas novas de PlscopeStatementBatchRow


def test_fetch_plscope_statements_batch_maps_new_columns():
    conn = FakeConnection(PLSCOPE_STATEMENTS_BATCH_ROWS)
    rows = extract.fetch_plscope_statements_batch(conn, "GESTAO", "FLOW_DEMO")

    assert len(rows) == 2
    insert_row = rows[0]
    assert insert_row.object_type == "PACKAGE BODY"
    assert insert_row.usage_id == 6
    assert insert_row.usage_context_id == 3
    # colunas antigas continuam presentes e inalteradas
    assert insert_row.object_name == "FLOW_DEMO"
    assert insert_row.stmt_type == "INSERT"
    assert insert_row.sql_id == "5rhw9u2hz7b12"

    dynamic_row = rows[1]
    assert dynamic_row.usage_id == 30
    assert dynamic_row.usage_context_id == 25


def test_plscope_statement_batch_row_still_constructs_with_only_original_fields():
    # NAO quebrar consumidor existente: cli.py e tests/test_depgraph_enrich_batch.py
    # constroem PlscopeStatementBatchRow so com os 6 campos originais, por
    # keyword. Os 3 campos novos tem default None -- isto tem que continuar
    # funcionando sem alteracao nesses chamadores.
    row = extract.PlscopeStatementBatchRow(
        object_name="PKG_A",
        line=6,
        stmt_type="INSERT",
        sql_id="5rhw9u2hz7b12",
        has_into_record="NO",
        text="INSERT INTO flow_demo_log (msg) VALUES ('x')",
    )
    assert row.object_type is None
    assert row.usage_id is None
    assert row.usage_context_id is None


def test_plscope_statement_batch_row_field_order_keeps_original_six_first():
    # NUNCA reordenar campos existentes de forma que quebre construcao
    # posicional -- prova direta da ordem dos 6 primeiros campos.
    field_names = [f.name for f in dataclasses.fields(extract.PlscopeStatementBatchRow)]
    assert field_names[:6] == [
        "object_name",
        "line",
        "stmt_type",
        "sql_id",
        "has_into_record",
        "text",
    ]
