"""Testes offline (T-02) para plsqlflow/graph.py e plsqlflow/resolve.py.

100% sem banco: carrega tests/fixtures/flow_demo_extract.json (extracao real
gravada do dev, pacote GESTAO.FLOW_DEMO) e monta um Extractor fake a partir
dele para o caso real, mais Extractors sinteticos (dataclasses construidas
direto no teste) para os casos que o FLOW_DEMO nao exercita: cascata FK,
OVERRIDING, max_depth e orcamento de nos.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from plsqlflow import extract, graph, resolve

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "flow_demo_extract.json"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE = load_fixture()


class FlowDemoExtractor:
    """Extractor fake que devolve os dados gravados do FLOW_DEMO real,
    ignorando os parametros (owner, object_name) -- assim como a query real
    de plscope_calls/plscope_statements, que e por objeto compilado, nao por
    subprograma (ver comentario de limitacao em graph.py)."""

    def __init__(self, fixture: Dict[str, Any]):
        self._calls = [extract.PlscopeCallRow(**row) for row in fixture["plscope_calls"]]
        self._statements = [extract.PlscopeStatementRow(**row) for row in fixture["plscope_statements"]]
        self._triggers = [extract.TriggerRow(**row) for row in fixture["triggers_for_tables"]]
        self._fk_cascade = [extract.FkCascadeRow(**row) for row in fixture["fk_cascade"]]
        self._type_hierarchy = [extract.TypeHierarchyRow(**row) for row in fixture["type_hierarchy"]]

    def calls(self, owner: str, object_name: str) -> List[extract.PlscopeCallRow]:
        return list(self._calls)

    def statements(self, owner: str, object_name: str) -> List[extract.PlscopeStatementRow]:
        return list(self._statements)

    def triggers(self, owner: str, table_names: List[str]) -> List[extract.TriggerRow]:
        return list(self._triggers)

    def fk_cascade(self, owner: str, table_name: str) -> List[extract.FkCascadeRow]:
        return list(self._fk_cascade)

    def type_hierarchy(self, owner: str, type_name: str) -> List[extract.TypeHierarchyRow]:
        return list(self._type_hierarchy)


def _flow_demo_root() -> graph.RootTarget:
    root = FIXTURE["root"]
    return graph.RootTarget(owner=root["owner"], object_name=root["object_name"], subprogram=root["subprogram"])


# --------------------------------------------------------------- FLOW_DEMO


def test_proc_a_and_proc_b_appear_as_nodes():
    result = graph.build_graph(FlowDemoExtractor(FIXTURE), _flow_demo_root())
    node_ids = {n.id for n in result.nodes}
    assert "GESTAO.FLOW_DEMO.PROC_A" in node_ids
    assert "GESTAO.FLOW_DEMO.PROC_B" in node_ids


def test_mutual_recursion_marked_and_bfs_terminates():
    result = graph.build_graph(FlowDemoExtractor(FIXTURE), _flow_demo_root())
    proc_a_id = "GESTAO.FLOW_DEMO.PROC_A"
    proc_b_id = "GESTAO.FLOW_DEMO.PROC_B"
    recursion_pairs = {(e.from_id, e.to_id) for e in result.edges if e.kind == "recursion"}
    assert (proc_a_id, proc_b_id) in recursion_pairs or (proc_b_id, proc_a_id) in recursion_pairs
    # sanidade de que o anti-loop funciona: grafo finito, sem explosao
    assert 0 < len(result.nodes) < 100
    assert 0 < len(result.edges) < 500


def test_calc_overload_resolves_two_distinct_nodes_and_edges():
    result = graph.build_graph(FlowDemoExtractor(FIXTURE), _flow_demo_root())
    node_ids = {n.id for n in result.nodes}
    calc1_id = "GESTAO.FLOW_DEMO.CALC_OVERLOAD#1"
    calc2_id = "GESTAO.FLOW_DEMO.CALC_OVERLOAD#2"
    assert calc1_id in node_ids
    assert calc2_id in node_ids
    assert calc1_id != calc2_id

    edges_to_calc1 = [e for e in result.edges if e.to_id == calc1_id and e.kind == "static"]
    edges_to_calc2 = [e for e in result.edges if e.to_id == calc2_id and e.kind == "static"]
    assert edges_to_calc1, "esperava aresta estatica para CALC_OVERLOAD#1 (linha 49, NUMBER)"
    assert edges_to_calc2, "esperava aresta estatica para CALC_OVERLOAD#2 (linha 50, VARCHAR2)"


def test_trigger_from_insert_appears_as_node_and_edge():
    result = graph.build_graph(FlowDemoExtractor(FIXTURE), _flow_demo_root())
    node_ids = {n.id for n in result.nodes}
    trig_id = "GESTAO.TRG_FLOW_DEMO_LOG"
    table_id = "GESTAO.FLOW_DEMO_LOG"
    assert trig_id in node_ids
    assert table_id in node_ids

    trigger_edges = [e for e in result.edges if e.kind == "trigger" and e.to_id == trig_id]
    assert trigger_edges
    assert trigger_edges[0].from_id == table_id
    assert trigger_edges[0].line == 6


# ----------------------------------------------------------------- resolve.py


def test_resolve_signatures_ambiguous_overload_gets_suffix():
    rows = [extract.ResolveTargetRow(**row) for row in FIXTURE["resolve_target"]]
    candidates = resolve.resolve_signatures(rows, subprogram="CALC_OVERLOAD")
    assert len(candidates) == 2
    assert {c.label for c in candidates} == {"CALC_OVERLOAD#1", "CALC_OVERLOAD#2"}
    by_overload = {c.overload: c for c in candidates}
    assert by_overload["1"].arguments[0].data_type == "NUMBER"
    assert by_overload["2"].arguments[0].data_type == "VARCHAR2"


def test_resolve_signatures_unambiguous_has_plain_label():
    rows = [extract.ResolveTargetRow(**row) for row in FIXTURE["resolve_target"]]
    candidates = resolve.resolve_signatures(rows, subprogram="MAIN")
    assert len(candidates) == 1
    assert candidates[0].label == "MAIN"
    assert len(candidates[0].arguments) == 2


def test_resolve_synonym_chain_stops_at_db_link():
    def fetch(owner, name):
        return [
            extract.SynonymRow(
                synonym_owner=owner, synonym_name=name, base_owner="REMOTE", base_name="TAB", db_link="REMOTE_LINK"
            )
        ]

    chain = resolve.resolve_synonym_chain(fetch, "GESTAO", "SYN1")
    assert len(chain) == 1
    assert chain[0].db_link == "REMOTE_LINK"


def test_resolve_synonym_chain_detects_cycle_without_hanging():
    def fetch(owner, name):
        other = "B" if name.upper() == "A" else "A"
        return [
            extract.SynonymRow(synonym_owner=owner, synonym_name=name, base_owner=owner, base_name=other, db_link=None)
        ]

    chain = resolve.resolve_synonym_chain(fetch, "GESTAO", "A", max_iterations=10)
    assert 0 < len(chain) <= 10


# ------------------------------------------------------------- FK cascade (sintetico)


class FkCascadeExtractor:
    def calls(self, owner, object_name):
        return []

    def statements(self, owner, object_name):
        return [
            extract.PlscopeStatementRow(
                line=10, stmt_type="DELETE", sql_id=None, has_into_record="NO",
                text="DELETE FROM PARENT_TBL WHERE id = :b1",
            )
        ]

    def triggers(self, owner, table_names):
        return []

    def fk_cascade(self, owner, table_name):
        if table_name == "PARENT_TBL":
            return [
                extract.FkCascadeRow(
                    child_owner="GESTAO", child_table="CHILD_TBL",
                    constraint_name="FK_CHILD_PARENT", delete_rule="CASCADE",
                )
            ]
        return []

    def type_hierarchy(self, owner, type_name):
        return []


def test_fk_cascade_on_delete_generates_cascade_edge():
    root = graph.RootTarget(owner="GESTAO", object_name="PARENT_PKG", subprogram="DEL_PARENT")
    result = graph.build_graph(FkCascadeExtractor(), root)
    cascade_edges = [e for e in result.edges if e.kind == "cascade"]
    assert cascade_edges
    assert cascade_edges[0].to_id == "GESTAO.CHILD_TBL"
    assert "CASCADE" in (cascade_edges[0].label or "")


# ------------------------------------------------------------- OVERRIDING (sintetico)


class OverrideExtractor:
    def calls(self, owner, object_name):
        return [
            extract.PlscopeCallRow(
                line=5, col=3, called_name="SPEAK", calling_object=object_name,
                decl_owner="GESTAO", decl_object="ANIMAL_T", decl_object_type="TYPE",
                decl_type="MEMBER FUNCTION", decl_line=2,
            )
        ]

    def statements(self, owner, object_name):
        return []

    def triggers(self, owner, table_names):
        return []

    def fk_cascade(self, owner, table_name):
        return []

    def type_hierarchy(self, owner, type_name):
        return [
            extract.TypeHierarchyRow(
                type_name="ANIMAL_T", supertype_name=None, final="NO", instantiable="YES",
                method_name="SPEAK", method_no=1, method_type="MEMBER", overriding=None, inherited=None,
            ),
            extract.TypeHierarchyRow(
                type_name="DOG_T", supertype_name="ANIMAL_T", final="YES", instantiable="YES",
                method_name="SPEAK", method_no=1, method_type="MEMBER", overriding="YES", inherited=None,
            ),
            extract.TypeHierarchyRow(
                type_name="CAT_T", supertype_name="ANIMAL_T", final="YES", instantiable="YES",
                method_name="SPEAK", method_no=1, method_type="MEMBER", overriding="YES", inherited=None,
            ),
        ]


def test_overriding_lists_all_subtypes_as_candidates_without_resolving():
    root = graph.RootTarget(owner="GESTAO", object_name="ZOO_PKG", subprogram="MAKE_NOISE")
    result = graph.build_graph(OverrideExtractor(), root)
    override_edges = [e for e in result.edges if e.kind == "override"]
    assert len(override_edges) >= 2
    targets = {e.to_id for e in override_edges}
    assert "GESTAO.DOG_T.SPEAK" in targets
    assert "GESTAO.CAT_T.SPEAK" in targets
    # nenhuma aresta estatica escolhe um subtipo especifico -- dispatch e runtime
    assert not any(e.kind == "static" and e.to_id in targets for e in result.edges)


# ------------------------------------------------------------- max_depth (sintetico)


class ChainExtractor:
    """Cadeia linear OBJ0 -> OBJ1 -> ... -> OBJ(length), cada objeto com uma
    unica chamada para o proximo -- deliberadamente sem reuso de objeto
    (evita a sobre-aproximacao de graph.py, que reconsidera a lista inteira
    de chamadas por objeto; aqui cada objeto tem exatamente uma chamada)."""

    def __init__(self, length: int):
        self.length = length

    def calls(self, owner, object_name):
        idx = int(object_name[len("OBJ"):])
        if idx >= self.length:
            return []
        return [
            extract.PlscopeCallRow(
                line=1, col=1, called_name="P", calling_object=object_name,
                decl_owner=owner, decl_object="OBJ{}".format(idx + 1),
                decl_object_type="PACKAGE BODY", decl_type="PROCEDURE", decl_line=1,
            )
        ]

    def statements(self, owner, object_name):
        return []

    def triggers(self, owner, table_names):
        return []

    def fk_cascade(self, owner, table_name):
        return []

    def type_hierarchy(self, owner, type_name):
        return []


def test_max_depth_truncates_deep_chain_without_blowing_up():
    root = graph.RootTarget(owner="GESTAO", object_name="OBJ0", subprogram="P")
    result = graph.build_graph(ChainExtractor(length=30), root, max_depth=5)
    assert len(result.nodes) <= 7  # OBJ0..OBJ5 (raiz + 5 niveis), nunca os 30
    assert any(n.truncated for n in result.nodes)
    assert result.stats["depth_reached"] <= 5


# --------------------------------------------------------- orcamento de nos (sintetico)


class BudgetExtractor:
    def __init__(self, width: int):
        self.width = width

    def calls(self, owner, object_name):
        if object_name != "ROOT_OBJ":
            return []
        return [
            extract.PlscopeCallRow(
                line=i, col=1, called_name="LEAF{}".format(i), calling_object=object_name,
                decl_owner=owner, decl_object="LEAF_OBJ{}".format(i),
                decl_object_type="PACKAGE BODY", decl_type="PROCEDURE", decl_line=1,
            )
            for i in range(self.width)
        ]

    def statements(self, owner, object_name):
        return []

    def triggers(self, owner, table_names):
        return []

    def fk_cascade(self, owner, table_name):
        return []

    def type_hierarchy(self, owner, type_name):
        return []


def test_node_budget_collapses_and_reports_what_was_dropped():
    root = graph.RootTarget(owner="GESTAO", object_name="ROOT_OBJ", subprogram="MAIN")
    result = graph.build_graph(BudgetExtractor(width=50), root, node_budget=10)

    assert len(result.nodes) <= 10 + len(result.collapsed)
    assert result.collapsed, "estouro de orcamento deveria gerar ao menos um grupo colapsado"

    created_leaves = sum(1 for n in result.nodes if n.id.startswith("GESTAO.LEAF_OBJ"))
    collapsed_count = sum(g.count for g in result.collapsed)
    assert collapsed_count > 0
    assert created_leaves + collapsed_count == 50, "colapso nao pode ser silencioso: soma tem que bater com o total"
