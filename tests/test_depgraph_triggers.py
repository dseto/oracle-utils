"""Testes offline (T-04, Entrega 1) para a fase 4 (triggers re-injetados)
de plsqlflow/depgraph.py -- `add_trigger_phase`.

100% sem banco. Dois blocos:
- Bloco FLOW_DEMO real: reusa tests/fixtures/depgraph_extract.json (BFS,
  T-02) + tests/fixtures/flow_demo_extract.json (plscope_statements/source
  para calcular a aresta WRITE real via depgraph_enrich.py, T-03 -- REUSE,
  nao reimplementado aqui) para exercitar o caso real documentado na nota
  da fixture: TRG_FLOW_DEMO_LOG dispara em FLOW_DEMO_LOG e depende de
  FLOW_DEMO_AUDIT (re-injecao na BFS).
- Bloco sintetico: anti-loop tabela->trigger->mesma tabela, trigger
  compartilhado por duas tabelas (nao re-expande), trigger desabilitado
  (entra marcado), caps respeitados na fase 4.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Tuple

from plsqlflow import depgraph, depgraph_enrich, dynsql, extract

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "depgraph_extract.json"
FLOW_FIXTURE_PATH = REPO / "tests" / "fixtures" / "flow_demo_extract.json"


def _kw(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k.lower(): v for k, v in row.items()}


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def load_flow_fixture() -> Dict[str, Any]:
    return json.loads(FLOW_FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE = load_fixture()
FLOW_FIXTURE = load_flow_fixture()


# --------------------------------------------------------------- fake real (FLOW_DEMO)


class FlowDemoFullExtractor:
    """Extractor fake da fixture real FLOW_DEMO, estendido com
    triggers()/tab_columns() (T-04) alem de deps_direct/object_catalog/
    plscope_check/synonym (T-02)."""

    def __init__(self, fixture: Dict[str, Any]):
        self._catalog = [extract.ObjectCatalogRow(**_kw(row)) for row in fixture["object_catalog"]]
        self._deps = fixture["deps_direct"]
        self._triggers = [extract.TriggerRow(**_kw(row)) for row in fixture["triggers_for_tables"]]
        self._tab_columns = [extract.TabColumnRow(**_kw(row)) for row in fixture["tab_columns"]]
        self.deps_direct_calls: List[Tuple[str, str]] = []
        self.triggers_calls: List[Tuple[str, Tuple[str, ...]]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        self.deps_direct_calls.append((owner.upper(), name.upper()))
        key = "{}.{}".format(owner.upper(), name.upper())
        return [extract.DepsDirectRow(**_kw(r)) for r in self._deps.get(key, [])]

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return [r for r in self._catalog if r.owner.upper() == owner.upper()]

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
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
            extract.PlscopeCheckRow(
                owner="GESTAO", name="TRG_FLOW_DEMO_LOG", type="TRIGGER",
                plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL",
            ),
        ]

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return []

    def triggers(self, owner: str, table_names) -> List[extract.TriggerRow]:
        self.triggers_calls.append((owner.upper(), tuple(sorted(table_names))))
        names_u = {n.upper() for n in table_names}
        return [
            t for t in self._triggers
            if t.table_owner.upper() == owner.upper() and t.table_name.upper() in names_u
        ]

    def tab_columns(self, owner: str, table_names) -> List[extract.TabColumnRow]:
        names_u = {n.upper() for n in table_names}
        return [
            c for c in self._tab_columns
            if c.owner.upper() == owner.upper() and c.table_name.upper() in names_u
        ]


def _write_edges_for_flow_demo() -> List[depgraph.DepEdge]:
    """Aresta WRITE real (T-03, REUSE de depgraph_enrich.py): INSERT INTO
    flow_demo_log na linha 6 do PACKAGE BODY -- e essa aresta que a fase 4
    usa para descobrir que FLOW_DEMO_LOG precisa ser consultada por
    triggers."""
    statements = [extract.PlscopeStatementRow(**row) for row in FLOW_FIXTURE["plscope_statements"]]
    source = dynsql.rows_from_lines(FLOW_FIXTURE["fetch_source"]["PACKAGE BODY"])
    return depgraph_enrich.table_edges_from_statements(statements, source, "GESTAO", "FLOW_DEMO")


def _build_flow_demo_with_triggers():
    extractor = FlowDemoFullExtractor(FIXTURE)
    bfs_result = depgraph.build_dep_graph(extractor, ("GESTAO", "FLOW_DEMO"))
    write_edges = _write_edges_for_flow_demo()
    merged = replace(bfs_result, edges=list(bfs_result.edges) + write_edges)
    final = depgraph.add_trigger_phase(extractor, merged)
    return extractor, final


def test_trigger_becomes_its_own_node_enabled():
    _, result = _build_flow_demo_with_triggers()
    trigger = next(n for n in result.nodes if n.object_name == "TRG_FLOW_DEMO_LOG")
    assert trigger.owner == "GESTAO"
    assert trigger.object_type == "TRIGGER"
    assert trigger.trigger_status == "ENABLED"
    assert trigger.note is None


def test_trigger_fires_edge_from_table_to_trigger():
    _, result = _build_flow_demo_with_triggers()
    fires = [
        e for e in result.edges
        if e.edge_type == "TRIGGER_FIRES"
        and e.from_ref == "GESTAO.FLOW_DEMO_LOG"
        and e.to_ref == "GESTAO.TRG_FLOW_DEMO_LOG"
    ]
    assert len(fires) == 1
    assert fires[0].op == "INSERT"


def test_flow_demo_audit_enters_via_trigger_reinjection():
    # A propria fixture documenta isso em _expected_bfs: FLOW_DEMO_AUDIT so
    # entra no grafo porque o trigger foi re-injetado na BFS e
    # deps_direct(GESTAO.TRG_FLOW_DEMO_LOG) aponta para FLOW_DEMO_AUDIT.
    _, result = _build_flow_demo_with_triggers()
    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    reference = set(FIXTURE["_expected_bfs"]["nodes"])
    assert node_ids == reference
    assert "GESTAO.FLOW_DEMO_AUDIT" in node_ids
    audit_edge = next(
        e for e in result.edges
        if e.from_ref == "GESTAO.TRG_FLOW_DEMO_LOG" and e.to_ref == "GESTAO.FLOW_DEMO_AUDIT"
    )
    assert audit_edge.edge_type == "CALL"


def test_flow_demo_table_nodes_get_columns_populated():
    _, result = _build_flow_demo_with_triggers()
    log_table = next(n for n in result.nodes if n.object_name == "FLOW_DEMO_LOG")
    audit_table = next(n for n in result.nodes if n.object_name == "FLOW_DEMO_AUDIT")
    assert log_table.columns is not None
    assert [c.column_name for c in log_table.columns] == ["ID", "MSG", "CRIADO_EM"]
    assert audit_table.columns is not None
    assert [c.column_name for c in audit_table.columns] == ["ID", "LOG_ID", "ACAO", "CRIADO_EM"]


def test_trigger_phase_is_deterministic_across_runs():
    _, first = _build_flow_demo_with_triggers()
    _, second = _build_flow_demo_with_triggers()
    assert first == second


def test_trigger_query_grouped_once_per_owner_table_list():
    extractor, _ = _build_flow_demo_with_triggers()
    assert extractor.triggers_calls == [("GESTAO", ("FLOW_DEMO_LOG",))]


# ---------------------------------------------------------------- sintetico


class SyntheticFullExtractor:
    def __init__(self, deps=None, catalog=None, plscope=None, triggers=None, tab_columns=None):
        self.deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = deps or {}
        self.catalog: Dict[str, List[extract.ObjectCatalogRow]] = catalog or {}
        self.plscope: Dict[str, List[extract.PlscopeCheckRow]] = plscope or {}
        self.trigger_rows: Dict[Tuple[str, ...], List[extract.TriggerRow]] = triggers or {}
        self.tab_columns_rows: Dict[str, List[extract.TabColumnRow]] = tab_columns or {}
        self.deps_direct_calls: List[Tuple[str, str]] = []
        self.triggers_calls: List[Tuple[str, Tuple[str, ...]]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        key = (owner.upper(), name.upper())
        self.deps_direct_calls.append(key)
        return list(self.deps.get(key, []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return []

    def triggers(self, owner: str, table_names) -> List[extract.TriggerRow]:
        key = (owner.upper(), tuple(sorted(n.upper() for n in table_names)))
        self.triggers_calls.append(key)
        return list(self.trigger_rows.get(key, []))

    def tab_columns(self, owner: str, table_names) -> List[extract.TabColumnRow]:
        return list(self.tab_columns_rows.get(owner.upper(), []))


def _table_node(owner="GESTAO", name="T") -> depgraph.DepNode:
    return depgraph.DepNode(owner=owner, object_name=name, object_type="TABLE", status="VALID", plscope=False)


def _base_result(nodes, edges) -> depgraph.DepGraphResult:
    return depgraph.DepGraphResult(
        nodes=nodes, edges=edges, needs_recompile=[], truncated=False,
        truncation_reason=None, not_expanded=[], stats={},
    )


def _write_edge(table_ref="GESTAO.T", from_ref="GESTAO.OBJ", line=5) -> depgraph.DepEdge:
    return depgraph.DepEdge(from_ref=from_ref, to_ref=table_ref, edge_type="WRITE", line=line, op="INSERT")


def _cat(owner, name, type_, status="VALID") -> extract.ObjectCatalogRow:
    return extract.ObjectCatalogRow(owner=owner, object_name=name, object_type=type_, status=status, last_ddl_time="2026-08-15 10:00:00")


def _trigger_row(owner, table, trigger, status="ENABLED", event="INSERT") -> extract.TriggerRow:
    return extract.TriggerRow(
        table_owner=owner, table_name=table, trigger_name=trigger, trigger_type="AFTER EACH ROW",
        triggering_event=event, when_clause=None, status=status,
    )


def test_table_trigger_same_table_does_not_loop_and_dedupes_edge():
    base = _base_result([_table_node()], [_write_edge()])
    extractor = SyntheticFullExtractor(
        deps={("GESTAO", "TRG_T"): [
            extract.DepsDirectRow(referenced_owner="GESTAO", referenced_name="T", referenced_type="TABLE", dependency_type="HARD")
        ]},
        catalog={"GESTAO": [_cat("GESTAO", "T", "TABLE"), _cat("GESTAO", "TRG_T", "TRIGGER")]},
        triggers={("GESTAO", ("T",)): [_trigger_row("GESTAO", "T", "TRG_T")]},
    )

    result = depgraph.add_trigger_phase(extractor, base)

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {"GESTAO.T", "GESTAO.TRG_T"}
    call_edges = [e for e in result.edges if e.edge_type == "CALL" and e.from_ref == "GESTAO.TRG_T" and e.to_ref == "GESTAO.T"]
    assert len(call_edges) == 1
    fires_edges = [e for e in result.edges if e.edge_type == "TRIGGER_FIRES"]
    assert len(fires_edges) == 1
    # deps_direct do trigger foi chamado exatamente uma vez -- sem loop
    assert extractor.deps_direct_calls == [("GESTAO", "TRG_T")]


def test_trigger_shared_by_two_tables_is_not_reexpanded():
    base = _base_result(
        [_table_node(name="T1"), _table_node(name="T2")],
        [_write_edge(table_ref="GESTAO.T1"), _write_edge(table_ref="GESTAO.T2")],
    )
    extractor = SyntheticFullExtractor(
        deps={("GESTAO", "TRG_SHARED"): []},
        catalog={"GESTAO": [
            _cat("GESTAO", "T1", "TABLE"), _cat("GESTAO", "T2", "TABLE"), _cat("GESTAO", "TRG_SHARED", "TRIGGER"),
        ]},
        triggers={("GESTAO", ("T1", "T2")): [
            _trigger_row("GESTAO", "T1", "TRG_SHARED"),
            _trigger_row("GESTAO", "T2", "TRG_SHARED"),
        ]},
    )

    result = depgraph.add_trigger_phase(extractor, base)

    trigger_nodes = [n for n in result.nodes if n.object_name == "TRG_SHARED"]
    assert len(trigger_nodes) == 1
    fires_edges = {(e.from_ref, e.to_ref) for e in result.edges if e.edge_type == "TRIGGER_FIRES"}
    assert fires_edges == {("GESTAO.T1", "GESTAO.TRG_SHARED"), ("GESTAO.T2", "GESTAO.TRG_SHARED")}
    # trigger so foi enfileirado/expandido uma vez
    assert extractor.deps_direct_calls == [("GESTAO", "TRG_SHARED")]


def test_disabled_trigger_enters_graph_marked():
    base = _base_result([_table_node()], [_write_edge()])
    extractor = SyntheticFullExtractor(
        deps={("GESTAO", "TRG_OFF"): []},
        catalog={"GESTAO": [_cat("GESTAO", "T", "TABLE"), _cat("GESTAO", "TRG_OFF", "TRIGGER")]},
        triggers={("GESTAO", ("T",)): [_trigger_row("GESTAO", "T", "TRG_OFF", status="DISABLED")]},
    )

    result = depgraph.add_trigger_phase(extractor, base)

    trigger = next(n for n in result.nodes if n.object_name == "TRG_OFF")
    assert trigger.trigger_status == "DISABLED"
    assert trigger.note == "trigger_status:DISABLED"
    fires = [e for e in result.edges if e.edge_type == "TRIGGER_FIRES" and e.to_ref == "GESTAO.TRG_OFF"]
    assert len(fires) == 1


def test_write_edge_with_unknown_target_is_ignored_gracefully():
    base = _base_result([], [depgraph.DepEdge(from_ref="GESTAO.OBJ", to_ref="?", edge_type="WRITE", line=1, op="INSERT")])
    extractor = SyntheticFullExtractor()

    result = depgraph.add_trigger_phase(extractor, base)

    assert result.nodes == []
    assert not any(e.edge_type == "TRIGGER_FIRES" for e in result.edges)
    assert extractor.triggers_calls == []


def test_trigger_phase_respects_max_objects_cap():
    base = _base_result([_table_node()], [_write_edge()])
    extractor = SyntheticFullExtractor(
        deps={("GESTAO", "TRG_T"): []},
        catalog={"GESTAO": [_cat("GESTAO", "T", "TABLE"), _cat("GESTAO", "TRG_T", "TRIGGER")]},
        triggers={("GESTAO", ("T",)): [_trigger_row("GESTAO", "T", "TRG_T")]},
    )

    result = depgraph.add_trigger_phase(extractor, base, max_objects=1)

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {"GESTAO.T"}
    assert "GESTAO.TRG_T" not in node_ids
    assert result.truncated is True
    assert result.truncation_reason is not None
    assert "max_objects" in result.truncation_reason
    assert "GESTAO.TRG_T" in result.not_expanded


def test_trigger_phase_respects_max_depth_cap():
    base = _base_result([_table_node()], [_write_edge()])
    extractor = SyntheticFullExtractor(
        deps={
            ("GESTAO", "TRG_T"): [
                extract.DepsDirectRow(referenced_owner="GESTAO", referenced_name="DEEP1", referenced_type="PACKAGE", dependency_type="HARD")
            ],
            ("GESTAO", "DEEP1"): [
                extract.DepsDirectRow(referenced_owner="GESTAO", referenced_name="DEEP2", referenced_type="PACKAGE", dependency_type="HARD")
            ],
        },
        catalog={"GESTAO": [
            _cat("GESTAO", "T", "TABLE"), _cat("GESTAO", "TRG_T", "TRIGGER"),
            _cat("GESTAO", "DEEP1", "PACKAGE"), _cat("GESTAO", "DEEP2", "PACKAGE"),
        ]},
        triggers={("GESTAO", ("T",)): [_trigger_row("GESTAO", "T", "TRG_T")]},
    )

    result = depgraph.add_trigger_phase(extractor, base, max_depth=1)

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    # TRG_T (depth 0), DEEP1 (depth 1, criado mas nao expandido)
    assert "GESTAO.TRG_T" in node_ids
    assert "GESTAO.DEEP1" in node_ids
    assert "GESTAO.DEEP2" not in node_ids
    assert result.truncated is True
    assert "max_depth" in result.truncation_reason


def test_add_trigger_phase_is_deterministic():
    base = _base_result([_table_node()], [_write_edge()])

    def run():
        extractor = SyntheticFullExtractor(
            deps={("GESTAO", "TRG_T"): []},
            catalog={"GESTAO": [_cat("GESTAO", "T", "TABLE"), _cat("GESTAO", "TRG_T", "TRIGGER")]},
            triggers={("GESTAO", ("T",)): [_trigger_row("GESTAO", "T", "TRG_T")]},
        )
        return depgraph.add_trigger_phase(extractor, base)

    assert run() == run()
