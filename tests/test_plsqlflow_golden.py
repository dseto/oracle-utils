"""Teste golden offline (T-04) para plsqlflow/report.py e plsqlflow/mermaid.py.

100% sem banco: carrega tests/fixtures/flow_demo_extract.json (mesmo fixture
gravado do dev usado pela T-02/T-03) e monta um Extractor fake que fecha o
contrato graph.Extractor + o metodo `source` que report.py exige. O mermaid
produzido para GESTAO.FLOW_DEMO.MAIN e comparado byte a byte com
tests/fixtures/flow_demo_golden.mmd.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from plsqlflow import dynsql, extract, report
from plsqlflow.graph import RootTarget

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "flow_demo_extract.json"
GOLDEN_PATH = REPO / "tests" / "fixtures" / "flow_demo_golden.mmd"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE = load_fixture()


class FlowDemoReportExtractor:
    """Extractor fake para report.py. calls/triggers/fk_cascade/type_hierarchy
    ignoram (owner, object_name) como o fake da T-02 (fixture so tem um
    objeto compilado) -- mas statements/source PRECISAM filtrar por
    (owner, object_name): diferente de graph.py, report.py consulta
    statements()/source() para TODO objeto que aparece como no no grafo
    (tabelas, triggers, a folha SYS.STANDARD), nao so o objeto raiz. Sem o
    filtro, o SQL dinamico do pacote vazaria para esses outros nos."""

    def __init__(self, fixture: Dict[str, Any]):
        self._calls = [extract.PlscopeCallRow(**row) for row in fixture["plscope_calls"]]
        self._statements = [extract.PlscopeStatementRow(**row) for row in fixture["plscope_statements"]]
        self._triggers = [extract.TriggerRow(**row) for row in fixture["triggers_for_tables"]]
        self._fk_cascade = [extract.FkCascadeRow(**row) for row in fixture["fk_cascade"]]
        self._type_hierarchy = [extract.TypeHierarchyRow(**row) for row in fixture["type_hierarchy"]]
        self._source_lines = fixture["fetch_source"]["PACKAGE BODY"]
        self._owner = fixture["root"]["owner"]
        self._object_name = fixture["root"]["object_name"]

    def _is_root_object(self, owner: str, object_name: str) -> bool:
        return owner == self._owner and object_name == self._object_name

    def calls(self, owner: str, object_name: str) -> List[extract.PlscopeCallRow]:
        return list(self._calls)

    def statements(self, owner: str, object_name: str) -> List[extract.PlscopeStatementRow]:
        if self._is_root_object(owner, object_name):
            return list(self._statements)
        return []

    def triggers(self, owner: str, table_names: List[str]) -> List[extract.TriggerRow]:
        return list(self._triggers)

    def fk_cascade(self, owner: str, table_name: str) -> List[extract.FkCascadeRow]:
        return list(self._fk_cascade)

    def type_hierarchy(self, owner: str, type_name: str) -> List[extract.TypeHierarchyRow]:
        return list(self._type_hierarchy)

    def source(self, owner: str, object_name: str) -> List[dynsql.FetchSourceRow]:
        if self._is_root_object(owner, object_name):
            return dynsql.rows_from_lines(self._source_lines, type_="PACKAGE BODY")
        return []


def _flow_demo_root() -> RootTarget:
    root = FIXTURE["root"]
    return RootTarget(owner=root["owner"], object_name=root["object_name"], subprogram=root["subprogram"])


def _build_flow_demo_report() -> Dict[str, Any]:
    return report.build_report(FlowDemoReportExtractor(FIXTURE), _flow_demo_root())


def test_mermaid_matches_golden_file_byte_for_byte():
    result = _build_flow_demo_report()
    expected = GOLDEN_PATH.read_text(encoding="utf-8")
    assert result["mermaid"] == expected


def test_blind_spots_has_exactly_one_unresolved_dynsql_at_line_31():
    result = _build_flow_demo_report()
    dynsql_spots = [b for b in result["blind_spots"] if b["type"] == "dynsql_unresolved"]
    assert len(dynsql_spots) == 1
    assert dynsql_spots[0]["at"] == "GESTAO.FLOW_DEMO:31"
    assert not any(":28" in b["at"] for b in dynsql_spots)


def test_pct_plscope_matches_documented_formula():
    result = _build_flow_demo_report()
    nodes = result["nodes"]
    counted = [
        n for n in nodes if n["owner"] and n["object_name"] and n["confidence"] in ("compiler", "lexical", "heuristic")
    ]
    compiler_count = sum(1 for n in counted if n["confidence"] == "compiler")
    expected_pct = round(100.0 * compiler_count / len(counted), 1) if counted else 0.0
    assert result["stats"]["pct_plscope"] == expected_pct
    assert 0 <= result["stats"]["pct_plscope"] <= 100


def test_build_report_is_deterministic_across_runs():
    first = json.dumps(_build_flow_demo_report(), sort_keys=True)
    second = json.dumps(_build_flow_demo_report(), sort_keys=True)
    assert first == second
