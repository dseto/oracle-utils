"""Testes offline (T-08, contrato depgraph-granular) para
plsqlflow/procgraph_render.py -- saida em disco no grao SUBPROGRAMA e a
secao COBERTURA como obrigacao de prova (docs/plano-depgraph-granular.md,
secao 6).

Nenhum destes casos toca rede/banco: reusa os fakes ja existentes de
tests/test_procgraph_bfs.py (fixture real de GESTAO.FLOW_DEMO, ciclo
inter-package PKG_M1/PKG_M2) e tests/test_procgraph_fallback.py (cadeia
A->B(sem PL/Scope)->C, needs_recompile). Nenhum arquivo novo de fixture:
os casos de "soma quebrada" (o cerne da prova do T-08) constroem um
`ProcGraphResult` corrompido a mao -- e o unico jeito de provar que
`build_coverage`/`render_graph` levantam em vez de "consertar" o numero.
"""
from __future__ import annotations

import json

import pytest

from plsqlflow.procgraph import ProcEdge, ProcGraphResult, ProcNode, build_proc_graph
from plsqlflow.procgraph_render import (
    CoverageError,
    build_coverage,
    capabilities_from_extractor,
    node_filename,
    node_ref,
    render_graph,
)
from tests.test_procgraph_bfs import FakeExtractor, _load_fixture
from tests.test_procgraph_fallback import FakeDepExtractor, FakeProcExtractor


def _flow_demo_result() -> ProcGraphResult:
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    return build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))


def _inter_package_cycle_result() -> ProcGraphResult:
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    return build_proc_graph(extractor, ("GESTAO", "PKG_M1", "P1"))


def _fallback_result() -> ProcGraphResult:
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()
    return build_proc_graph(proc_extractor, ("GESTAO", "A", "P1"), dep_extractor=dep_extractor)


def _empty_result() -> ProcGraphResult:
    return ProcGraphResult(
        nodes=[],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={"nodes": 0, "edges": 0},
    )


FULL_CAPABILITIES = {"resolve_owner": True, "object_wrapped": True, "triggers": True}


# --------------------------------------------------------------------------
# COBERTURA -- caso normal fecha
# --------------------------------------------------------------------------


def test_coverage_sums_close_on_golden_fixture():
    result = _flow_demo_result()

    coverage = build_coverage(result, FULL_CAPABILITIES)

    assert coverage.nodes_subprogram + coverage.nodes_object + coverage.nodes_unresolved == coverage.nodes_total
    assert coverage.calls_subprogram + coverage.calls_init_spec + coverage.calls_unattributed == coverage.calls_total
    assert (
        coverage.statements_subprogram + coverage.statements_init_spec + coverage.statements_unattributed
        == coverage.statements_total
    )
    assert coverage.nodes_total == len(result.nodes)


def test_coverage_sums_close_on_fallback_fixture():
    # A(fino) -> B(sem PL/Scope, fallback) -> C(fino, alcancado via
    # ALL_DEPENDENCIES) -- exercita os tres graos ao mesmo tempo.
    result = _fallback_result()

    coverage = build_coverage(result, FULL_CAPABILITIES)

    assert coverage.nodes_subprogram + coverage.nodes_object + coverage.nodes_unresolved == coverage.nodes_total
    assert coverage.nodes_object >= 1
    assert "GESTAO.B" in coverage.needs_recompile


def test_coverage_lists_reasons_per_object_for_fallback_nodes():
    result = _fallback_result()

    coverage = build_coverage(result, FULL_CAPABILITIES)

    reasons_text = " ".join(coverage.reasons)
    assert "GESTAO.B" in reasons_text
    assert "PL/Scope" in reasons_text


# --------------------------------------------------------------------------
# COBERTURA -- regra dura: soma quebrada levanta, nunca "conserta"
# --------------------------------------------------------------------------


def test_coverage_raises_on_unknown_grain():
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="mystery")],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )

    with pytest.raises(CoverageError, match="objetos alcancados"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_coverage_raises_on_malformed_call_from_ref():
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[ProcEdge(from_ref="MALFORMED", to_ref="GESTAO.X.P1", edge_type="CALL", line=1)],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )

    with pytest.raises(CoverageError, match="calls"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_coverage_raises_on_malformed_statement_from_ref():
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[ProcEdge(from_ref="", to_ref="GESTAO.T1", edge_type="WRITE", line=1)],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )

    with pytest.raises(CoverageError, match="statements"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_render_graph_raises_and_writes_nothing_when_coverage_broken(tmp_path):
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="mystery")],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )
    out_dir = tmp_path / "broken-graph"

    with pytest.raises(CoverageError):
        render_graph(bogus, out_dir, {"capabilities": FULL_CAPABILITIES})

    # Regra dura: nenhum arquivo (nem o diretorio) pode ficar para tras --
    # "o grafo nao e gravado como valido" nao e so sobre o conteudo, e sobre
    # a existencia mesma dos arquivos.
    assert not out_dir.exists()


# --------------------------------------------------------------------------
# capacidades opcionais ausentes -- degradacao declarada (item 3 do T-08)
# --------------------------------------------------------------------------


def test_index_declares_missing_resolve_owner_explicitly(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(
        result,
        out_dir,
        {"capabilities": {"resolve_owner": False, "object_wrapped": True, "triggers": True}},
    )

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "resolve_owner" in index_text
    assert "AUSENTE" in index_text
    assert "podem NAO ter sido seguidas" in index_text


def test_index_declares_capabilities_not_informed(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {})  # sem "capabilities" nenhuma

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "NAO INFORMADO" in index_text


def test_index_declares_all_capabilities_available(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "resolve_owner (resolucao de CALL inter-package por signature): disponivel" in index_text
    assert "object_wrapped (deteccao de objeto PL/SQL wrapped): disponivel" in index_text
    assert "triggers (descoberta de trigger de tabela escrita): disponivel" in index_text


def test_capabilities_from_extractor_reflects_hasattr():
    assert capabilities_from_extractor(FakeExtractor(_load_fixture())) == {
        "resolve_owner": True,
        "object_wrapped": False,
        "triggers": False,
    }
    assert capabilities_from_extractor(FakeProcExtractor()) == {
        "resolve_owner": True,
        "object_wrapped": True,
        "triggers": False,
    }


# --------------------------------------------------------------------------
# ## Ciclos
# --------------------------------------------------------------------------


def test_cycles_section_renders_same_object_mutual_recursion(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## Ciclos" in index_text
    assert "recursao mutua (mesmo objeto)" in index_text
    assert "GESTAO.FLOW_DEMO.PROC_A" in index_text
    assert "GESTAO.FLOW_DEMO.PROC_B" in index_text


def test_cycles_section_renders_cross_object_inter_package(tmp_path):
    result = _inter_package_cycle_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "acoplamento inter-package" in index_text
    assert "GESTAO.PKG_M1.P1" in index_text
    assert "GESTAO.PKG_M2.P2" in index_text


def test_cycles_section_says_nenhum_when_acyclic(tmp_path):
    result = _fallback_result()  # A -> B -> C, sem ciclo nenhum
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    cycles_section = index_text.split("## Ciclos", 1)[1].split("## PONTOS CEGOS", 1)[0]
    assert "- nenhum" in cycles_section


# --------------------------------------------------------------------------
# arvore em disco
# --------------------------------------------------------------------------


def test_render_writes_expected_tree_structure(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    written = render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES, "root_ref": "GESTAO.FLOW_DEMO.MAIN"})

    assert (out_dir / "INDEX.md").exists()
    assert (out_dir / "edges.jsonl").exists()
    assert (out_dir / "meta.json").exists()
    assert (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").exists()
    assert all(p.exists() for p in written)
    # recompile.sql NUNCA gerado neste modo (ProcNode nao carrega
    # object_type -- ver docstring do modulo) mesmo quando needs_recompile
    # existiria em outro caso; aqui nao ha needs_recompile de qualquer forma.
    assert not (out_dir / "recompile.sql").exists()


def test_node_md_content_has_ref_header_and_outbound_calls(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    main_md = (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").read_text(encoding="utf-8")
    assert main_md.startswith("# GESTAO.FLOW_DEMO.MAIN\n")
    assert "## Chama (outbound)" in main_md
    assert "GESTAO.FLOW_DEMO.PROC_A" in main_md
    # assercao negativa (mesmo criterio de tests/test_procgraph_bfs.py):
    # MAIN nunca chama LENGTH nem escreve em FLOW_DEMO_LOG.
    assert "LENGTH" not in main_md
    assert "FLOW_DEMO_LOG" not in main_md


def test_edges_jsonl_has_one_line_per_edge_sorted(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    lines = (out_dir / "edges.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.edges)
    payloads = [json.loads(line) for line in lines]
    assert payloads == sorted(
        payloads, key=lambda p: (p["from_ref"], p["to_ref"], p["edge_type"], p["line"] if p["line"] is not None else -1)
    )


def test_meta_json_has_chain_hash_and_no_timestamp(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert "chain_hash" in meta
    assert len(meta["chain_hash"]) == 64  # SHA-256 hex
    assert "params" in meta
    assert "timestamp" not in meta
    assert "time" not in meta


def test_stale_node_files_removed_between_generations(tmp_path):
    out_dir = tmp_path / "graph"
    render_graph(_flow_demo_result(), out_dir, {"capabilities": FULL_CAPABILITIES})
    assert (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").exists()

    # segunda geracao com um resultado MENOR -- o no antigo tem que sumir,
    # nao ficar orfao (mesma disciplina de depgraph_render.render_graph).
    render_graph(_empty_result(), out_dir, {"capabilities": FULL_CAPABILITIES})

    assert not (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").exists()
    assert list((out_dir / "nodes").glob("*.md")) == []


# --------------------------------------------------------------------------
# determinismo
# --------------------------------------------------------------------------


def test_render_is_byte_for_byte_deterministic_across_two_runs(tmp_path):
    result1 = _flow_demo_result()
    result2 = _flow_demo_result()

    out_dir_1 = tmp_path / "run1"
    out_dir_2 = tmp_path / "run2"
    meta_params = {"capabilities": FULL_CAPABILITIES, "root_ref": "GESTAO.FLOW_DEMO.MAIN"}

    render_graph(result1, out_dir_1, meta_params)
    render_graph(result2, out_dir_2, meta_params)

    index_1 = (out_dir_1 / "INDEX.md").read_bytes()
    index_2 = (out_dir_2 / "INDEX.md").read_bytes()
    assert index_1 == index_2

    edges_1 = (out_dir_1 / "edges.jsonl").read_bytes()
    edges_2 = (out_dir_2 / "edges.jsonl").read_bytes()
    assert edges_1 == edges_2

    meta_1 = json.loads((out_dir_1 / "meta.json").read_text(encoding="utf-8"))
    meta_2 = json.loads((out_dir_2 / "meta.json").read_text(encoding="utf-8"))
    assert meta_1["chain_hash"] == meta_2["chain_hash"]


def test_no_file_uses_crlf_line_endings(tmp_path):
    # Byte-exatidao em Windows (mesma preocupacao literal de
    # depgraph_render.py): nenhuma escrita pode deixar o modo texto padrao
    # trocar "\n" por "\r\n".
    out_dir = tmp_path / "graph"
    render_graph(_flow_demo_result(), out_dir, {"capabilities": FULL_CAPABILITIES})

    for path in [out_dir / "INDEX.md", out_dir / "edges.jsonl", out_dir / "meta.json"]:
        raw = path.read_bytes()
        assert b"\r\n" not in raw


# --------------------------------------------------------------------------
# node_ref / node_filename
# --------------------------------------------------------------------------


def test_node_ref_three_part_for_subprogram():
    node = ProcNode(owner="GESTAO", object_name="FLOW_DEMO", subprogram="MAIN", grain="subprogram")
    assert node_ref(node) == "GESTAO.FLOW_DEMO.MAIN"


def test_node_ref_two_part_for_object_grain_fallback():
    node = ProcNode(owner="GESTAO", object_name="B", subprogram="", grain="object")
    assert node_ref(node) == "GESTAO.B"


def test_node_filename_sanitizes_overload_suffix():
    assert node_filename("GESTAO", "FLOW_DEMO", "CALC_OVERLOAD#2") == "GESTAO.FLOW_DEMO.CALC_OVERLOAD_2.md"


def test_node_filename_two_part_for_fallback_object():
    assert node_filename("GESTAO", "B", "") == "GESTAO.B.md"
