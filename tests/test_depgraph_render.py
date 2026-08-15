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


# ------------------------------------------------------------- indice de arestas (T-04)
#
# `render_graph` deixa de passar a lista INTEIRA de arestas pra cada
# `render_node_md` (custo O(nos x arestas) -- minutos de CPU num grafo de
# 20 mil nos / 200 mil arestas) e passa a indexar uma vez
# (`from_ref -> [arestas]`, `to_ref -> [arestas]`) e entregar so as
# arestas do proprio no. Os tres testes abaixo cobrem exatamente o que a
# tarefa pede: (1) equivalencia -- grafo sintetico medio renderiza os
# MESMOS bytes de sempre; (2) corretude do indice -- no so-inbound, no
# so-outbound, no sem aresta nenhuma e aresta com to_ref pra fora do grafo
# (UNKNOWN_TARGET = "?", emitido de verdade por depgraph_enrich) nao
# quebram nem somem; (3) prova barata de que `render_node_md` recebe so
# as arestas do proprio no (sem medir relogio -- flaky).


def _build_medium_synthetic_result() -> depgraph.DepGraphResult:
    """Grafo sintetico medio (~30 nos) cobrindo os seis `edge_type` e os
    casos que o indice de arestas precisa acertar: cadeia de CALL onde
    cada no do meio tem aresta inbound E outbound ao mesmo tempo,
    SYNONYM_RESOLVES_TO (mesma secao de CALL no node .md), READ/WRITE
    numa TABLE com colunas, TRIGGER_FIRES e duas DYNAMIC_SQL da MESMA
    origem em linhas diferentes (a ordenacao final e por `sorted(...)`
    dentro de `_render_dynsql_lines` -- o indice so precisa preservar as
    DUAS entradas, no ordena quem le). 100% hardcoded, sem banco/relogio/
    random."""
    procs = [
        depgraph.DepNode(
            owner="GESTAO",
            object_name="P{:02d}".format(i),
            object_type="PROCEDURE",
            status="VALID",
            plscope=True,
            source_first_line=1,
            source_last_line=50,
        )
        for i in range(28)
    ]
    table = depgraph.DepNode(
        owner="GESTAO",
        object_name="TBL",
        object_type="TABLE",
        status="VALID",
        plscope=False,
        columns=[
            extract.TabColumnRow(
                owner="GESTAO", table_name="TBL", column_name="ID", column_id=1,
                data_type="NUMBER", nullable="N", data_default=None, data_precision=10,
            ),
            extract.TabColumnRow(
                owner="GESTAO", table_name="TBL", column_name="NOME", column_id=2,
                data_type="VARCHAR2", nullable="Y", data_default=None, data_length=100,
            ),
        ],
    )
    trigger = depgraph.DepNode(
        owner="GESTAO", object_name="TRG", object_type="TRIGGER", status="VALID", plscope=True,
        trigger_status="ENABLED",
    )
    nodes = procs + [table, trigger]

    edges: List[depgraph.DepEdge] = []
    for i in range(27):
        edges.append(depgraph.DepEdge(
            from_ref="GESTAO.P{:02d}".format(i), to_ref="GESTAO.P{:02d}".format(i + 1),
            edge_type="CALL", line=i + 1,
        ))
    edges.append(depgraph.DepEdge(
        from_ref="GESTAO.P05", to_ref="GESTAO.P20", edge_type="SYNONYM_RESOLVES_TO", line=None,
    ))

    for i in (1, 3, 7, 12):
        edges.append(depgraph.DepEdge(
            from_ref="GESTAO.P{:02d}".format(i), to_ref="GESTAO.TBL",
            edge_type="READ", line=100 + i, cols=["ID", "NOME"],
        ))
    for i in (2, 9):
        edges.append(depgraph.DepEdge(
            from_ref="GESTAO.P{:02d}".format(i), to_ref="GESTAO.TBL",
            edge_type="WRITE", line=200 + i, op="INSERT",
        ))

    edges.append(depgraph.DepEdge(
        from_ref="GESTAO.TBL", to_ref="GESTAO.TRG", edge_type="TRIGGER_FIRES", line=None, op="INSERT",
    ))

    # DYNAMIC_SQL: mesma origem (P10), duas linhas distintas -- um alvo
    # dentro do grafo, outro UNKNOWN_TARGET ("?").
    edges.append(depgraph.DepEdge(
        from_ref="GESTAO.P10", to_ref="GESTAO.TBL", edge_type="DYNAMIC_SQL",
        line=15, confidence="partial", dynamic=True, snippet_ref="P10:15",
    ))
    edges.append(depgraph.DepEdge(
        from_ref="GESTAO.P10", to_ref="?", edge_type="DYNAMIC_SQL",
        line=42, confidence="opaque", dynamic=True, snippet_ref="P10:42",
    ))

    snippets = {
        "P10:15": "EXECUTE IMMEDIATE 'SELECT * FROM TBL'",
        "P10:42": "EXECUTE IMMEDIATE v_sql",
    }

    return depgraph.DepGraphResult(
        nodes=nodes,
        edges=edges,
        needs_recompile=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        stats={"nodes": len(nodes), "edges": len(edges)},
        snippets=snippets,
    )


def test_render_equivalence_for_medium_synthetic_graph(tmp_path):
    """Entrega 1 do pedido de teste: o mesmo `DepGraphResult` sintetico
    renderizado em dois diretorios temporarios produz os MESMOS bytes em
    TODOS os arquivos -- se o indice de arestas reordenasse ou perdesse
    algo, esse teste pegaria (a suite golden ja prova que os bytes de hoje
    nao mudam pro grafo pequeno; este prova o mesmo pra um grafo medio,
    com os seis edge_type e o caso de duas DYNAMIC_SQL na mesma origem)."""
    result = _build_medium_synthetic_result()
    out_a = tmp_path / "synthetic_a"
    out_b = tmp_path / "synthetic_b"
    params = {"root_ref": "GESTAO.P00"}

    depgraph_render.render_graph(result, out_a, params)
    depgraph_render.render_graph(result, out_b, params)

    rel_a = [p.relative_to(out_a).as_posix() for p in _tree(out_a)]
    rel_b = [p.relative_to(out_b).as_posix() for p in _tree(out_b)]
    assert rel_a == rel_b

    for rel in rel_a:
        assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes(), (
            "arquivo {} difere entre as duas renderizacoes do mesmo result".format(rel)
        )

    # duas DYNAMIC_SQL da mesma origem, ambas presentes (o indice nao pode
    # engolir uma delas).
    p10_text = (out_a / "nodes" / depgraph_render.node_filename("GESTAO", "P10")).read_text(encoding="utf-8")
    assert "L15" in p10_text and "-> GESTAO.TBL" in p10_text
    assert "L42" in p10_text and "-> ?" in p10_text


def test_edge_index_handles_isolated_and_unknown_target_nodes(tmp_path):
    """Entrega 2 do pedido de teste: ataca a corretude do indice --
    no so-inbound, no so-outbound, no sem aresta nenhuma, e uma aresta
    cujo `to_ref` nao corresponde a NENHUM no do grafo (UNKNOWN_TARGET =
    "?", que `depgraph_enrich` emite de verdade quando nao resolve o alvo
    do SQL dinamico). O indice (`to_ref -> [arestas]`) nao pode estourar
    KeyError nem descartar essa aresta."""
    nodes = [
        depgraph.DepNode(owner="GESTAO", object_name="ONLY_IN", object_type="PROCEDURE", status="VALID", plscope=True),
        depgraph.DepNode(owner="GESTAO", object_name="ONLY_OUT", object_type="PROCEDURE", status="VALID", plscope=True),
        depgraph.DepNode(owner="GESTAO", object_name="ISOLATED", object_type="PROCEDURE", status="VALID", plscope=True),
    ]
    edges = [
        depgraph.DepEdge(from_ref="GESTAO.ONLY_OUT", to_ref="GESTAO.ONLY_IN", edge_type="CALL", line=10),
        # UNKNOWN_TARGET: nenhum no do grafo tem ref "?".
        depgraph.DepEdge(from_ref="GESTAO.ONLY_OUT", to_ref="?", edge_type="WRITE", line=20, op="INSERT"),
    ]
    result = depgraph.DepGraphResult(
        nodes=nodes, edges=edges, needs_recompile=[], truncated=False,
        truncation_reason=None, not_expanded=[], stats={},
    )
    out_dir = tmp_path / "idx"

    # nao pode estourar KeyError/IndexError -- e a asserção principal deste
    # teste.
    depgraph_render.render_graph(result, out_dir, {})

    only_in_text = (out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "ONLY_IN")).read_text(encoding="utf-8")
    assert "## Chamado por (inbound)" in only_in_text
    assert "- GESTAO.ONLY_OUT (CALL)" in only_in_text
    assert "## Chama (outbound)" not in only_in_text

    only_out_text = (out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "ONLY_OUT")).read_text(encoding="utf-8")
    assert "## Chama (outbound)" in only_out_text
    assert "- GESTAO.ONLY_IN (CALL)" in only_out_text
    assert "## Chamado por (inbound)" not in only_out_text
    assert "## Tabelas acessadas" in only_out_text
    assert "-> ?" in only_out_text  # WRITE pro UNKNOWN_TARGET nao pode sumir

    isolated_text = (out_dir / "nodes" / depgraph_render.node_filename("GESTAO", "ISOLATED")).read_text(encoding="utf-8")
    assert "## Chama (outbound)" not in isolated_text
    assert "## Chamado por (inbound)" not in isolated_text
    assert "## Tabelas acessadas" not in isolated_text

    # a aresta pro UNKNOWN_TARGET continua em edges.jsonl -- o indice do
    # render e so uma otimizacao de leitura, nao pode descartar dado.
    edges_text = (out_dir / "edges.jsonl").read_text(encoding="utf-8")
    assert '"to_ref": "?"' in edges_text


def test_render_node_md_receives_only_its_own_edges(monkeypatch, tmp_path):
    """Entrega 3 do pedido de teste: prova barata (sem relogio, sem
    `random`) de que `render_node_md` recebe so as arestas do proprio no
    -- intercepta a chamada e verifica que TODO outbound tem `from_ref`
    igual ao no e TODO inbound tem `to_ref` igual ao no; e que a soma das
    arestas recebidas bate com o total esperado (nenhuma aresta duplicada
    nem perdida). Se `render_graph` voltasse a passar a lista inteira,
    este teste pegaria na hora (outbound/inbound teriam arestas de outros
    nos)."""
    result = _build_medium_synthetic_result()
    node_refs = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}

    original = depgraph_render.render_node_md
    calls: List[Any] = []

    def spy(node, outbound, inbound, nodes_by_ref, snippets):
        ref = "{}.{}".format(node.owner, node.object_name)
        calls.append((ref, list(outbound), list(inbound)))
        return original(node, outbound, inbound, nodes_by_ref, snippets)

    monkeypatch.setattr(depgraph_render, "render_node_md", spy)
    depgraph_render.render_graph(result, tmp_path / "spy", {})

    assert len(calls) == len(result.nodes)
    for ref, outbound, inbound in calls:
        assert all(e.from_ref == ref for e in outbound), "outbound de {} tem aresta de outro no".format(ref)
        assert all(e.to_ref == ref for e in inbound), "inbound de {} tem aresta de outro no".format(ref)

    total_outbound = sum(len(outbound) for _, outbound, _ in calls)
    total_inbound = sum(len(inbound) for _, _, inbound in calls)
    expected_outbound = sum(1 for e in result.edges if e.from_ref in node_refs)
    expected_inbound = sum(1 for e in result.edges if e.to_ref in node_refs)
    assert total_outbound == expected_outbound
    assert total_inbound == expected_inbound
