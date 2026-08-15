"""Testes offline (T-03, contrato oracle-depgraph-scale) para o
enriquecimento em lote (statements/source por owner) orquestrado por
`plsqlflow.cli._build_depgraph_result` e pelos metodos novos de
`cli.DbDepExtractor` (`statements_batch`/`source_batch`).

100% sem banco, sem relogio, sem `random`. Dois fakes do Protocol
`depgraph.DepExtractor` + `statements`/`source` (extensao do CLI, ver
`DbDepExtractor`), alimentados pelo MESMO dado subjacente por objeto:

- `PerObjectExtractor`: so `statements`/`source` -- caminho de hoje, uma
  chamada por objeto PL/SQL (2N chamadas para N objetos).
- `BatchExtractor(PerObjectExtractor)`: acrescenta `statements_batch`/
  `source_batch`, reconstruindo as linhas em lote (com `object_name`/`name`
  extra) na mesma ORDEM que as queries reais entregam (`object_name, line`
  e `name, type, line`) a partir do MESMO dicionario por objeto.

A equivalencia provada aqui e do CODIGO de reagrupamento em
`cli._build_depgraph_result` (BatchRow -> Row por owner, depois por
objeto) -- nao do dado de entrada, que seria trivial se cada fake tivesse
seu proprio dado independente.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from plsqlflow import cli, depgraph, dynsql, extract

OWNER = "GESTAO"


# --------------------------------------------------------------- fixture

# PKG_ROOT: SELECT em TAB_A (READ) + dois EXECUTE IMMEDIATE em linhas
# diferentes (5 e 15) -- o caso do bug real de um contrato anterior: a
# chave de dedup das arestas precisa incluir `line`, senao os dois viram
# uma aresta DYNAMIC_SQL so.
_PKG_ROOT_LINES = [
    "PACKAGE BODY pkg_root IS\n",              # 1
    "  PROCEDURE p IS\n",                       # 2
    "  BEGIN\n",                                 # 3
    "    NULL;\n",                               # 4
    "    EXECUTE IMMEDIATE 'DROP TABLE TMP_X';\n",  # 5
    "    NULL;\n",                               # 6
    "    NULL;\n",                               # 7
    "    NULL;\n",                               # 8
    "    NULL;\n",                               # 9
    "    SELECT * FROM TAB_A;\n",                # 10
    "    NULL;\n",                               # 11
    "    NULL;\n",                               # 12
    "    NULL;\n",                               # 13
    "    NULL;\n",                               # 14
    "    EXECUTE IMMEDIATE 'DROP TABLE TMP_Y';\n",  # 15
    "    NULL;\n",                               # 16
    "    NULL;\n",                               # 17
    "    NULL;\n",                               # 18
    "    NULL;\n",                               # 19
    "  END p;\n",                                # 20
]

_PKG_B_LINES = [
    "PACKAGE BODY pkg_b IS\n",                          # 1
    "  PROCEDURE q IS\n",                                # 2
    "  BEGIN\n",                                          # 3
    "    INSERT INTO TAB_B (COL1) VALUES (1);\n",        # 4
    "  END q;\n",                                         # 5
]


def _catalog() -> Dict[str, List[extract.ObjectCatalogRow]]:
    names_packages = ["PKG_ROOT", "PKG_B", "PKG_C", "PKG_D", "PKG_E", "PKG_NOPLSCOPE"]
    rows = [
        extract.ObjectCatalogRow(
            owner=OWNER, object_name=n, object_type="PACKAGE",
            status="VALID", last_ddl_time="2026-08-15 10:00:00",
        )
        for n in names_packages
    ]
    rows += [
        extract.ObjectCatalogRow(
            owner=OWNER, object_name="TAB_A", object_type="TABLE",
            status="VALID", last_ddl_time="2026-08-15 09:00:00",
        ),
        extract.ObjectCatalogRow(
            owner=OWNER, object_name="TAB_B", object_type="TABLE",
            status="VALID", last_ddl_time="2026-08-15 09:00:00",
        ),
    ]
    return {OWNER: rows}


def _deps() -> Dict[Tuple[str, str], List[extract.DepsDirectRow]]:
    def row(name: str, type_: str) -> extract.DepsDirectRow:
        return extract.DepsDirectRow(
            referenced_owner=OWNER, referenced_name=name, referenced_type=type_, dependency_type="HARD"
        )

    return {
        (OWNER, "PKG_ROOT"): [
            row("TAB_A", "TABLE"),
            row("PKG_B", "PACKAGE"),
            row("PKG_C", "PACKAGE"),
            row("PKG_D", "PACKAGE"),
            row("PKG_E", "PACKAGE"),
            row("PKG_NOPLSCOPE", "PACKAGE"),
        ],
        (OWNER, "TAB_A"): [],
        (OWNER, "PKG_B"): [row("TAB_B", "TABLE")],
        (OWNER, "TAB_B"): [],
        (OWNER, "PKG_C"): [],
        (OWNER, "PKG_D"): [],
        (OWNER, "PKG_E"): [],
        (OWNER, "PKG_NOPLSCOPE"): [],
    }


def _plscope() -> Dict[str, List[extract.PlscopeCheckRow]]:
    # PKG_NOPLSCOPE tem linha de plscope_check, mas SEM IDENTIFIERS:ALL --
    # objeto sem PL/Scope disponivel (nao pode entrar no lote).
    with_plscope = ["PKG_ROOT", "PKG_B", "PKG_C", "PKG_D", "PKG_E"]
    rows = [
        extract.PlscopeCheckRow(
            owner=OWNER, name=n, type="PACKAGE", plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL"
        )
        for n in with_plscope
    ]
    rows.append(
        extract.PlscopeCheckRow(owner=OWNER, name="PKG_NOPLSCOPE", type="PACKAGE", plscope_settings=None)
    )
    return {OWNER: rows}


def _statements() -> Dict[Tuple[str, str], List[extract.PlscopeStatementRow]]:
    return {
        (OWNER, "PKG_ROOT"): [
            # Ordem de entrada deliberadamente fora de ordem de linha --
            # table_edges_from_statements/dynamic_sql_findings ordenam
            # internamente, entao isso nao pode fazer diferenca no
            # resultado final (mas exercita o reagrupamento sem depender
            # de a fonte ja vir ordenada).
            extract.PlscopeStatementRow(
                line=15, stmt_type="EXECUTE IMMEDIATE", sql_id=None, has_into_record="NO", text=None
            ),
            extract.PlscopeStatementRow(
                line=5, stmt_type="EXECUTE IMMEDIATE", sql_id=None, has_into_record="NO", text=None
            ),
            extract.PlscopeStatementRow(
                line=10, stmt_type="SELECT", sql_id="sel001", has_into_record="NO",
                text="SELECT * FROM TAB_A",
            ),
        ],
        (OWNER, "PKG_B"): [
            extract.PlscopeStatementRow(
                line=4, stmt_type="INSERT", sql_id="ins001", has_into_record="NO",
                text="INSERT INTO TAB_B (COL1) VALUES (1)",
            ),
        ],
        (OWNER, "PKG_C"): [],
        (OWNER, "PKG_D"): [],
        (OWNER, "PKG_E"): [],
    }


def _source() -> Dict[Tuple[str, str], List[extract.FetchSourceRow]]:
    return {
        (OWNER, "PKG_ROOT"): dynsql.rows_from_lines(_PKG_ROOT_LINES, "PACKAGE BODY"),
        (OWNER, "PKG_B"): dynsql.rows_from_lines(_PKG_B_LINES, "PACKAGE BODY"),
        (OWNER, "PKG_C"): [],
        (OWNER, "PKG_D"): [],
        (OWNER, "PKG_E"): [],
    }


# ELIGIVEIS (tipo PL/SQL + plscope True): PKG_ROOT, PKG_B, PKG_C, PKG_D,
# PKG_E -- N = 5. PKG_NOPLSCOPE fica de fora (vai para needs_recompile).
ELIGIBLE_COUNT = 5


# --------------------------------------------------------------- fakes


class PerObjectExtractor:
    """Fake do Protocol `DepExtractor` + `statements`/`source` -- caminho
    de hoje (sem os metodos em lote). Conta chamadas de `statements`/
    `source` para provar 2N round-trips."""

    def __init__(self):
        self.catalog = _catalog()
        self.deps = _deps()
        self.plscope = _plscope()
        self.statements_rows = _statements()
        self.source_rows = _source()
        self.trigger_rows: Dict[Tuple[str, Tuple[str, ...]], List[extract.TriggerRow]] = {}
        self.tab_columns_rows: Dict[str, List[extract.TabColumnRow]] = {}
        self.synonyms: Dict[Tuple[str, str], List[extract.SynonymRow]] = {}
        self.statements_calls: List[Tuple[str, str]] = []
        self.source_calls: List[Tuple[str, str]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        return list(self.deps.get((owner.upper(), name.upper()), []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return list(self.synonyms.get((owner.upper(), name.upper()), []))

    def triggers(self, owner: str, table_names: Sequence[str]) -> List[extract.TriggerRow]:
        key = (owner.upper(), tuple(sorted(n.upper() for n in table_names)))
        return list(self.trigger_rows.get(key, []))

    def tab_columns(self, owner: str, table_names: Sequence[str]) -> List[extract.TabColumnRow]:
        return list(self.tab_columns_rows.get(owner.upper(), []))

    def statements(self, owner: str, object_name: str) -> List[extract.PlscopeStatementRow]:
        self.statements_calls.append((owner.upper(), object_name.upper()))
        return list(self.statements_rows.get((owner.upper(), object_name.upper()), []))

    def source(self, owner: str, object_name: str) -> List[extract.FetchSourceRow]:
        self.source_calls.append((owner.upper(), object_name.upper()))
        return list(self.source_rows.get((owner.upper(), object_name.upper()), []))


class BatchExtractor(PerObjectExtractor):
    """Mesmo dado de `PerObjectExtractor`, mas expondo `statements_batch`/
    `source_batch` -- simula o fetch em lote real, reconstruindo as linhas
    com o campo extra de nome e na ORDEM que as queries em lote entregam
    (object_name/line e name/type/line)."""

    def __init__(self):
        super().__init__()
        self.statements_batch_calls: List[Tuple[str, Tuple[str, ...]]] = []
        self.source_batch_calls: List[Tuple[str, Tuple[str, ...]]] = []

    def statements_batch(
        self, owner: str, object_names: Sequence[str]
    ) -> List[extract.PlscopeStatementBatchRow]:
        names = tuple(sorted(n.upper() for n in object_names))
        self.statements_batch_calls.append((owner.upper(), names))
        rows: List[extract.PlscopeStatementBatchRow] = []
        for name in names:
            stmts = sorted(
                self.statements_rows.get((owner.upper(), name), []), key=lambda s: s.line
            )
            for stmt in stmts:
                rows.append(
                    extract.PlscopeStatementBatchRow(
                        object_name=name,
                        line=stmt.line,
                        stmt_type=stmt.stmt_type,
                        sql_id=stmt.sql_id,
                        has_into_record=stmt.has_into_record,
                        text=stmt.text,
                    )
                )
        return rows

    def source_batch(self, owner: str, object_names: Sequence[str]) -> List[extract.FetchSourceBatchRow]:
        names = tuple(sorted(n.upper() for n in object_names))
        self.source_batch_calls.append((owner.upper(), names))
        rows: List[extract.FetchSourceBatchRow] = []
        for name in names:
            srcs = sorted(
                self.source_rows.get((owner.upper(), name), []), key=lambda s: (s.type, s.line)
            )
            for src in srcs:
                rows.append(
                    extract.FetchSourceBatchRow(name=name, type=src.type, line=src.line, text=src.text)
                )
        return rows


def _run(extractor) -> depgraph.DepGraphResult:
    return cli._build_depgraph_result(
        extractor,
        OWNER,
        "PKG_ROOT",
        stop_schemas=["SYS", "SYSTEM"],
        max_objects=500,
        max_depth=20,
        dynamic_window=5,
    )


# --------------------------------------------------------------- equivalencia


def test_batch_and_per_object_paths_produce_identical_depgraph_result():
    batch_result = _run(BatchExtractor())
    per_object_result = _run(PerObjectExtractor())

    assert batch_result.nodes == per_object_result.nodes
    assert batch_result.edges == per_object_result.edges
    assert batch_result.needs_recompile == per_object_result.needs_recompile
    assert batch_result.truncated == per_object_result.truncated
    assert batch_result.stats == per_object_result.stats
    assert batch_result.snippets == per_object_result.snippets
    # Equivalencia total do dataclass, nao so campo a campo.
    assert batch_result == per_object_result


def test_needs_recompile_includes_object_without_plscope_in_both_paths():
    batch_result = _run(BatchExtractor())
    per_object_result = _run(PerObjectExtractor())

    assert "GESTAO.PKG_NOPLSCOPE" in batch_result.needs_recompile
    assert "GESTAO.PKG_NOPLSCOPE" in per_object_result.needs_recompile


# --------------------------------------------------------------- contagem de chamadas


def test_per_object_path_makes_two_calls_per_eligible_object():
    extractor = PerObjectExtractor()
    _run(extractor)

    # N objetos elegiveis (tipo PL/SQL + plscope) -> N chamadas de
    # statements() + N de source() = 2N, nunca menos.
    assert len(extractor.statements_calls) == ELIGIBLE_COUNT
    assert len(extractor.source_calls) == ELIGIBLE_COUNT

    called_names = {name for _owner, name in extractor.statements_calls}
    assert called_names == {"PKG_ROOT", "PKG_B", "PKG_C", "PKG_D", "PKG_E"}


def test_batch_path_call_count_independent_of_object_count():
    extractor = BatchExtractor()
    _run(extractor)

    # Todos os elegiveis estao no MESMO owner (GESTAO) -- uma unica
    # chamada de statements_batch/source_batch resolve os N objetos, nao
    # cresce com N (compare com o teste acima, 2N=10 no caminho antigo).
    assert len(extractor.statements_batch_calls) == 1
    assert len(extractor.source_batch_calls) == 1
    assert extractor.statements_calls == []
    assert extractor.source_calls == []

    owner, names = extractor.statements_batch_calls[0]
    assert owner == "GESTAO"
    assert set(names) == {"PKG_ROOT", "PKG_B", "PKG_C", "PKG_D", "PKG_E"}


def test_object_without_plscope_never_enters_the_batch_request():
    extractor = BatchExtractor()
    _run(extractor)

    _owner, names = extractor.statements_batch_calls[0]
    assert "PKG_NOPLSCOPE" not in names
    _owner2, source_names = extractor.source_batch_calls[0]
    assert "PKG_NOPLSCOPE" not in source_names


# --------------------------------------------------------------- SQL dinamico


def test_two_execute_immediate_on_different_lines_produce_two_dynamic_edges():
    # Bug real de um contrato anterior: a chave de dedup das arestas
    # precisa incluir `line`, senao os dois EXECUTE IMMEDIATE virariam uma
    # unica aresta DYNAMIC_SQL.
    batch_result = _run(BatchExtractor())

    dynamic_edges = [
        e for e in batch_result.edges if e.edge_type == "DYNAMIC_SQL" and e.from_ref == "GESTAO.PKG_ROOT"
    ]
    assert len(dynamic_edges) == 2
    assert sorted(e.line for e in dynamic_edges) == [5, 15]


def test_dynamic_sql_snippets_match_between_batch_and_per_object_paths():
    batch_result = _run(BatchExtractor())
    per_object_result = _run(PerObjectExtractor())

    dynamic_refs = {
        e.snippet_ref
        for e in batch_result.edges
        if e.edge_type == "DYNAMIC_SQL" and e.from_ref == "GESTAO.PKG_ROOT"
    }
    assert len(dynamic_refs) == 2
    for ref in dynamic_refs:
        assert batch_result.snippets[ref] == per_object_result.snippets[ref]
        assert "EXECUTE IMMEDIATE" in batch_result.snippets[ref]


# --------------------------------------------------------------- chunking (DbDepExtractor)


def test_db_dep_extractor_statements_batch_chunks_above_batch_chunk_size(monkeypatch):
    calls: List[str] = []

    def fake_fetch(conn, owner, object_list):
        calls.append(object_list)
        names = object_list.split(",")
        return [
            extract.PlscopeStatementBatchRow(
                object_name=name, line=1, stmt_type="SELECT", sql_id=None,
                has_into_record="NO", text=None,
            )
            for name in names
        ]

    monkeypatch.setattr(extract, "fetch_plscope_statements_batch", fake_fetch)

    names = ["PKG_{:04d}".format(i) for i in range(250)]
    extractor = cli.DbDepExtractor(conn=object())
    rows = extractor.statements_batch("GESTAO", names)

    expected_chunks = extract.chunk_names(names)
    assert len(expected_chunks) == 2  # 250 nomes / 200 por chunk
    assert calls == expected_chunks
    assert len(rows) == 250
    assert {r.object_name for r in rows} == {n.upper() for n in names}


def test_db_dep_extractor_source_batch_chunks_above_batch_chunk_size(monkeypatch):
    calls: List[str] = []

    def fake_fetch(conn, owner, object_list, object_type=None):
        calls.append(object_list)
        names = object_list.split(",")
        return [
            extract.FetchSourceBatchRow(name=name, type="PACKAGE BODY", line=1, text="NULL;\n")
            for name in names
        ]

    monkeypatch.setattr(extract, "fetch_source_batch", fake_fetch)

    names = ["PKG_{:04d}".format(i) for i in range(250)]
    extractor = cli.DbDepExtractor(conn=object())
    rows = extractor.source_batch("GESTAO", names)

    expected_chunks = extract.chunk_names(names)
    assert len(expected_chunks) == 2
    assert calls == expected_chunks
    assert len(rows) == 250
    assert {r.name for r in rows} == {n.upper() for n in names}


def test_db_dep_extractor_statements_batch_empty_names_makes_no_call(monkeypatch):
    called = []
    monkeypatch.setattr(
        extract, "fetch_plscope_statements_batch", lambda *a, **k: called.append(1) or []
    )

    extractor = cli.DbDepExtractor(conn=object())
    rows = extractor.statements_batch("GESTAO", [])

    assert rows == []
    assert called == []
