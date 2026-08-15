"""Testes offline (T-02) para a BFS de dependencias em plsqlflow/depgraph.py.

100% sem banco. Dois tipos de fonte de dados:
- `FlowDemoDepExtractor`: le tests/fixtures/depgraph_extract.json (extracao
  REAL do dev, pacote GESTAO.FLOW_DEMO) para deps_direct/object_catalog. A
  fixture NAO tem secao plscope_check (a T-01 nao a incluiu) nem synonym --
  esses dois pedacos sao supridos aqui como dado sintetico local (nao
  gravado na fixture JSON), conforme instrucao da tarefa.
- `SyntheticExtractor`: fonte generica em memoria para os casos que o
  FLOW_DEMO real nao exercita (ciclo, estouro de limite, sinonimo, objeto
  sem PL/Scope).

Importante sobre o escopo da fixture: `_expected_bfs` no JSON documenta o
grafo final incluindo a re-injecao de triggers (Fase 4 / T-04), que NAO faz
parte desta tarefa -- T-02 e so a BFS sobre ALL_DEPENDENCIES (deps_direct).
Por isso os testes abaixo validam o subconjunto que a BFS pura de fato
alcanca a partir de GESTAO.FLOW_DEMO (FLOW_DEMO, FLOW_DEMO_LOG,
SYS.STANDARD) e NAO o TRG_FLOW_DEMO_LOG/FLOW_DEMO_AUDIT, que so entram via
Fase 4.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from plsqlflow import depgraph, extract

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "depgraph_extract.json"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE = load_fixture()


def _kw(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k.lower(): v for k, v in row.items()}


# --------------------------------------------------------------- fake real (FLOW_DEMO)


class FlowDemoDepExtractor:
    """Extractor fake alimentado pela extracao real do dev (FLOW_DEMO).

    plscope_check e synonym nao existem na fixture JSON (fora do escopo da
    T-01 que a gerou) -- fornecidos aqui como dado sintetico local:
    FLOW_DEMO foi de fato compilado com PL/Scope completo no dev real.
    """

    def __init__(self, fixture: Dict[str, Any]):
        self._catalog = [extract.ObjectCatalogRow(**_kw(row)) for row in fixture["object_catalog"]]
        self._deps = fixture["deps_direct"]
        self.deps_direct_calls: List[Tuple[str, str]] = []
        self.object_catalog_calls: List[str] = []
        self.plscope_check_calls: List[str] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        self.deps_direct_calls.append((owner.upper(), name.upper()))
        key = "{}.{}".format(owner.upper(), name.upper())
        rows = self._deps.get(key, [])
        return [extract.DepsDirectRow(**_kw(r)) for r in rows]

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        self.object_catalog_calls.append(owner.upper())
        return [r for r in self._catalog if r.owner.upper() == owner.upper()]

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        self.plscope_check_calls.append(owner.upper())
        if owner.upper() != "GESTAO":
            return []
        return [
            extract.PlscopeCheckRow(
                owner="GESTAO", name="FLOW_DEMO", type="PACKAGE",
                plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL",
            ),
            extract.PlscopeCheckRow(
                owner="GESTAO", name="FLOW_DEMO", type="PACKAGE BODY",
                plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL",
            ),
        ]

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return []


def _flow_demo_root() -> Tuple[str, str]:
    root = FIXTURE["root"]
    return root["owner"], root["object_name"]


def _build_flow_demo():
    extractor = FlowDemoDepExtractor(FIXTURE)
    result = depgraph.build_dep_graph(extractor, _flow_demo_root())
    return extractor, result


# ---------------------------------------------------------- fake sintetico generico


class SyntheticExtractor:
    def __init__(self, deps=None, catalog=None, plscope=None, synonyms=None):
        self.deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = deps or {}
        self.catalog: Dict[str, List[extract.ObjectCatalogRow]] = catalog or {}
        self.plscope: Dict[str, List[extract.PlscopeCheckRow]] = plscope or {}
        self.synonyms: Dict[Tuple[str, str], List[extract.SynonymRow]] = synonyms or {}
        self.deps_direct_calls: List[Tuple[str, str]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        key = (owner.upper(), name.upper())
        self.deps_direct_calls.append(key)
        return list(self.deps.get(key, []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return list(self.synonyms.get((owner.upper(), name.upper()), []))


def _dep(owner: str, name: str, type_: str = "PACKAGE") -> extract.DepsDirectRow:
    return extract.DepsDirectRow(
        referenced_owner=owner, referenced_name=name, referenced_type=type_, dependency_type="HARD"
    )


def _cat(owner: str, name: str, type_: str = "PACKAGE", status: str = "VALID") -> extract.ObjectCatalogRow:
    return extract.ObjectCatalogRow(
        owner=owner, object_name=name, object_type=type_, status=status, last_ddl_time="2026-08-15 10:00:00"
    )


# --------------------------------------------------------------------- FLOW_DEMO real


def test_flow_demo_bfs_reaches_expected_backbone_nodes():
    # T-02 e so a BFS sobre ALL_DEPENDENCIES (deps_direct) -- NAO inclui
    # triggers (Fase 4/T-04). A partir de GESTAO.FLOW_DEMO, os unicos nos
    # alcancaveis via deps_direct sao FLOW_DEMO (self-loop filtrado),
    # FLOW_DEMO_LOG e a fronteira SYS.STANDARD.
    _, result = _build_flow_demo()
    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {"GESTAO.FLOW_DEMO", "GESTAO.FLOW_DEMO_LOG", "SYS.STANDARD"}
    assert result.truncated is False
    assert result.truncation_reason is None


def test_flow_demo_expected_bfs_reference_is_a_superset_including_trigger_phase():
    # Confere a nota da fixture: _expected_bfs inclui TRG_FLOW_DEMO_LOG e
    # FLOW_DEMO_AUDIT, que so entram na Fase 4 (T-04) -- nao no escopo desta
    # BFS pura. O conjunto que produzimos e um subconjunto estrito do
    # _expected_bfs documentado.
    _, result = _build_flow_demo()
    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    reference = set(FIXTURE["_expected_bfs"]["nodes"])
    assert node_ids < reference
    assert "GESTAO.TRG_FLOW_DEMO_LOG" not in node_ids
    assert "GESTAO.FLOW_DEMO_AUDIT" not in node_ids


def test_package_and_package_body_collapse_into_one_node():
    _, result = _build_flow_demo()
    flow_demo_nodes = [n for n in result.nodes if n.owner == "GESTAO" and n.object_name == "FLOW_DEMO"]
    assert len(flow_demo_nodes) == 1
    assert flow_demo_nodes[0].object_type in ("PACKAGE", "PACKAGE BODY")


def test_self_loop_is_discarded():
    _, result = _build_flow_demo()
    assert not any(e.from_ref == e.to_ref for e in result.edges)
    # a aresta especifica do self-loop real da fixture nunca aparece
    assert not any(e.from_ref == "GESTAO.FLOW_DEMO" and e.to_ref == "GESTAO.FLOW_DEMO" for e in result.edges)


def test_sys_standard_enters_as_leaf_and_is_not_expanded():
    extractor, result = _build_flow_demo()
    standard = next(n for n in result.nodes if n.owner == "SYS" and n.object_name == "STANDARD")
    assert standard.is_leaf is True
    # fronteira nunca reconsultada: nem deps_direct, nem catalogo/plscope
    assert ("SYS", "STANDARD") not in extractor.deps_direct_calls
    assert "SYS" not in extractor.object_catalog_calls
    assert "SYS" not in extractor.plscope_check_calls


def test_flow_demo_has_plscope_and_is_not_in_needs_recompile():
    _, result = _build_flow_demo()
    assert "GESTAO.FLOW_DEMO" not in result.needs_recompile
    flow_demo = next(n for n in result.nodes if n.object_name == "FLOW_DEMO")
    assert flow_demo.plscope is True


def test_flow_demo_determinism_two_runs_produce_identical_result():
    _, result1 = _build_flow_demo()
    _, result2 = _build_flow_demo()
    assert result1 == result2


# ------------------------------------------------------------------------ ciclo sintetico


def test_synthetic_cycle_a_b_a_terminates_without_infinite_loop():
    extractor = SyntheticExtractor(
        deps={
            ("GESTAO", "A"): [_dep("GESTAO", "B")],
            ("GESTAO", "B"): [_dep("GESTAO", "A")],
        },
        catalog={"GESTAO": [_cat("GESTAO", "A"), _cat("GESTAO", "B")]},
    )
    result = depgraph.build_dep_graph(extractor, ("GESTAO", "A"))

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {"GESTAO.A", "GESTAO.B"}
    edge_pairs = {(e.from_ref, e.to_ref) for e in result.edges}
    assert ("GESTAO.A", "GESTAO.B") in edge_pairs
    assert ("GESTAO.B", "GESTAO.A") in edge_pairs
    assert result.truncated is False


# -------------------------------------------------------------------- max_objects


def test_max_objects_small_truncates_with_reason_and_not_expanded():
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "LEAF{}".format(i)) for i in range(20)]}
    catalog = [_cat("GESTAO", "ROOT")] + [_cat("GESTAO", "LEAF{}".format(i), type_="TABLE") for i in range(20)]
    extractor = SyntheticExtractor(deps=deps, catalog={"GESTAO": catalog})

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT"), max_objects=5)

    assert len(result.nodes) <= 5
    assert result.truncated is True
    assert result.truncation_reason is not None
    assert "max_objects" in result.truncation_reason
    assert result.not_expanded, "estouro de max_objects nao pode ser silencioso"


def test_max_objects_never_truncates_silently_when_under_limit():
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "LEAF1")]}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT"), _cat("GESTAO", "LEAF1", type_="TABLE")]}
    extractor = SyntheticExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT"), max_objects=500)

    assert result.truncated is False
    assert result.truncation_reason is None
    assert result.not_expanded == []


# ---------------------------------------------------------------------- max_depth


def test_max_depth_small_truncates_deep_chain():
    chain_length = 10
    deps = {
        ("GESTAO", "OBJ{}".format(i)): [_dep("GESTAO", "OBJ{}".format(i + 1))]
        for i in range(chain_length)
    }
    catalog = {"GESTAO": [_cat("GESTAO", "OBJ{}".format(i)) for i in range(chain_length + 1)]}
    extractor = SyntheticExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "OBJ0"), max_depth=2)

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    # OBJ0 (depth 0), OBJ1 (depth 1), OBJ2 (depth 2, criado mas nao expandido)
    assert node_ids == {"GESTAO.OBJ0", "GESTAO.OBJ1", "GESTAO.OBJ2"}
    assert result.truncated is True
    assert result.truncation_reason is not None
    assert "max_depth" in result.truncation_reason
    assert "GESTAO.OBJ2" in result.not_expanded
    # a cadeia nunca foi expandida alem do limite
    assert ("GESTAO", "OBJ3") not in extractor.deps_direct_calls


# --------------------------------------------------------------- fronteira DBMS_/UTL_


def test_dbms_prefixed_object_becomes_leaf_without_expansion():
    deps = {("GESTAO", "ROOT"): [_dep("SYS", "DBMS_OUTPUT")]}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT")]}
    extractor = SyntheticExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT"))

    dbms_node = next(n for n in result.nodes if n.object_name == "DBMS_OUTPUT")
    assert dbms_node.is_leaf is True
    assert ("SYS", "DBMS_OUTPUT") not in extractor.deps_direct_calls


# ------------------------------------------------------------------ PL/Scope ausente


def test_object_without_plscope_enters_graph_and_needs_recompile_without_raising():
    deps = {("GESTAO", "NOPLSCOPE"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "NOPLSCOPE", type_="PACKAGE")]}
    plscope = {
        "GESTAO": [
            extract.PlscopeCheckRow(
                owner="GESTAO", name="NOPLSCOPE", type="PACKAGE", plscope_settings="IDENTIFIERS:NONE"
            )
        ]
    }
    extractor = SyntheticExtractor(deps=deps, catalog=catalog, plscope=plscope)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "NOPLSCOPE"))

    node = next(n for n in result.nodes if n.object_name == "NOPLSCOPE")
    assert node.plscope is False
    assert "GESTAO.NOPLSCOPE" in result.needs_recompile


def test_table_without_plscope_row_is_not_flagged_needs_recompile():
    # TABLE nao passa por all_plsql_object_settings -- ausencia de linha de
    # plscope_check nao pode gerar falso-positivo em needs_recompile.
    deps = {("GESTAO", "SOME_TABLE"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "SOME_TABLE", type_="TABLE")]}
    extractor = SyntheticExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "SOME_TABLE"))

    assert result.needs_recompile == []


# ----------------------------------------------------------------------- sinonimo


def test_synonym_without_deps_resolves_to_base_object_and_expands_it():
    deps = {
        ("GESTAO", "SYN1"): [],
        ("GESTAO", "BASE_OBJ"): [_dep("GESTAO", "DOWNSTREAM")],
        ("GESTAO", "DOWNSTREAM"): [],
    }
    catalog = {
        "GESTAO": [
            _cat("GESTAO", "SYN1", type_="SYNONYM"),
            _cat("GESTAO", "BASE_OBJ", type_="PACKAGE"),
            _cat("GESTAO", "DOWNSTREAM", type_="TABLE"),
        ]
    }
    synonyms = {
        ("GESTAO", "SYN1"): [
            extract.SynonymRow(
                synonym_owner="GESTAO", synonym_name="SYN1", base_owner="GESTAO", base_name="BASE_OBJ", db_link=None
            )
        ]
    }
    extractor = SyntheticExtractor(deps=deps, catalog=catalog, synonyms=synonyms)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "SYN1"))

    edge_types = {(e.from_ref, e.to_ref): e.edge_type for e in result.edges}
    assert edge_types.get(("GESTAO.SYN1", "GESTAO.BASE_OBJ")) == "SYNONYM_RESOLVES_TO"
    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert "GESTAO.BASE_OBJ" in node_ids
    assert "GESTAO.DOWNSTREAM" in node_ids  # base object foi expandido normalmente


def test_synonym_chain_ending_in_db_link_becomes_opaque_leaf():
    deps = {("GESTAO", "SYN2"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "SYN2", type_="SYNONYM")]}
    synonyms = {
        ("GESTAO", "SYN2"): [
            extract.SynonymRow(
                synonym_owner="GESTAO", synonym_name="SYN2",
                base_owner="REMOTE", base_name="REMOTE_TAB", db_link="REMOTE_LINK",
            )
        ]
    }
    extractor = SyntheticExtractor(deps=deps, catalog=catalog, synonyms=synonyms)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "SYN2"))

    dblink_edges = [e for e in result.edges if e.edge_type == "SYNONYM_RESOLVES_TO" and e.from_ref == "GESTAO.SYN2"]
    assert dblink_edges
    target_ref = dblink_edges[0].to_ref
    assert target_ref.endswith("@REMOTE_LINK")
    dblink_node = next(n for n in result.nodes if n.object_type == "DATABASE LINK")
    assert dblink_node.is_leaf is True
    assert dblink_node.note is not None and "db_link" in dblink_node.note
    assert dblink_node.note == "db_link:REMOTE_LINK"


# --------------------------------------------------------------------- determinismo


def test_synthetic_run_is_deterministic_across_calls():
    deps = {
        ("GESTAO", "ROOT"): [_dep("GESTAO", "B"), _dep("GESTAO", "A"), _dep("GESTAO", "C")],
        ("GESTAO", "A"): [],
        ("GESTAO", "B"): [],
        ("GESTAO", "C"): [],
    }
    catalog = {"GESTAO": [_cat("GESTAO", n) for n in ("ROOT", "A", "B", "C")]}

    def run():
        extractor = SyntheticExtractor(deps=deps, catalog=catalog)
        return depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT"))

    result1 = run()
    result2 = run()
    assert result1 == result2
    # ordem estavel: dependencias visitadas em ordem alfabetica, nao na
    # ordem bruta devolvida por deps_direct (B, A, C)
    assert [n.object_name for n in result1.nodes] == ["A", "B", "C", "ROOT"]


# --------------------------------------------------------------------- DepRoot dataclass


def test_build_dep_graph_accepts_dataclass_root():
    deps = {("GESTAO", "ROOT"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT")]}
    extractor = SyntheticExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, depgraph.DepRoot(owner="GESTAO", object_name="ROOT"))

    assert len(result.nodes) == 1
    assert result.nodes[0].object_name == "ROOT"
