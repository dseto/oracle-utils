"""Testes offline (T-04, Entrega 2) para plsqlflow/depgraph_render.py --
golden test COMPARANDO ARQUIVOS EM DISCO (nao string em memoria).

100% sem banco: monta o `DepGraphResult` completo do pacote GESTAO.FLOW_DEMO
(BFS T-02 + READ/WRITE/DYNAMIC_SQL T-03 via depgraph_enrich.py, REUSE, nao
reimplementado -- + triggers T-04 via depgraph.add_trigger_phase), grava em
`tmp_path` via `render_graph` e compara byte a byte com
tests/fixtures/depgraph_golden/.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

import pytest

from plsqlflow import depgraph, depgraph_enrich, depgraph_render, dynsql, extract

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "depgraph_extract.json"
FLOW_FIXTURE_PATH = REPO / "tests" / "fixtures" / "flow_demo_extract.json"
GOLDEN_DIR = REPO / "tests" / "fixtures" / "depgraph_golden"


def _kw(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k.lower(): v for k, v in row.items()}


FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
FLOW_FIXTURE = json.loads(FLOW_FIXTURE_PATH.read_text(encoding="utf-8"))


class FlowDemoFullExtractor:
    """Mesmo fake de tests/test_depgraph_triggers.py -- duplicado aqui de
    proposito (convencao do repo: cada arquivo de teste e autocontido, ver
    tests/test_depgraph_bfs.py vs tests/test_depgraph_enrich.py)."""

    def __init__(self, fixture: Dict[str, Any]):
        self._catalog = [extract.ObjectCatalogRow(**_kw(row)) for row in fixture["object_catalog"]]
        self._deps = fixture["deps_direct"]
        self._triggers = [extract.TriggerRow(**_kw(row)) for row in fixture["triggers_for_tables"]]
        self._tab_columns = [extract.TabColumnRow(**_kw(row)) for row in fixture["tab_columns"]]

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
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


META_PARAMS = {
    "root_ref": "GESTAO.FLOW_DEMO",
    "stop_schemas": ["SYS", "SYSTEM"],
    "max_objects": 500,
    "max_depth": 20,
    "dynamic_window": 30,
}


def build_full_flow_demo_result() -> depgraph.DepGraphResult:
    """Pipeline completo (T-02 + T-03 reusado + T-04) para GESTAO.FLOW_DEMO,
    100% offline a partir das fixtures reais ja usadas por T-02/T-03/T-04."""
    extractor = FlowDemoFullExtractor(FIXTURE)
    bfs_result = depgraph.build_dep_graph(extractor, ("GESTAO", "FLOW_DEMO"))

    statements = [extract.PlscopeStatementRow(**row) for row in FLOW_FIXTURE["plscope_statements"]]
    source = dynsql.rows_from_lines(FLOW_FIXTURE["fetch_source"]["PACKAGE BODY"])

    write_edges = depgraph_enrich.table_edges_from_statements(statements, source, "GESTAO", "FLOW_DEMO")
    dyn_findings = depgraph_enrich.dynamic_sql_findings(
        statements, source, catalog_names=["FLOW_DEMO_LOG"], owner="GESTAO", object_name="FLOW_DEMO"
    )
    dyn_edges = [e for f in dyn_findings for e in f.edges]
    snippets = {f.snippet_ref: f.snippet for f in dyn_findings}

    merged_edges = list(bfs_result.edges) + write_edges + dyn_edges
    merged = replace(bfs_result, edges=merged_edges, snippets=snippets)

    return depgraph.add_trigger_phase(extractor, merged)


@pytest.fixture(scope="module")
def full_result() -> depgraph.DepGraphResult:
    return build_full_flow_demo_result()


def _tree(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


# ------------------------------------------------------------------ golden


def test_render_matches_golden_files_byte_for_byte(full_result, tmp_path):
    out_dir = tmp_path / "GESTAO.FLOW_DEMO"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)

    generated = _tree(out_dir)
    golden = _tree(GOLDEN_DIR)

    generated_rel = [p.relative_to(out_dir).as_posix() for p in generated]
    golden_rel = [p.relative_to(GOLDEN_DIR).as_posix() for p in golden]
    assert generated_rel == golden_rel, "arvore gerada difere da arvore golden"

    for rel in generated_rel:
        generated_bytes = (out_dir / rel).read_bytes()
        golden_bytes = (GOLDEN_DIR / rel).read_bytes()
        assert generated_bytes == golden_bytes, "arquivo {} difere byte a byte do golden".format(rel)


# --------------------------------------------------------------- idempotencia


def test_render_twice_produces_identical_bytes(full_result, tmp_path):
    out_dir = tmp_path / "run"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    first = {p.relative_to(out_dir).as_posix(): p.read_bytes() for p in _tree(out_dir)}

    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    second = {p.relative_to(out_dir).as_posix(): p.read_bytes() for p in _tree(out_dir)}

    assert first == second


def test_render_removes_stale_node_files_between_runs(tmp_path):
    out_dir = tmp_path / "shrink"
    big = depgraph.DepGraphResult(
        nodes=[
            depgraph.DepNode(owner="GESTAO", object_name="A", object_type="TABLE", status="VALID", plscope=False),
            depgraph.DepNode(owner="GESTAO", object_name="B", object_type="TABLE", status="VALID", plscope=False),
        ],
        edges=[], needs_recompile=[], truncated=False, truncation_reason=None, not_expanded=[], stats={},
    )
    depgraph_render.render_graph(big, out_dir, {})
    assert (out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "A")).exists()
    assert (out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "B")).exists()

    small = replace(big, nodes=big.nodes[:1])
    depgraph_render.render_graph(small, out_dir, {})

    assert (out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "A")).exists()
    assert not (out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "B")).exists()


# ------------------------------------------------------------------- \r\n


def test_no_generated_file_contains_crlf(full_result, tmp_path):
    out_dir = tmp_path / "crlf"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    for path in _tree(out_dir):
        data = path.read_bytes()
        assert b"\r\n" not in data, "{} contem CRLF".format(path)


# --------------------------------------------------------------- edges.jsonl


def test_edges_jsonl_is_sorted_and_parseable_line_by_line(full_result, tmp_path):
    out_dir = tmp_path / "edges"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    text = (out_dir / "edges.jsonl").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines, "edges.jsonl vazio"

    parsed = [json.loads(line) for line in lines]
    keys = [(e["from_ref"], e["to_ref"], e["edge_type"], e["line"] if e["line"] is not None else -1) for e in parsed]
    assert keys == sorted(keys)

    # todo campo documentado presente em toda linha (mesmo que None)
    for entry in parsed:
        for field_name in ("from_ref", "to_ref", "edge_type", "line", "op", "cols", "dynamic", "confidence", "context", "snippet_ref"):
            assert field_name in entry


def test_edges_jsonl_has_no_trailing_blank_line(full_result, tmp_path):
    out_dir = tmp_path / "edges2"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    raw = (out_dir / "edges.jsonl").read_bytes()
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")


# -------------------------------------------------------------------- INDEX.md


def test_index_md_has_pontos_cegos_section(full_result, tmp_path):
    out_dir = tmp_path / "index"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## PONTOS CEGOS" in text
    # FLOW_DEMO tem dynsql partial/opaque dependendo do catalogo -- linha
    # 31 nao resolve literal, e classificada partial (catalogo tem
    # FLOW_DEMO_LOG) -- tem que aparecer nos pontos cegos.
    assert "GESTAO.FLOW_DEMO L31" in text


def test_index_md_lists_transitive_closure(full_result, tmp_path):
    out_dir = tmp_path / "index2"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## Fechamento transitivo" in text
    for ref in ("GESTAO.FLOW_DEMO", "GESTAO.FLOW_DEMO_LOG", "GESTAO.FLOW_DEMO_AUDIT", "GESTAO.TRG_FLOW_DEMO_LOG", "SYS.STANDARD"):
        assert "- {} [".format(ref) in text


# --------------------------------------------------------------------- meta.json


def test_meta_json_has_no_timestamp_and_stable_chain_hash(full_result, tmp_path):
    out_dir = tmp_path / "meta"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    payload = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))

    assert "timestamp" not in payload
    assert "generated_at" not in payload
    assert "chain_hash" in payload and payload["chain_hash"]
    assert payload["extractor_version"]
    assert payload["params"] == META_PARAMS

    # gerar de novo -- mesmo hash (nao depende de relogio)
    out_dir2 = tmp_path / "meta2"
    depgraph_render.render_graph(full_result, out_dir2, META_PARAMS)
    payload2 = json.loads((out_dir2 / "meta.json").read_text(encoding="utf-8"))
    assert payload2["chain_hash"] == payload["chain_hash"]
    assert (out_dir / "meta.json").read_bytes() == (out_dir2 / "meta.json").read_bytes()


def test_chain_hash_changes_when_last_ddl_time_changes(full_result):
    changed_nodes = [
        replace(n, last_ddl_time="1999-01-01 00:00:00") if n.object_name == "FLOW_DEMO" else n
        for n in full_result.nodes
    ]
    original_hash = depgraph_render.compute_chain_hash(full_result.nodes)
    changed_hash = depgraph_render.compute_chain_hash(changed_nodes)
    assert original_hash != changed_hash


# -------------------------------------------------------------------- recompile.sql


def test_recompile_sql_only_exists_when_needs_recompile_present(tmp_path):
    result_without = depgraph.DepGraphResult(
        nodes=[depgraph.DepNode(owner="GESTAO", object_name="X", object_type="TABLE", status="VALID", plscope=False)],
        edges=[], needs_recompile=[], truncated=False, truncation_reason=None, not_expanded=[], stats={},
    )
    out_dir = tmp_path / "norecompile"
    depgraph_render.render_graph(result_without, out_dir, {})
    assert not (out_dir / "recompile.sql").exists()

    result_with = depgraph.DepGraphResult(
        nodes=[
            depgraph.DepNode(owner="GESTAO", object_name="PKG", object_type="PACKAGE", status="VALID", plscope=False),
        ],
        edges=[], needs_recompile=["GESTAO.PKG"], truncated=False, truncation_reason=None, not_expanded=[], stats={},
    )
    out_dir2 = tmp_path / "withrecompile"
    depgraph_render.render_graph(result_with, out_dir2, {})
    recompile_path = out_dir2 / "recompile.sql"
    assert recompile_path.exists()
    text = recompile_path.read_text(encoding="ascii")  # levanta UnicodeDecodeError se nao for ASCII puro
    assert "GESTAO.PKG" in text
    assert "PLSCOPE_SETTINGS" in text
    assert "pipeline NUNCA roda isto" in text


def test_recompile_sql_removed_when_graph_no_longer_needs_it(tmp_path):
    result_with = depgraph.DepGraphResult(
        nodes=[depgraph.DepNode(owner="GESTAO", object_name="PKG", object_type="PACKAGE", status="VALID", plscope=False)],
        edges=[], needs_recompile=["GESTAO.PKG"], truncated=False, truncation_reason=None, not_expanded=[], stats={},
    )
    out_dir = tmp_path / "toggle"
    depgraph_render.render_graph(result_with, out_dir, {})
    assert (out_dir / "recompile.sql").exists()

    result_without = replace(result_with, needs_recompile=[])
    depgraph_render.render_graph(result_without, out_dir, {})
    assert not (out_dir / "recompile.sql").exists()


# --------------------------------------------------------------------- nodes/*.md


def test_node_md_section_order_is_fixed(full_result, tmp_path):
    out_dir = tmp_path / "nodemd"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    path = out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "FLOW_DEMO")
    text = path.read_text(encoding="utf-8")

    assert text.startswith("# GESTAO.FLOW_DEMO\n")
    order = ["## Chama (outbound)", "## Chamado por (inbound)", "## Tabelas acessadas", "## Colunas", "## Triggers ativados", "## SQL Dinâmico"]
    present = [h for h in order if h in text]
    positions = [text.index(h) for h in present]
    assert positions == sorted(positions)


def test_node_md_filename_sanitizes_special_characters():
    assert depgraph_render.node_filename("GESTAO", "FOO$BAR#BAZ") == "GESTAO.FOO_BAR_BAZ.md"


def test_table_node_md_has_columns_and_inbound_write(full_result, tmp_path):
    out_dir = tmp_path / "tablenode"
    depgraph_render.render_graph(full_result, out_dir, META_PARAMS)
    path = out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "FLOW_DEMO_LOG")
    text = path.read_text(encoding="utf-8")

    assert "## Colunas" in text
    assert "- MSG VARCHAR2(200) NULL" in text
    assert "## Tabelas acessadas" in text
    assert "<- GESTAO.FLOW_DEMO" in text
    assert "## Triggers ativados" in text
    assert "GESTAO.TRG_FLOW_DEMO_LOG" in text
