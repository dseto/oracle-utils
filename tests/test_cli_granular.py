"""Testes offline (T-08, contrato depgraph-granular) para o subcomando
`python -m plsqlflow depgraph --granular` em plsqlflow/cli.py.

100% sem banco: monkeypatcha `db.connect`, `cli.DbDepExtractor` e
`cli.ProcDbExtractor`. O motor real (`procgraph.build_proc_graph` +
`procgraph_render.render_graph`) roda sobre a fixture ja usada por
tests/test_procgraph_bfs.py (GESTAO.FLOW_DEMO real + PKG_CH_A/B/C +
PKG_API) e sobre os fakes de T-04 (tests/test_procgraph_fallback.py) para o
caminho needs_recompile/exit 3.

O criterio CENTRAL deste arquivo (plano, secao 5; Plans.md [T-08]) e
NAO-REGRESSAO: sem `--granular`, o subcomando `depgraph` tem que continuar
IDENTICO -- parsing, comportamento e saida -- ao que
tests/test_depgraph_cli.py ja prova (suite intocada por esta tarefa).

Como essa nao-regressao e provada, em duas camadas:
1. tests/test_depgraph_cli.py continua verde e intocado -- ele e a golden do
   caminho de grao objeto, e nenhuma linha dele foi alterada por T-08.
2. `test_object_mode_never_reaches_the_granular_code_path` abaixo fecha a
   unica brecha que a camada 1 nao cobre. Uma suite verde prova que a saida
   continua CERTA, mas nao prova que ela continua vindo do MESMO codigo --
   uma implementacao granular que por acaso produzisse o mesmo resultado
   passaria despercebida. O teste sabota os dois pontos de entrada do motor
   novo (`procgraph.build_proc_graph` e `procgraph_render.render_graph`) para
   estourar se forem chamados, e entao roda o modo objeto: se ele terminar
   com exito, o caminho novo e comprovadamente INALCANCAVEL sem a flag, que e
   a forma mais forte de "byte a byte identico" -- o codigo antigo nao foi so
   preservado, ele continua sendo o unico executado.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import oracledb

from plsqlflow import cli, db, extract
from tests.test_procgraph_bfs import FakeExtractor, _load_fixture
from tests.test_procgraph_fallback import FakeDepExtractor as FallbackDepExtractor
from tests.test_procgraph_fallback import FakeProcExtractor as FallbackProcExtractor


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeRootCheckExtractor:
    """So o suficiente do Protocol `depgraph.DepExtractor` para
    `_missing_roots`/`object_catalog` -- o fallback de grao objeto (T-04)
    ja tem prova dedicada em tests/test_procgraph_fallback.py; estes testes
    de CLI usam raizes que SEMPRE tem PL/Scope (nunca disparam o fallback),
    entao os outros metodos do Protocol nunca sao chamados de verdade."""

    def __init__(self, catalog: Dict[str, List[extract.ObjectCatalogRow]]):
        self.catalog = catalog

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return list(self.catalog.get(owner.upper(), []))

    def deps_direct(self, owner, name):
        return []

    def plscope_check(self, owner):
        return []

    def synonym(self, owner, name):
        return []

    def triggers(self, owner, table_names):
        return []

    def tab_columns(self, owner, table_names):
        return []


def _catalog(*names: str, owner: str = "GESTAO") -> Dict[str, List[extract.ObjectCatalogRow]]:
    return {
        owner: [
            extract.ObjectCatalogRow(
                owner=owner, object_name=name, object_type="PACKAGE BODY",
                status="VALID", last_ddl_time="2026-08-15 10:00:00",
            )
            for name in names
        ]
    }


def _patch_granular_pipeline(monkeypatch, proc_extractor, dep_extractor, conn: Optional[FakeConn] = None) -> FakeConn:
    conn = conn or FakeConn()
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: conn)
    monkeypatch.setattr(cli, "DbDepExtractor", lambda c: dep_extractor)
    monkeypatch.setattr(cli, "ProcDbExtractor", lambda c, root_owners=(): proc_extractor)
    return conn


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------


def test_granular_flag_defaults_to_false():
    args = cli.build_depgraph_arg_parser().parse_args(["GESTAO.PKG_A"])
    assert args.granular is False


def test_granular_flag_accepts_three_part_target_at_parser_level():
    # O PARSER nao valida a forma do alvo (isso e feito depois, por
    # `_parse_depgraph_target`/`_parse_depgraph_target_granular`) -- so
    # confirma que a flag existe e liga.
    args = cli.build_depgraph_arg_parser().parse_args(["GESTAO.FLOW_DEMO.MAIN", "--granular"])
    assert args.granular is True
    assert args.target == ["GESTAO.FLOW_DEMO.MAIN"]


def test_granular_flag_does_not_introduce_password_like_flags():
    # Mesmo criterio de tests/test_depgraph_cli.py::
    # test_depgraph_parser_has_no_password_or_dsn_flag, reaplicado aqui
    # porque esta tarefa e quem acrescenta a flag nova.
    parser = cli.build_depgraph_arg_parser()
    option_strings: List[str] = []
    for action in parser._actions:
        option_strings.extend(action.option_strings)
    lowered = [s.lower() for s in option_strings]
    assert "--granular" in lowered
    forbidden = ("pwd", "password", "senha", "dsn", "host", "port", "user")
    offending = [s for s in lowered if any(f in s for f in forbidden)]
    assert offending == []


# --------------------------------------------------------------------------
# alvo de 3 partes: erro sem --granular, aceito com --granular
# --------------------------------------------------------------------------


def test_three_part_target_rejected_without_granular_before_connecting(monkeypatch):
    connect_calls = []
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: connect_calls.append(1) or FakeConn())

    rc = cli.depgraph_main(["GESTAO.FLOW_DEMO.MAIN"])

    assert rc == cli.EXIT_INVALID_ROOT == 4
    assert connect_calls == [], "alvo de 3 partes sem --granular tem que falhar ANTES de conectar"


def test_malformed_target_rejected_with_granular_before_connecting(monkeypatch):
    connect_calls = []
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: connect_calls.append(1) or FakeConn())

    rc = cli.depgraph_main(["SOMENTE.QUATRO.PARTES.AQUI", "--granular"])

    assert rc == cli.EXIT_INVALID_ROOT == 4
    assert connect_calls == []


# --------------------------------------------------------------------------
# nao-regressao: sem --granular, o motor novo e INALCANCAVEL
# --------------------------------------------------------------------------


def test_object_mode_never_reaches_the_granular_code_path(monkeypatch, tmp_path):
    """Prova estrutural da nao-regressao (ver docstring do modulo, camada 2).

    tests/test_depgraph_cli.py ja prova que a SAIDA do modo objeto continua
    certa. O que ele nao pode provar e que ela continua vindo do MESMO codigo:
    uma implementacao nova que por acaso produzisse bytes iguais passaria
    despercebida, e a garantia dada ao usuario ("sem a flag nada muda")
    dependeria de coincidencia em vez de estrutura.

    Aqui os dois pontos de entrada do motor granular sao sabotados para
    estourar se forem chamados. Se o modo objeto termina com exito mesmo
    assim, o caminho novo nao e so equivalente -- ele nao e executado.
    """
    from plsqlflow import procgraph, procgraph_render

    def _explode(*args, **kwargs):
        raise AssertionError(
            "modo objeto (sem --granular) chamou o motor granular -- "
            "a garantia de nao-regressao do contrato foi quebrada"
        )

    monkeypatch.setattr(procgraph, "build_proc_graph", _explode)
    monkeypatch.setattr(procgraph_render, "render_graph", _explode)

    extractor = FakeRootCheckExtractor(_catalog("PKG_A"))
    conn = FakeConn()
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: conn)
    monkeypatch.setattr(cli, "DbDepExtractor", lambda c: extractor)
    # ProcDbExtractor nem deve ser instanciado no caminho de grao objeto.
    monkeypatch.setattr(
        cli,
        "ProcDbExtractor",
        lambda c, root_owners=(): _explode(),
    )

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_A", "--output", str(out_root)])

    # exit 3 (nao 0) porque `FakeRootCheckExtractor.plscope_check` devolve
    # lista vazia: o modo objeto corretamente reporta grafo parcial. Nao e o
    # que este teste mede -- o que importa e que a execucao percorreu o
    # pipeline INTEIRO ate gravar os arquivos sem NUNCA chamar o motor
    # granular (qualquer chamada teria estourado em `_explode`).
    assert rc == cli.EXIT_NEEDS_RECOMPILE == 3
    assert conn.closed is True

    out_dir = out_root / "GESTAO.PKG_A"
    assert (out_dir / "INDEX.md").exists()
    assert (out_dir / "edges.jsonl").exists()
    assert (out_dir / "meta.json").exists()
    # COBERTURA e secao exclusiva do modo granular: nao pode ter vazado para
    # o INDEX.md do modo objeto.
    assert "## COBERTURA" not in (out_dir / "INDEX.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# sucesso -- alvo de 3 partes, arvore em disco
# --------------------------------------------------------------------------


def test_granular_three_part_target_success_writes_expected_tree(monkeypatch, tmp_path, capsys):
    fixture = _load_fixture()
    proc_extractor = FakeExtractor(fixture)
    dep_extractor = FakeRootCheckExtractor(_catalog("FLOW_DEMO"))
    conn = _patch_granular_pipeline(monkeypatch, proc_extractor, dep_extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.FLOW_DEMO.MAIN", "--granular", "--output", str(out_root)])

    assert rc == cli.EXIT_OK == 0
    assert conn.closed is True

    out_dir = out_root / "GESTAO.FLOW_DEMO.MAIN"
    assert (out_dir / "INDEX.md").exists()
    assert (out_dir / "edges.jsonl").exists()
    assert (out_dir / "meta.json").exists()
    assert (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").exists()
    assert not (out_dir / "recompile.sql").exists()

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## COBERTURA" in index_text
    assert "## Ciclos" in index_text
    # FakeExtractor (T-03) so implementa resolve_owner -- object_wrapped e
    # triggers ficam ausentes, e isso tem que aparecer declarado.
    assert "AUSENTE" in index_text

    out = capsys.readouterr().out
    assert "depgraph --granular" in out
    assert "GESTAO.FLOW_DEMO.MAIN" in out
    assert "nos=" in out and "arestas=" in out and "pontos_cegos=" in out


def test_granular_two_part_target_seeds_full_public_api(monkeypatch, tmp_path):
    fixture = _load_fixture()
    proc_extractor = FakeExtractor(fixture)
    dep_extractor = FakeRootCheckExtractor(_catalog("PKG_API"))
    _patch_granular_pipeline(monkeypatch, proc_extractor, dep_extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_API", "--granular", "--output", str(out_root)])

    assert rc == cli.EXIT_OK
    out_dir = out_root / "GESTAO.PKG_API"
    node_names = {p.name for p in (out_dir / "nodes").glob("*.md")}
    assert "GESTAO.PKG_API.PUB1.md" in node_names
    assert "GESTAO.PKG_API.PUB2.md" in node_names


# --------------------------------------------------------------------------
# profundidade total por default; caps opt-in
# --------------------------------------------------------------------------


def test_granular_default_depth_is_unlimited(monkeypatch, tmp_path):
    fixture = _load_fixture()
    proc_extractor = FakeExtractor(fixture)
    dep_extractor = FakeRootCheckExtractor(_catalog("PKG_CH_A"))
    _patch_granular_pipeline(monkeypatch, proc_extractor, dep_extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_CH_A.P1", "--granular", "--output", str(out_root)])

    assert rc == cli.EXIT_OK
    out_dir = out_root / "GESTAO.PKG_CH_A.P1"
    node_names = {p.name for p in (out_dir / "nodes").glob("*.md")}
    # PKG_CH_A -> PKG_CH_B -> PKG_CH_C: sem --max-objects, a cadeia
    # INTEIRA tem que aparecer, mesmo usando o MESMO parser cujo default
    # numerico (5000) e do modo objeto (ver _flag_explicitly_passed).
    assert "GESTAO.PKG_CH_C.P3.md" in node_names
    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "truncamento" not in index_text.lower()


def test_granular_explicit_max_objects_truncates_with_declared_reason(monkeypatch, tmp_path, capsys):
    fixture = _load_fixture()
    proc_extractor = FakeExtractor(fixture)
    dep_extractor = FakeRootCheckExtractor(_catalog("PKG_CH_A"))
    _patch_granular_pipeline(monkeypatch, proc_extractor, dep_extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(
        ["GESTAO.PKG_CH_A.P1", "--granular", "--max-objects", "2", "--output", str(out_root)]
    )

    assert rc == cli.EXIT_OK  # truncamento nao e falha, so aviso
    out_dir = out_root / "GESTAO.PKG_CH_A.P1"
    node_names = {p.name for p in (out_dir / "nodes").glob("*.md")}
    assert "GESTAO.PKG_CH_C.P3.md" not in node_names

    err = capsys.readouterr().err.lower()
    assert "truncado" in err


# --------------------------------------------------------------------------
# exit 3 -- needs_recompile (fallback de grao objeto, T-04, via CLI)
# --------------------------------------------------------------------------


class _CatalogWithExtraRoot(FallbackDepExtractor):
    """`FakeDepExtractor` de tests/test_procgraph_fallback.py, mais um
    unico objeto extra no catalogo (a propria raiz `GESTAO.A`, que o
    CATALOG daquele modulo nao lista -- ele so precisa aparecer como
    ALVO de dependencia, nunca como raiz de CLI la). Subclasse em vez de
    mutar o dict `CATALOG` module-level (evitaria vazar estado entre
    testes de arquivos diferentes)."""

    def object_catalog(self, owner, object_list=None):
        rows = super().object_catalog(owner, object_list)
        if owner.upper() == "GESTAO":
            rows = rows + [
                extract.ObjectCatalogRow(
                    owner="GESTAO", object_name="A", object_type="PACKAGE BODY",
                    status="VALID", last_ddl_time="2026-08-15 10:00:00",
                )
            ]
        return rows


def test_granular_needs_recompile_returns_exit_three_and_still_writes_files(monkeypatch, tmp_path):
    proc_extractor = FallbackProcExtractor()
    dep_extractor = _CatalogWithExtraRoot()
    conn = _patch_granular_pipeline(monkeypatch, proc_extractor, dep_extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.A.P1", "--granular", "--output", str(out_root)])

    assert rc == cli.EXIT_NEEDS_RECOMPILE == 3
    assert conn.closed is True

    out_dir = out_root / "GESTAO.A.P1"
    assert (out_dir / "INDEX.md").exists(), "exit 3 nao e falha -- arquivos gravados mesmo assim"
    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "GESTAO.B" in index_text
    assert "Recompilaveis" in index_text


# --------------------------------------------------------------------------
# erros de conexao / raiz inexistente -- mesmos exit codes do modo objeto
# --------------------------------------------------------------------------


def test_granular_connection_config_error_returns_five(monkeypatch, capsys):
    def _boom(alias=None, env=None):
        raise db.ConnectionConfigError("alias 'dev' nao encontrado")

    monkeypatch.setattr(db, "connect", _boom)

    rc = cli.depgraph_main(["GESTAO.FLOW_DEMO.MAIN", "--granular", "--conn", "dev"])

    assert rc == cli.EXIT_CONNECTION_ERROR == 5
    assert "erro" in capsys.readouterr().err.lower()


def test_granular_driver_error_returns_five(monkeypatch, capsys):
    def _boom(alias=None, env=None):
        raise oracledb.Error("ORA-12154")

    monkeypatch.setattr(db, "connect", _boom)

    rc = cli.depgraph_main(["GESTAO.FLOW_DEMO.MAIN", "--granular"])

    assert rc == cli.EXIT_CONNECTION_ERROR == 5


def test_granular_nonexistent_root_returns_four(monkeypatch, tmp_path):
    fixture = _load_fixture()
    proc_extractor = FakeExtractor(fixture)
    dep_extractor = FakeRootCheckExtractor(_catalog("FLOW_DEMO"))  # nao tem NAO_EXISTE
    conn = _patch_granular_pipeline(monkeypatch, proc_extractor, dep_extractor)

    rc = cli.depgraph_main(["GESTAO.NAO_EXISTE.P1", "--granular", "--output", str(tmp_path / "out")])

    assert rc == cli.EXIT_INVALID_ROOT == 4
    assert conn.closed is True


# --------------------------------------------------------------------------
# determinismo (duas execucoes, saida identica)
# --------------------------------------------------------------------------


def test_granular_cli_output_is_deterministic_across_two_runs(monkeypatch, tmp_path):
    fixture = _load_fixture()
    dep_extractor_1 = FakeRootCheckExtractor(_catalog("FLOW_DEMO"))
    _patch_granular_pipeline(monkeypatch, FakeExtractor(fixture), dep_extractor_1)
    out_1 = tmp_path / "run1"
    rc1 = cli.depgraph_main(["GESTAO.FLOW_DEMO.MAIN", "--granular", "--output", str(out_1)])
    assert rc1 == cli.EXIT_OK

    dep_extractor_2 = FakeRootCheckExtractor(_catalog("FLOW_DEMO"))
    _patch_granular_pipeline(monkeypatch, FakeExtractor(fixture), dep_extractor_2)
    out_2 = tmp_path / "run2"
    rc2 = cli.depgraph_main(["GESTAO.FLOW_DEMO.MAIN", "--granular", "--output", str(out_2)])
    assert rc2 == cli.EXIT_OK

    index_1 = (out_1 / "GESTAO.FLOW_DEMO.MAIN" / "INDEX.md").read_bytes()
    index_2 = (out_2 / "GESTAO.FLOW_DEMO.MAIN" / "INDEX.md").read_bytes()
    assert index_1 == index_2

    edges_1 = (out_1 / "GESTAO.FLOW_DEMO.MAIN" / "edges.jsonl").read_bytes()
    edges_2 = (out_2 / "GESTAO.FLOW_DEMO.MAIN" / "edges.jsonl").read_bytes()
    assert edges_1 == edges_2


# --------------------------------------------------------------------------
# ProcDbExtractor -- extractor de producao (usado sem monkeypatch aqui:
# so a query/conexao e trocada por fakes locais, a CLASSE real e exercitada)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: List[Tuple[Any, ...]], columns: List[str]):
        self._rows = rows
        self.description = [(c,) for c in columns]

    def execute(self, sql_text, binds):
        self._executed = (sql_text, binds)

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeDbConn:
    def __init__(self, rows: List[Tuple[Any, ...]], columns: List[str]):
        self._rows = rows
        self._columns = columns
        self.executed_binds: List[Dict[str, Any]] = []

    def cursor(self):
        return _RecordingCursor(self)


class _RecordingCursor(_FakeCursor):
    def __init__(self, conn: "_FakeDbConn"):
        super().__init__(conn._rows, conn._columns)
        self._conn = conn

    def execute(self, sql_text, binds):
        super().execute(sql_text, binds)
        self._conn.executed_binds.append(dict(binds))


def test_proc_db_extractor_resolve_owner_dedupes_spec_and_body_rows():
    # Validado ao vivo (T-08, ver docstring de ProcDbExtractor): a mesma
    # signature pode aparecer em DUAS linhas (SPEC e BODY) do MESMO objeto
    # -- resolve_owner tem que devolver UM par so, nunca quebrar.
    rows = [("GESTAO", "FLOW_DEMO"), ("GESTAO", "FLOW_DEMO")]
    conn = _FakeDbConn(rows, ["OWNER", "OBJECT_NAME"])
    extractor = cli.ProcDbExtractor(conn, root_owners=["GESTAO"])

    result = extractor.resolve_owner("SOME_SIGNATURE")

    assert result == ("GESTAO", "FLOW_DEMO")
    assert conn.executed_binds[-1]["owner_list"] == "GESTAO"
    assert conn.executed_binds[-1]["signature"] == "SOME_SIGNATURE"


def test_proc_db_extractor_resolve_owner_returns_none_when_not_found():
    conn = _FakeDbConn([], ["OWNER", "OBJECT_NAME"])
    extractor = cli.ProcDbExtractor(conn, root_owners=["GESTAO"])

    assert extractor.resolve_owner("UNKNOWN_SIG") is None


def test_proc_db_extractor_resolve_owner_scope_grows_with_loaded_objects(monkeypatch):
    conn = _FakeDbConn([], ["OWNER", "OBJECT_NAME"])
    extractor = cli.ProcDbExtractor(conn, root_owners=["GESTAO"])

    monkeypatch.setattr(extract, "fetch_plscope_tree_batch", lambda c, o, n: [])
    extractor.plscope_identifiers("OUTRO_SCHEMA", "PKG_X")

    extractor.resolve_owner("SIG")

    owner_list = conn.executed_binds[-1]["owner_list"]
    assert "GESTAO" in owner_list.split(",")
    assert "OUTRO_SCHEMA" in owner_list.split(",")


def test_proc_db_extractor_object_wrapped_detects_marker():
    conn = _FakeDbConn([("PACKAGE BODY pkg wrapped",), ("abcd",)], ["TEXT"])
    extractor = cli.ProcDbExtractor(conn)

    assert extractor.object_wrapped("GESTAO", "PKG_X") is True


def test_proc_db_extractor_object_wrapped_false_for_plain_source():
    conn = _FakeDbConn([("PACKAGE BODY pkg IS",), ("BEGIN NULL; END;",)], ["TEXT"])
    extractor = cli.ProcDbExtractor(conn)

    assert extractor.object_wrapped("GESTAO", "PKG_X") is False


def test_proc_db_extractor_triggers_uses_any_status_fetcher(monkeypatch):
    calls = []

    def fake_fetch(conn, owner, table_list):
        calls.append((owner, table_list))
        return []

    monkeypatch.setattr(extract, "fetch_triggers_any_status", fake_fetch)
    extractor = cli.ProcDbExtractor(conn=object())

    extractor.triggers("gestao", ["tab_b", "tab_a"])

    assert calls == [("gestao", "TAB_A,TAB_B")]
