"""Testes offline (T-05) para o subcomando `python -m plsqlflow depgraph` em
plsqlflow/cli.py -- dispatch de subcomando, retro-compatibilidade com o modo
flow existente, exit codes proprios e ausencia de flag de senha/DSN.

100% sem banco: `db.connect` e `cli.DbDepExtractor` sao monkeypatchados; o
pipeline real (BFS T-02 + enrich T-03 + triggers T-04 + render T-04) roda
sobre um extractor fake em memoria, exercitando o codigo de producao de
`_build_depgraph_result`/`depgraph_main` de ponta a ponta.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import oracledb
import pytest

from plsqlflow import cli, db, extract, queries


def _kw(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k.lower(): v for k, v in row.items()}


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeDepExtractor:
    """Fake do Protocol `depgraph.DepExtractor` + `statements`/`source`
    (extensao usada pelo orquestrador do CLI, ver `DbDepExtractor`)."""

    def __init__(
        self,
        catalog: Dict[str, List[extract.ObjectCatalogRow]],
        deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = None,
        plscope: Dict[str, List[extract.PlscopeCheckRow]] = None,
        triggers: Dict[Tuple[str, Tuple[str, ...]], List[extract.TriggerRow]] = None,
        tab_columns: Dict[str, List[extract.TabColumnRow]] = None,
        statements: Dict[Tuple[str, str], List[extract.PlscopeStatementRow]] = None,
        source: Dict[Tuple[str, str], List[extract.FetchSourceRow]] = None,
        synonyms: Dict[Tuple[str, str], List[extract.SynonymRow]] = None,
    ):
        self.catalog = catalog
        self.deps = deps or {}
        self.plscope = plscope or {}
        self.trigger_rows = triggers or {}
        self.tab_columns_rows = tab_columns or {}
        self.statements_rows = statements or {}
        self.source_rows = source or {}
        self.synonyms = synonyms or {}
        self.object_catalog_calls: List[str] = []
        self.triggers_calls: List[Tuple[str, Tuple[str, ...]]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        return list(self.deps.get((owner.upper(), name.upper()), []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        self.object_catalog_calls.append(owner.upper())
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return list(self.synonyms.get((owner.upper(), name.upper()), []))

    def triggers(self, owner: str, table_names: Sequence[str]) -> List[extract.TriggerRow]:
        key = (owner.upper(), tuple(sorted(n.upper() for n in table_names)))
        self.triggers_calls.append(key)
        return list(self.trigger_rows.get(key, []))

    def tab_columns(self, owner: str, table_names: Sequence[str]) -> List[extract.TabColumnRow]:
        return list(self.tab_columns_rows.get(owner.upper(), []))

    def statements(self, owner: str, object_name: str) -> List[extract.PlscopeStatementRow]:
        return list(self.statements_rows.get((owner.upper(), object_name.upper()), []))

    def source(self, owner: str, object_name: str) -> List[extract.FetchSourceRow]:
        return list(self.source_rows.get((owner.upper(), object_name.upper()), []))


# --------------------------------------------------------------- cenarios fake


def _catalog_ok() -> Dict[str, List[extract.ObjectCatalogRow]]:
    return {
        "GESTAO": [
            extract.ObjectCatalogRow(
                owner="GESTAO", object_name="PKG_A", object_type="PACKAGE",
                status="VALID", last_ddl_time="2026-08-15 10:00:00",
            ),
            extract.ObjectCatalogRow(
                owner="GESTAO", object_name="PKG_A", object_type="PACKAGE BODY",
                status="VALID", last_ddl_time="2026-08-15 10:00:00",
            ),
            extract.ObjectCatalogRow(
                owner="GESTAO", object_name="TAB_A", object_type="TABLE",
                status="VALID", last_ddl_time="2026-08-15 09:00:00",
            ),
        ]
    }


def _make_ok_extractor() -> FakeDepExtractor:
    return FakeDepExtractor(
        catalog=_catalog_ok(),
        deps={
            ("GESTAO", "PKG_A"): [
                extract.DepsDirectRow(
                    referenced_owner="GESTAO", referenced_name="TAB_A",
                    referenced_type="TABLE", dependency_type="HARD",
                ),
            ],
            ("GESTAO", "TAB_A"): [],
        },
        plscope={
            "GESTAO": [
                extract.PlscopeCheckRow(
                    owner="GESTAO", name="PKG_A", type="PACKAGE",
                    plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL",
                ),
                extract.PlscopeCheckRow(
                    owner="GESTAO", name="PKG_A", type="PACKAGE BODY",
                    plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL",
                ),
            ]
        },
        statements={
            ("GESTAO", "PKG_A"): [
                extract.PlscopeStatementRow(
                    line=10, stmt_type="SELECT", sql_id=None,
                    has_into_record="NO", text="SELECT * FROM TAB_A",
                ),
            ]
        },
        source={("GESTAO", "PKG_A"): []},
        tab_columns={
            "GESTAO": [
                extract.TabColumnRow(
                    owner="GESTAO", table_name="TAB_A", column_name="ID",
                    column_id=1, data_type="NUMBER", nullable="N", data_default=None,
                ),
            ]
        },
    )


def _make_needs_recompile_extractor() -> FakeDepExtractor:
    catalog = {
        "GESTAO": [
            extract.ObjectCatalogRow(
                owner="GESTAO", object_name="PKG_B", object_type="PACKAGE",
                status="VALID", last_ddl_time="2026-08-15 10:00:00",
            ),
        ]
    }
    return FakeDepExtractor(catalog=catalog, deps={("GESTAO", "PKG_B"): []}, plscope={"GESTAO": []})


def _patch_pipeline(monkeypatch, extractor: FakeDepExtractor, conn: FakeConn = None) -> FakeConn:
    conn = conn or FakeConn()
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: conn)
    monkeypatch.setattr(cli, "DbDepExtractor", lambda c: extractor)
    return conn


# --------------------------------------------------------- retro-compat / dispatch


def test_dot_target_dispatches_to_flow_mode_not_depgraph(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_flow_main", lambda argv: calls.append(("flow", argv)) or 0)
    monkeypatch.setattr(cli, "depgraph_main", lambda argv: calls.append(("depgraph", argv)) or 0)

    rc = cli.main(["GESTAO.FLOW_DEMO.MAIN", "--conn", "dev"])

    assert rc == 0
    assert calls == [("flow", ["GESTAO.FLOW_DEMO.MAIN", "--conn", "dev"])]


def test_depgraph_subcommand_dispatches_to_depgraph_main(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_flow_main", lambda argv: calls.append(("flow", argv)) or 0)
    monkeypatch.setattr(cli, "depgraph_main", lambda argv: calls.append(("depgraph", argv)) or 0)

    rc = cli.main(["depgraph", "GESTAO.FLOW_DEMO", "--conn", "dev"])

    assert rc == 0
    assert calls == [("depgraph", ["GESTAO.FLOW_DEMO", "--conn", "dev"])]


def test_two_part_target_still_dispatches_to_flow_mode(monkeypatch):
    """owner.objeto (sem subprograma) tambem e alvo do modo flow -- so o
    subcomando `depgraph` literal desvia para o novo caminho."""
    calls = []
    monkeypatch.setattr(cli, "_flow_main", lambda argv: calls.append(("flow", argv)) or 0)
    monkeypatch.setattr(cli, "depgraph_main", lambda argv: calls.append(("depgraph", argv)) or 0)

    cli.main(["GESTAO.FLOW_DEMO"])

    assert calls == [("flow", ["GESTAO.FLOW_DEMO"])]


def test_unknown_subcommand_like_first_arg_is_rejected(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "_flow_main", lambda argv: calls.append(("flow", argv)) or 0)
    monkeypatch.setattr(cli, "depgraph_main", lambda argv: calls.append(("depgraph", argv)) or 0)

    rc = cli.main(["frobnicate"])

    assert rc == 2
    assert calls == []
    err = capsys.readouterr().err
    assert "frobnicate" in err
    assert "depgraph" in err


@pytest.mark.parametrize(
    "argv,expected_subcommand",
    [
        (["depgraph", "GESTAO.FOO"], "depgraph"),
        (["GESTAO.FOO", "--conn", "dev"], None),
        ([], None),
        (["-h"], None),
        (["--json"], None),
    ],
)
def test_dispatch_argv_first_token_without_dot_is_subcommand(argv, expected_subcommand):
    subcommand, _rest = cli._dispatch_argv(argv)
    assert subcommand == expected_subcommand


def test_dispatch_argv_preserves_rest_of_argv():
    subcommand, rest = cli._dispatch_argv(["depgraph", "GESTAO.FOO", "--max-depth", "5"])
    assert subcommand == "depgraph"
    assert rest == ["GESTAO.FOO", "--max-depth", "5"]


# ---------------------------------------------------------------------- flags


def test_depgraph_parser_default_flags():
    args = cli.build_depgraph_arg_parser().parse_args(["GESTAO.PKG_A"])
    assert args.conn is None
    assert args.output == "./oracle-graph" == cli.DEFAULT_OUTPUT_DIR
    assert args.stop_schemas == "SYS,SYSTEM" == cli.DEFAULT_STOP_SCHEMAS_ARG
    assert args.dynamic_window == 30 == cli.DEFAULT_DYNAMIC_WINDOW
    assert args.max_objects == 500 == cli.DEFAULT_MAX_OBJECTS
    assert args.max_depth == 20 == cli.DEFAULT_MAX_DEPTH


def test_depgraph_parser_accepts_all_documented_flags():
    args = cli.build_depgraph_arg_parser().parse_args(
        [
            "GESTAO.PKG_A",
            "--conn", "dev",
            "--output", "C:/tmp/graph",
            "--stop-schemas", "SYS,SYSTEM,APEX_040000",
            "--dynamic-window", "10",
            "--max-objects", "50",
            "--max-depth", "3",
        ]
    )
    assert args.conn == "dev"
    assert args.output == "C:/tmp/graph"
    assert args.stop_schemas == "SYS,SYSTEM,APEX_040000"
    assert args.dynamic_window == 10
    assert args.max_objects == 50
    assert args.max_depth == 3


def test_depgraph_parser_has_no_password_or_dsn_flag():
    """Regra de seguranca do T-05: senha NUNCA por argumento -- so --conn
    (alias) ou env vars. Asserção explícita sobre o parser inteiro."""
    parser = cli.build_depgraph_arg_parser()
    option_strings: List[str] = []
    for action in parser._actions:
        option_strings.extend(action.option_strings)

    lowered = [s.lower() for s in option_strings]
    assert "--conn" in lowered

    forbidden_substrings = ("pwd", "password", "senha", "dsn", "host", "port", "user")
    offending = [s for s in lowered if any(f in s for f in forbidden_substrings)]
    assert offending == [], "flags proibidas no parser do depgraph: {}".format(offending)


# --------------------------------------------------------------------- exit 0


def test_depgraph_success_returns_zero_and_writes_files(monkeypatch, tmp_path, capsys):
    extractor = _make_ok_extractor()
    conn = _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_A", "--output", str(out_root)])

    assert rc == cli.EXIT_OK == 0
    assert conn.closed is True

    out_dir = out_root / "GESTAO.PKG_A"
    assert (out_dir / "INDEX.md").exists()
    assert (out_dir / "edges.jsonl").exists()
    assert (out_dir / "meta.json").exists()
    assert (out_dir / "nodes").is_dir()
    assert not (out_dir / "recompile.sql").exists()

    out = capsys.readouterr().out
    assert "GESTAO.PKG_A" in out
    assert "nos=" in out and "arestas=" in out and "pontos_cegos=" in out


def test_depgraph_success_default_output_dir_is_oracle_graph(monkeypatch, tmp_path, capsys):
    extractor = _make_ok_extractor()
    _patch_pipeline(monkeypatch, extractor)
    monkeypatch.chdir(tmp_path)

    rc = cli.depgraph_main(["GESTAO.PKG_A"])

    assert rc == cli.EXIT_OK
    assert (tmp_path / "oracle-graph" / "GESTAO.PKG_A" / "INDEX.md").exists()


# --------------------------------------------------------------------- exit 3


def test_depgraph_missing_plscope_returns_three_and_still_writes_files(monkeypatch, tmp_path):
    extractor = _make_needs_recompile_extractor()
    _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_B", "--output", str(out_root)])

    assert rc == cli.EXIT_NEEDS_RECOMPILE == 3

    out_dir = out_root / "GESTAO.PKG_B"
    assert (out_dir / "INDEX.md").exists(), "exit 3 nao e falha -- arquivos tem que ser gravados mesmo assim"
    assert (out_dir / "edges.jsonl").exists()
    assert (out_dir / "meta.json").exists()
    assert (out_dir / "recompile.sql").exists()
    recompile_text = (out_dir / "recompile.sql").read_text(encoding="ascii")
    assert "GESTAO.PKG_B" in recompile_text


# --------------------------------------------------------------------- exit 4


def test_depgraph_malformed_target_returns_four_without_connecting(monkeypatch, tmp_path, capsys):
    connect_calls = []
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: connect_calls.append(1) or FakeConn())

    rc = cli.depgraph_main(["SOMENTE_OWNER", "--output", str(tmp_path / "out")])

    assert rc == cli.EXIT_INVALID_ROOT == 4
    assert connect_calls == [], "alvo malformado tem que falhar ANTES de conectar"
    assert "erro" in capsys.readouterr().err.lower()


def test_depgraph_nonexistent_root_returns_four(monkeypatch, tmp_path, capsys):
    extractor = _make_ok_extractor()  # tem PKG_A/TAB_A, nao tem PKG_NAO_EXISTE
    conn = _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_NAO_EXISTE", "--output", str(out_root)])

    assert rc == cli.EXIT_INVALID_ROOT == 4
    assert conn.closed is True
    assert not out_root.exists() or not any(out_root.rglob("INDEX.md"))
    err = capsys.readouterr().err.lower()
    assert "pkg_nao_existe" in err or "não encontrada" in err or "nao encontrada" in err


# --------------------------------------------------------------------- exit 5


def test_depgraph_connection_config_error_returns_five(monkeypatch, capsys):
    def _boom(alias=None, env=None):
        raise db.ConnectionConfigError("alias 'dev' nao encontrado")

    monkeypatch.setattr(db, "connect", _boom)

    rc = cli.depgraph_main(["GESTAO.PKG_A", "--conn", "dev"])

    assert rc == cli.EXIT_CONNECTION_ERROR == 5
    assert "erro" in capsys.readouterr().err.lower()


def test_depgraph_driver_error_returns_five(monkeypatch, capsys):
    def _boom(alias=None, env=None):
        raise oracledb.Error("ORA-12154: nao foi possivel resolver o identificador de conexao")

    monkeypatch.setattr(db, "connect", _boom)

    rc = cli.depgraph_main(["GESTAO.PKG_A"])

    assert rc == cli.EXIT_CONNECTION_ERROR == 5
    assert "erro" in capsys.readouterr().err.lower()


def test_depgraph_driver_error_during_extraction_returns_five(monkeypatch, tmp_path):
    """Erro do driver durante a extracao (nao so no connect() inicial) tambem
    conta como exit 5 -- o pipeline inteiro so faz SELECT sobre a mesma
    conexao."""

    class ExplodingExtractor(FakeDepExtractor):
        def object_catalog(self, owner, object_list=None):
            raise oracledb.Error("ORA-03113: fim de arquivo no canal de comunicacao")

    conn = _patch_pipeline(monkeypatch, ExplodingExtractor(catalog={}))

    rc = cli.depgraph_main(["GESTAO.PKG_A", "--output", str(tmp_path / "out")])

    assert rc == cli.EXIT_CONNECTION_ERROR == 5
    assert conn.closed is True


# --------------------------------------------------------------- DbDepExtractor


def test_db_dep_extractor_triggers_uses_any_status_fetcher(monkeypatch):
    calls = []

    def fake_fetch(conn, owner, table_list):
        calls.append((owner, table_list))
        return []

    monkeypatch.setattr(extract, "fetch_triggers_any_status", fake_fetch)
    extractor = cli.DbDepExtractor(conn=object())

    extractor.triggers("gestao", ["tab_b", "tab_a"])

    assert calls == [("gestao", "TAB_A,TAB_B")]


def test_db_dep_extractor_triggers_empty_table_names_short_circuits(monkeypatch):
    called = []
    monkeypatch.setattr(extract, "fetch_triggers_any_status", lambda *a, **k: called.append(1) or [])

    result = cli.DbDepExtractor(conn=object()).triggers("GESTAO", [])

    assert result == []
    assert called == []


# ----------------------------------------------------- triggers_any_status.sql


def test_triggers_any_status_sql_registered_in_queries():
    assert "triggers_any_status.sql" in queries.QUERY_NAMES
    text = queries.QUERY_TEXT["triggers_any_status.sql"]
    assert text.strip()


def _sql_body_without_comments(text: str) -> str:
    """Remove linhas de comentario (`-- ...`) -- usado para inspecionar so o
    SQL executavel, sem casar texto de comentario explicativo (ex.: a
    docstring de triggers_any_status.sql CITA o filtro da irma, o que faria
    um grep ingenuo no texto INTEIRO dar falso positivo)."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("--")
    ).upper()


def test_triggers_any_status_does_not_filter_status():
    text = queries.QUERY_TEXT["triggers_any_status.sql"]
    body = _sql_body_without_comments(text)
    assert "STATUS = 'ENABLED'" not in body
    assert ":OWNER" in body and ":TABLE_LIST" in body


def test_triggers_for_tables_sister_query_still_filters_enabled():
    """Prova que a lacuna foi corrigida SEM alterar a irma: a query do modo
    flow (graph.py/report.py, congelados por golden test) continua com o
    mesmo filtro de hoje."""
    body = _sql_body_without_comments(queries.QUERY_TEXT["triggers_for_tables.sql"])
    assert "STATUS = 'ENABLED'" in body


def test_extract_fetch_triggers_any_status_exists_and_uses_new_query(monkeypatch):
    captured = {}

    def fake_run_query(conn, sql_text, binds=None):
        captured["sql_text"] = sql_text
        captured["binds"] = binds
        return []

    monkeypatch.setattr(db, "run_query", fake_run_query)
    rows = extract.fetch_triggers_any_status(conn=object(), owner="GESTAO", table_list="TAB_A")

    assert rows == []
    assert captured["binds"] == {"owner": "GESTAO", "table_list": "TAB_A"}
    assert captured["sql_text"] == queries.QUERY_TEXT["triggers_any_status.sql"]
