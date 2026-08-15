"""Testes offline (T-05, contrato depgraph-scale) para multiplas raizes e
para o cap de `--max-objects` (default ampliado, `0` desliga) -- ver
spec.md, Escopo item 5.

100% sem banco, sem relogio, sem `random`. Dois niveis de teste:
- `plsqlflow.depgraph.build_dep_graph(..., roots=[...])` direto (fila/
  visited-set compartilhados) -- prova a semantica de multiplas raizes no
  motor da BFS, sem precisar do CLI em volta.
- `plsqlflow.cli.depgraph_main([...])`, com `db.connect`/`cli.DbDepExtractor`
  monkeypatchados (mesmo padrao de tests/test_depgraph_cli.py) -- prova a
  fiacao ponta a ponta: parsing de `target` (`nargs="+"`), obrigatoriedade
  de `--name` com mais de uma raiz, nome do diretorio de saida, `meta.json`
  com todas as raizes, e a traducao `--max-objects 0` -> sem cap de
  quantidade feita em `cli._effective_max_objects` (o motor da BFS em si
  continua tratando `max_objects=0` como "cabe zero objetos" -- contrato
  congelado de T-02/tests/test_depgraph_bfs_limits.py, fora da superficie
  desta tarefa; a traducao acontece so na borda do CLI).
"""
from __future__ import annotations

import json
from typing import Dict, List, Tuple

from plsqlflow import cli, db, depgraph, extract

# --------------------------------------------------------------------------
# Helpers de fixture sintetica (mesmo estilo de tests/test_depgraph_bfs_limits.py)
# --------------------------------------------------------------------------


def _dep(owner: str, name: str, type_: str = "PACKAGE") -> extract.DepsDirectRow:
    return extract.DepsDirectRow(
        referenced_owner=owner, referenced_name=name, referenced_type=type_, dependency_type="HARD"
    )


def _cat(owner: str, name: str, type_: str = "PACKAGE", status: str = "VALID") -> extract.ObjectCatalogRow:
    return extract.ObjectCatalogRow(
        owner=owner, object_name=name, object_type=type_, status=status, last_ddl_time="2026-08-15 10:00:00"
    )


class _Source:
    """Fake minimo do Protocol `DepExtractor` -- sem `deps_direct_batch`
    (caminho por-objeto), suficiente para os testes de multi-raiz: eles nao
    exercitam a equivalencia lote/por-objeto (isso e T-01/T-02, ja provado
    em outros arquivos), so a semantica de raizes multiplas e o cap."""

    def __init__(self, deps=None, catalog=None, plscope=None, synonyms=None):
        self.deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = deps or {}
        self.catalog: Dict[str, List[extract.ObjectCatalogRow]] = catalog or {}
        self.plscope: Dict[str, List[extract.PlscopeCheckRow]] = plscope or {}
        self.synonyms: Dict[Tuple[str, str], List[extract.SynonymRow]] = synonyms or {}

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        return list(self.deps.get((owner.upper(), name.upper()), []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return list(self.synonyms.get((owner.upper(), name.upper()), []))


# --------------------------------------------------------------------------
# depgraph.build_dep_graph(..., roots=[...]) -- semantica pura da BFS
# --------------------------------------------------------------------------


def test_two_roots_sharing_object_produce_a_single_shared_node():
    """GESTAO.ROOT_A e GESTAO.ROOT_B dependem os dois de GESTAO.SHARED --
    o objeto compartilhado tem que virar UM SO no, com uma aresta CALL de
    CADA raiz apontando para ele (nao duas raizes = dois nos)."""
    deps = {
        ("GESTAO", "ROOT_A"): [_dep("GESTAO", "SHARED", type_="TABLE")],
        ("GESTAO", "ROOT_B"): [_dep("GESTAO", "SHARED", type_="TABLE")],
        ("GESTAO", "SHARED"): [],
    }
    catalog = {
        "GESTAO": [
            _cat("GESTAO", "ROOT_A", type_="TABLE"),
            _cat("GESTAO", "ROOT_B", type_="TABLE"),
            _cat("GESTAO", "SHARED", type_="TABLE"),
        ]
    }
    extractor = _Source(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT_A"), roots=[("GESTAO", "ROOT_B")])

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {"GESTAO.ROOT_A", "GESTAO.ROOT_B", "GESTAO.SHARED"}
    # SHARED aparece uma unica vez na lista de nos (nao duplicado).
    shared_nodes = [n for n in result.nodes if n.object_name == "SHARED"]
    assert len(shared_nodes) == 1

    edge_pairs = {(e.from_ref, e.to_ref, e.edge_type) for e in result.edges}
    assert ("GESTAO.ROOT_A", "GESTAO.SHARED", "CALL") in edge_pairs
    assert ("GESTAO.ROOT_B", "GESTAO.SHARED", "CALL") in edge_pairs
    assert result.truncated is False


def test_two_disjoint_roots_contain_the_closure_of_both():
    deps = {
        ("GESTAO", "ROOT_A"): [_dep("GESTAO", "CHILD_A", type_="TABLE")],
        ("GESTAO", "ROOT_B"): [_dep("GESTAO", "CHILD_B", type_="TABLE")],
        ("GESTAO", "CHILD_A"): [],
        ("GESTAO", "CHILD_B"): [],
    }
    catalog = {
        "GESTAO": [
            _cat("GESTAO", "ROOT_A", type_="TABLE"),
            _cat("GESTAO", "ROOT_B", type_="TABLE"),
            _cat("GESTAO", "CHILD_A", type_="TABLE"),
            _cat("GESTAO", "CHILD_B", type_="TABLE"),
        ]
    }
    extractor = _Source(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT_A"), roots=[("GESTAO", "ROOT_B")])

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {"GESTAO.ROOT_A", "GESTAO.CHILD_A", "GESTAO.ROOT_B", "GESTAO.CHILD_B"}
    edge_pairs = {(e.from_ref, e.to_ref, e.edge_type) for e in result.edges}
    assert ("GESTAO.ROOT_A", "GESTAO.CHILD_A", "CALL") in edge_pairs
    assert ("GESTAO.ROOT_B", "GESTAO.CHILD_B", "CALL") in edge_pairs


def test_single_root_result_is_unchanged_by_the_roots_parameter():
    """Nao-regressao: `roots=None` (default), `roots=()` e nem passar o
    parametro tem que produzir o MESMO `DepGraphResult` de uma raiz so --
    a assinatura antiga continua se comportando exatamente como antes."""
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "CHILD", type_="TABLE")], ("GESTAO", "CHILD"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT", type_="TABLE"), _cat("GESTAO", "CHILD", type_="TABLE")]}

    baseline = depgraph.build_dep_graph(_Source(deps=deps, catalog=catalog), ("GESTAO", "ROOT"))
    explicit_none = depgraph.build_dep_graph(_Source(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), roots=None)
    explicit_empty = depgraph.build_dep_graph(_Source(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), roots=())

    assert baseline == explicit_none == explicit_empty
    assert {"{}.{}".format(n.owner, n.object_name) for n in baseline.nodes} == {"GESTAO.ROOT", "GESTAO.CHILD"}


def test_repeating_the_same_root_in_roots_does_not_duplicate_it():
    """Raiz repetida (a mesma raiz tambem listada em `roots`) e um no-op --
    o visited-set de `enqueue` ja garante isso, sem logica nova."""
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "CHILD", type_="TABLE")], ("GESTAO", "CHILD"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT", type_="TABLE"), _cat("GESTAO", "CHILD", type_="TABLE")]}

    baseline = depgraph.build_dep_graph(_Source(deps=deps, catalog=catalog), ("GESTAO", "ROOT"))
    with_self_repeated = depgraph.build_dep_graph(
        _Source(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), roots=[("GESTAO", "ROOT")]
    )

    assert baseline == with_self_repeated


def test_roots_parameter_accepts_deproot_dataclass_mixed_with_tuple():
    """`roots` aceita `DepRoot` e tupla misturados -- mesma normalizacao de
    `root` (`_normalize_root`), so aplicada item a item."""
    deps = {
        ("GESTAO", "ROOT_A"): [],
        ("GESTAO", "ROOT_B"): [],
    }
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT_A", type_="TABLE"), _cat("GESTAO", "ROOT_B", type_="TABLE")]}

    result = depgraph.build_dep_graph(
        _Source(deps=deps, catalog=catalog),
        depgraph.DepRoot(owner="GESTAO", object_name="ROOT_A"),
        roots=[("GESTAO", "ROOT_B")],
    )

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {"GESTAO.ROOT_A", "GESTAO.ROOT_B"}


# --------------------------------------------------------------------------
# cli.depgraph_main -- fiacao ponta a ponta (mesmo padrao de
# tests/test_depgraph_cli.py: FakeConn + DbDepExtractor monkeypatchados)
# --------------------------------------------------------------------------


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _CliFakeExtractor(_Source):
    """`_Source` + os metodos extras que `cli._build_depgraph_result`/
    `depgraph.add_trigger_phase` exigem estruturalmente
    (`triggers`/`tab_columns`/`statements`/`source`). Os cenarios abaixo
    usam so objetos TABLE (fora de `PLSQL_OBJECT_TYPES`), entao
    `statements`/`source` nunca sao de fato chamados -- presentes so para
    satisfazer a interface que `cli.DbDepExtractor` implementa."""

    def triggers(self, owner: str, table_names) -> List[extract.TriggerRow]:
        return []

    def tab_columns(self, owner: str, table_names) -> List[extract.TabColumnRow]:
        return []

    def statements(self, owner: str, object_name: str) -> List[extract.PlscopeStatementRow]:
        return []

    def source(self, owner: str, object_name: str) -> List[extract.FetchSourceRow]:
        return []


def _patch_pipeline(monkeypatch, extractor) -> FakeConn:
    conn = FakeConn()
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: conn)
    monkeypatch.setattr(cli, "DbDepExtractor", lambda c: extractor)
    return conn


def _shared_object_fixture() -> _CliFakeExtractor:
    deps = {
        ("GESTAO", "PKG_A"): [_dep("GESTAO", "SHARED", type_="TABLE")],
        ("GESTAO", "PKG_B"): [_dep("GESTAO", "SHARED", type_="TABLE")],
        ("GESTAO", "SHARED"): [],
    }
    catalog = {
        "GESTAO": [
            _cat("GESTAO", "PKG_A", type_="TABLE"),
            _cat("GESTAO", "PKG_B", type_="TABLE"),
            _cat("GESTAO", "SHARED", type_="TABLE"),
        ]
    }
    return _CliFakeExtractor(deps=deps, catalog=catalog)


def test_cli_missing_name_with_multiple_roots_errors_before_connecting(monkeypatch, tmp_path, capsys):
    connect_calls: List[int] = []
    monkeypatch.setattr(db, "connect", lambda alias=None, env=None: connect_calls.append(1) or FakeConn())

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_A", "GESTAO.PKG_B", "--output", str(out_root)])

    assert rc == cli.EXIT_INVALID_ROOT == 4
    assert connect_calls == [], "--name ausente com multiplas raizes tem que falhar ANTES de conectar"
    assert not out_root.exists(), "nenhum arquivo deveria ter sido escrito"
    err = capsys.readouterr().err.lower()
    assert "erro" in err and "--name" in err


def test_cli_multiple_roots_with_name_writes_single_graph_and_registers_all_roots(monkeypatch, tmp_path):
    extractor = _shared_object_fixture()
    conn = _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(
        ["GESTAO.PKG_A", "GESTAO.PKG_B", "--name", "modulo-financeiro", "--output", str(out_root)]
    )

    assert rc == cli.EXIT_OK == 0
    assert conn.closed is True

    out_dir = out_root / "modulo-financeiro"
    assert out_dir.is_dir(), "saida tem que ir para <output>/<name>/ com multiplas raizes"
    assert (out_dir / "INDEX.md").exists()
    assert (out_dir / "edges.jsonl").exists()
    # Objeto compartilhado -- um so arquivo de no, nao um por raiz.
    assert (out_dir / "nodes" / "GESTAO.SHARED.md").exists()

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["params"]["roots"] == ["GESTAO.PKG_A", "GESTAO.PKG_B"]
    assert "GESTAO.PKG_A" in meta["params"]["root_ref"]
    assert "GESTAO.PKG_B" in meta["params"]["root_ref"]


def test_cli_single_root_with_name_also_uses_name_as_output_dir(monkeypatch, tmp_path):
    """`--name` com UMA raiz so e opcional, mas quando informado tambem
    rotula a saida (nao so a raiz sozinha teria comportamento diferente de
    quando ha varias) -- ver docstring de `depgraph_main`."""
    deps = {("GESTAO", "PKG_A"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "PKG_A", type_="TABLE")]}
    extractor = _CliFakeExtractor(deps=deps, catalog=catalog)
    conn = _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_A", "--name", "so-uma-raiz", "--output", str(out_root)])

    assert rc == cli.EXIT_OK == 0
    assert conn.closed is True
    assert (out_root / "so-uma-raiz" / "INDEX.md").exists()
    assert not (out_root / "GESTAO.PKG_A").exists()


def test_cli_single_root_without_name_keeps_owner_dot_object_output_dir(monkeypatch, tmp_path):
    """Nao-regressao explicita: sem `--name`, uma raiz so continua saindo
    em `<output>/<OWNER>.<OBJETO>/`, byte a byte como antes do T-05."""
    deps = {("GESTAO", "PKG_A"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "PKG_A", type_="TABLE")]}
    extractor = _CliFakeExtractor(deps=deps, catalog=catalog)
    conn = _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.PKG_A", "--output", str(out_root)])

    assert rc == cli.EXIT_OK == 0
    assert conn.closed is True
    assert (out_root / "GESTAO.PKG_A" / "INDEX.md").exists()


def test_cli_nonexistent_root_among_multiple_reports_all_missing(monkeypatch, tmp_path, capsys):
    extractor = _shared_object_fixture()  # tem PKG_A/PKG_B/SHARED, nao tem PKG_NAO_EXISTE
    conn = _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(
        ["GESTAO.PKG_A", "GESTAO.PKG_NAO_EXISTE", "--name", "modulo", "--output", str(out_root)]
    )

    assert rc == cli.EXIT_INVALID_ROOT == 4
    assert conn.closed is True
    err = capsys.readouterr().err
    assert "PKG_NAO_EXISTE" in err
    assert not out_root.exists() or not any(out_root.rglob("INDEX.md"))


# --------------------------------------------------------------------------
# --max-objects: default 5000 e `0` desliga o cap de quantidade
# --------------------------------------------------------------------------


def test_default_max_objects_is_5000_locked():
    """Trava o valor (spec T-05, escopo item 5) -- se alguem baixar o
    default por descuido numa mudanca futura, este teste acusa."""
    assert cli.DEFAULT_MAX_OBJECTS == 5000
    args = cli.build_depgraph_arg_parser().parse_args(["GESTAO.PKG_A"])
    assert args.max_objects == 5000


def test_effective_max_objects_translates_zero_to_unlimited():
    assert cli._effective_max_objects(0) == cli._UNLIMITED_MAX_OBJECTS
    assert cli._effective_max_objects(0) != 0


def test_effective_max_objects_passes_through_nonzero_values():
    assert cli._effective_max_objects(5000) == 5000
    assert cli._effective_max_objects(1) == 1
    assert cli._effective_max_objects(500) == 500


def _wide_single_owner_fixture(width: int) -> _CliFakeExtractor:
    """Raiz TABLE com `width` filhos TABLE no mesmo owner -- `width` maior
    que o default ANTIGO (500) prova que `--max-objects 0` de fato desliga
    o cap de quantidade (nao so "o default subiu")."""
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "CHILD{:04d}".format(i), type_="TABLE") for i in range(width)]}
    for i in range(width):
        deps[("GESTAO", "CHILD{:04d}".format(i))] = []
    catalog = {
        "GESTAO": [_cat("GESTAO", "ROOT", type_="TABLE")]
        + [_cat("GESTAO", "CHILD{:04d}".format(i), type_="TABLE") for i in range(width)]
    }
    return _CliFakeExtractor(deps=deps, catalog=catalog)


def test_cli_max_objects_zero_does_not_truncate_graph_bigger_than_old_default(monkeypatch, tmp_path, capsys):
    width = 520  # > 500 (default antigo) e < 5000 (default novo) de proposito
    extractor = _wide_single_owner_fixture(width)
    conn = _patch_pipeline(monkeypatch, extractor)

    out_root = tmp_path / "out"
    rc = cli.depgraph_main(["GESTAO.ROOT", "--output", str(out_root), "--max-objects", "0"])

    assert rc == cli.EXIT_OK == 0
    assert conn.closed is True

    out_dir = out_root / "GESTAO.ROOT"
    edges_lines = (out_dir / "edges.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(edges_lines) == width  # ROOT -> cada CHILD, nenhum cortado por quantidade

    out, err = capsys.readouterr()
    assert "nos={}".format(width + 1) in out
    assert "truncado" not in err.lower(), "max-objects 0 nao deveria truncar por quantidade"

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["params"]["max_objects"] == 0, "meta.json registra o valor literal informado pelo usuario"


def test_cli_max_objects_zero_still_truncates_by_max_depth():
    """`--max-objects 0` desliga so o cap de QUANTIDADE -- o aviso por
    `--max-depth` continua valendo (spec T-05: "o aviso por max_depth
    continua"). Testado direto no motor (`depgraph.build_dep_graph`) com o
    valor ja traduzido (`cli._effective_max_objects(0)`), pois e exatamente
    isso que `depgraph_main` passa adiante."""
    chain_len = 25  # > max_depth default (20)
    deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = {}
    catalog_rows = []
    for i in range(chain_len):
        cur = "N{:02d}".format(i)
        nxt = "N{:02d}".format(i + 1)
        deps[("GESTAO", cur)] = [_dep("GESTAO", nxt, type_="TABLE")] if i + 1 < chain_len else []
        catalog_rows.append(_cat("GESTAO", cur, type_="TABLE"))
    catalog = {"GESTAO": catalog_rows}
    extractor = _Source(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(
        extractor, ("GESTAO", "N00"), max_objects=cli._effective_max_objects(0), max_depth=20
    )

    assert result.truncated is True
    assert "max_depth" in (result.truncation_reason or "")
    assert "max_objects" not in (result.truncation_reason or "")
    # 21 nos (N00..N20, profundidade 0..20): o no NO limite (depth==max_depth)
    # ainda e criado -- so nao e expandido -- entao nenhum no e cortado por
    # QUANTIDADE (o cap esta desligado); a cadeia so para de crescer porque
    # bateu max_depth, exatamente o que este teste prova.
    assert len(result.nodes) == 21
    assert result.not_expanded == ["GESTAO.N20"]
