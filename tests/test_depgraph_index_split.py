"""Testes offline (T-04, contrato depgraph-scale) para o limiar
`--index-split` de `plsqlflow/depgraph_render.py`: abaixo do limiar o
`INDEX.md` continua no formato flat de sempre (byte a byte, provado pelo
golden test de tests/test_depgraph_render.py); acima do limiar vira
sumario (Estatisticas / Hubs / lista de particoes) e o fechamento
transitivo completo migra para `INDEX-<OWNER>.md`, um por schema.

100% sem banco -- todo `DepGraphResult` e montado a mao (sem
extractor/fixture de rede), sem relogio nem `random`.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import List

from plsqlflow import depgraph, depgraph_render

DepEdge = depgraph.DepEdge
DepNode = depgraph.DepNode
DepGraphResult = depgraph.DepGraphResult


def _tree(root: Path) -> List[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


# --------------------------------------------------------------------------
# grafo pequeno (abaixo do limiar) -- cobre PONTOS CEGOS completo tambem no
# regime flat, pra provar que a secao nunca desaparece independente do
# tamanho do grafo.
# --------------------------------------------------------------------------


def _build_small_result() -> DepGraphResult:
    nodes = [
        DepNode(owner="GESTAO", object_name="PKG_A", object_type="PACKAGE", status="VALID", plscope=True),
        DepNode(owner="GESTAO", object_name="PKG_B", object_type="PACKAGE", status="VALID", plscope=True),
        DepNode(owner="GESTAO", object_name="TBL", object_type="TABLE", status="VALID", plscope=False, is_leaf=True),
        DepNode(
            owner="GESTAO", object_name="PKG_NOPLSCOPE", object_type="PACKAGE",
            status="VALID", plscope=False,
        ),
    ]
    edges = [
        DepEdge(from_ref="GESTAO.PKG_A", to_ref="GESTAO.PKG_B", edge_type="CALL", line=1),
        DepEdge(from_ref="GESTAO.PKG_B", to_ref="GESTAO.TBL", edge_type="WRITE", line=5, op="INSERT"),
        DepEdge(
            from_ref="GESTAO.PKG_A", to_ref="GESTAO.PKG_B", edge_type="DYNAMIC_SQL",
            line=10, confidence="partial", dynamic=True, snippet_ref="A:10",
        ),
        DepEdge(
            from_ref="GESTAO.PKG_A", to_ref="?", edge_type="DYNAMIC_SQL",
            line=20, confidence="opaque", dynamic=True, snippet_ref="A:20",
        ),
    ]
    return DepGraphResult(
        nodes=nodes,
        edges=edges,
        needs_recompile=["GESTAO.PKG_NOPLSCOPE"],
        truncated=True,
        truncation_reason="max_objects atingido (4)",
        not_expanded=["GESTAO.PKG_C"],
        stats={"nodes": len(nodes), "edges": len(edges)},
        snippets={"A:10": "EXECUTE IMMEDIATE 'X'", "A:20": "EXECUTE IMMEDIATE v_sql"},
    )


def test_below_threshold_keeps_flat_index_and_no_partitions(tmp_path):
    result = _build_small_result()
    out_dir = tmp_path / "small"
    # index_split bem acima do numero de nos -- fica no regime de sempre.
    depgraph_render.render_graph(result, out_dir, {"root_ref": "GESTAO.PKG_A", "index_split": 100})

    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## Hubs" not in text
    assert "## Fechamento transitivo" in text
    for ref, type_, marker in (
        ("GESTAO.PKG_A", "PACKAGE", ""),
        ("GESTAO.PKG_B", "PACKAGE", ""),
        ("GESTAO.TBL", "TABLE", " (leaf)"),
        ("GESTAO.PKG_NOPLSCOPE", "PACKAGE", ""),
    ):
        assert "- {} [{}]{}".format(ref, type_, marker) in text

    assert not list(out_dir.glob("INDEX-*.md"))


def test_below_threshold_pontos_cegos_complete(tmp_path):
    result = _build_small_result()
    out_dir = tmp_path / "small_blind"
    depgraph_render.render_graph(result, out_dir, {"index_split": 100})
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")

    assert "## PONTOS CEGOS" in text
    assert "### SQL dinâmico não resolvido" in text
    assert "GESTAO.PKG_A L10 [partial] -> GESTAO.PKG_B" in text
    assert "GESTAO.PKG_A L20 [opaque] -> ?" in text
    assert "### Objetos sem PL/Scope" in text
    assert "- GESTAO.PKG_NOPLSCOPE" in text
    assert "### Truncamento" in text
    assert "- motivo: max_objects atingido (4)" in text
    assert "- não expandido: GESTAO.PKG_C" in text


def test_default_index_split_is_1000_when_key_missing(tmp_path):
    """`meta_params` sem "index_split" (chamador antigo) cai no default
    1000 -- um grafo de 4 nos fica MUITO abaixo, entao continua flat."""
    result = _build_small_result()
    out_dir = tmp_path / "default_split"
    depgraph_render.render_graph(result, out_dir, {"root_ref": "GESTAO.PKG_A"})
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## Hubs" not in text
    assert "## Fechamento transitivo" in text
    assert not list(out_dir.glob("INDEX-*.md"))


# --------------------------------------------------------------------------
# grafo grande (acima do limiar): 30 nos em 3 owners (GESTAO=20, FIN=6,
# RH=4), --index-split 10. Grau (entrada+saida) de cada no e controlado via
# arestas para "sinks" fora do grafo (o indice de hubs so precisa que o
# REF apareca numa aresta, o alvo nao precisa ser um no do result) -- isso
# permite atribuir um grau exato por no sem depender de contagem indireta.
#
# Graus atribuidos (decrescente): G01=30 G02=29 G03=28 G04=27 G05=26 G06=25
# G07=24 G08=23 G09=22 G10=21 G11=20 G12=19 G13=18 G14=17 G15=16 G16=15
# G17=14 G18=13 G19=12 G20=11 F01=13(empate com G18) F02=10 F03=9 F04=8
# F05=7 F06=6 R01=5 R02=4 R03=3 R04=2.
#
# Ordenacao esperada por (-grau, owner, object_name): G01..G17 (graus
# 30..14, 17 nos) seguidos do empate grau=13 -- FIN < GESTAO alfabetico,
# entao F01 vem ANTES de G18 -- depois G19(12). Isso fecha os 20 primeiros
# (17 + F01 + G18 + G19 = 20); G20 (grau 11, 21o colocado) fica de FORA do
# Hubs mesmo tendo mais grau que qualquer no FIN/RH restante -- prova que o
# corte e por RANKING, nao por owner.
# --------------------------------------------------------------------------

_GESTAO_DEGREES = {
    "G01": 30, "G02": 29, "G03": 28, "G04": 27, "G05": 26, "G06": 25,
    "G07": 24, "G08": 23, "G09": 22, "G10": 21, "G11": 20, "G12": 19,
    "G13": 18, "G14": 17, "G15": 16, "G16": 15, "G17": 14, "G18": 13,
    "G19": 12, "G20": 11,
}
_FIN_DEGREES = {"F01": 13, "F02": 10, "F03": 9, "F04": 8, "F05": 7, "F06": 6}
_RH_BASE_DEGREES = {"R01": 5, "R02": 4, "R03": 3, "R04": 2}


def _degree_edges(owner: str, name: str, count: int) -> List[DepEdge]:
    """`count` arestas outbound do no para sinks dedicados e inexistentes
    no grafo -- infla o grau de `owner.name` em exatamente `count`, sem
    afetar o grau de nenhum outro no do result (o sink nunca e um DepNode,
    entao nunca concorre por posicao de hub)."""
    return [
        DepEdge(
            from_ref="{}.{}".format(owner, name),
            to_ref="SINK.{}_{}_{}".format(owner, name, i),
            edge_type="CALL",
            line=i,
        )
        for i in range(count)
    ]


def _build_large_result() -> DepGraphResult:
    nodes: List[DepNode] = []
    edges: List[DepEdge] = []

    for name, degree in _GESTAO_DEGREES.items():
        nodes.append(DepNode(owner="GESTAO", object_name=name, object_type="PROCEDURE", status="VALID", plscope=True))
        edges.extend(_degree_edges("GESTAO", name, degree))
    for name, degree in _FIN_DEGREES.items():
        nodes.append(DepNode(owner="FIN", object_name=name, object_type="PROCEDURE", status="VALID", plscope=True))
        edges.extend(_degree_edges("FIN", name, degree))
    for name, degree in _RH_BASE_DEGREES.items():
        # RH.R04 marcado leaf so pra provar que o marcador " (leaf)"
        # sobrevive na particao INDEX-RH.md.
        nodes.append(
            DepNode(
                owner="RH", object_name=name, object_type="PROCEDURE", status="VALID", plscope=True,
                is_leaf=(name == "R04"),
            )
        )
        edges.extend(_degree_edges("RH", name, degree))

    # PONTOS CEGOS: dynamic sql partial/opaque em nos de grau baixo (RH),
    # que nao interferem no ranking de hubs (ficam bem abaixo do corte).
    edges.append(
        DepEdge(
            from_ref="RH.R03", to_ref="RH.R04", edge_type="DYNAMIC_SQL",
            line=10, confidence="partial", dynamic=True, snippet_ref="R03:10",
        )
    )
    edges.append(
        DepEdge(
            from_ref="RH.R03", to_ref="?", edge_type="DYNAMIC_SQL",
            line=20, confidence="opaque", dynamic=True, snippet_ref="R03:20",
        )
    )

    return DepGraphResult(
        nodes=nodes,
        edges=edges,
        needs_recompile=["FIN.F06"],
        truncated=True,
        truncation_reason="max_objects atingido (30)",
        not_expanded=["GESTAO.G21", "FIN.F07"],
        stats={"nodes": len(nodes), "edges": len(edges)},
        snippets={"R03:10": "EXECUTE IMMEDIATE 'Y'", "R03:20": "EXECUTE IMMEDIATE v_sql"},
    )


_LARGE_META = {"root_ref": "GESTAO.G01", "index_split": 10}


def test_above_threshold_index_has_four_sections_in_order(tmp_path):
    result = _build_large_result()
    out_dir = tmp_path / "large"
    depgraph_render.render_graph(result, out_dir, _LARGE_META)
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")

    for heading in ("## Estatísticas", "## Hubs", "## Fechamento transitivo", "## PONTOS CEGOS"):
        assert heading in text, "secao {} ausente".format(heading)

    positions = [text.index(h) for h in ("## Estatísticas", "## Hubs", "## Fechamento transitivo", "## PONTOS CEGOS")]
    assert positions == sorted(positions)


def test_above_threshold_hubs_top20_deterministic_with_tie_break(tmp_path):
    result = _build_large_result()
    out_dir = tmp_path / "hubs"
    depgraph_render.render_graph(result, out_dir, _LARGE_META)
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")

    hub_section = text.split("## Hubs", 1)[1].split("## Fechamento transitivo", 1)[0]
    hub_refs = [line.split(" ", 2)[1] for line in hub_section.splitlines() if line.startswith("- ")]

    expected = (
        ["GESTAO.G{:02d}".format(i) for i in range(1, 18)]
        + ["FIN.F01", "GESTAO.G18", "GESTAO.G19"]
    )
    assert hub_refs == expected
    assert len(hub_refs) == 20
    # G20 tem grau 11 (21o colocado) -- fora do top 20 mesmo estando na
    # mesma familia dos demais G-nos.
    assert "GESTAO.G20" not in hub_refs

    # contagens visiveis: G01 grau 30, tudo outbound (entrada 0, saida 30).
    assert "- GESTAO.G01 [PROCEDURE] (grau: 30, entrada: 0, saída: 30)" in text
    # empate resolvido por owner: FIN.F01 grau 13, GESTAO.G18 grau 13,
    # FIN < GESTAO -- F01 aparece antes de G18 (ja provado por `expected`
    # acima; aqui so confere o texto exato de ambas as linhas).
    assert "- FIN.F01 [PROCEDURE] (grau: 13, entrada: 0, saída: 13)" in text
    assert "- GESTAO.G18 [PROCEDURE] (grau: 13, entrada: 0, saída: 13)" in text


def test_above_threshold_one_partition_file_per_owner_and_no_node_lost(tmp_path):
    result = _build_large_result()
    out_dir = tmp_path / "partitions"
    depgraph_render.render_graph(result, out_dir, _LARGE_META)

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    fechamento = index_text.split("## Fechamento transitivo", 1)[1].split("## PONTOS CEGOS", 1)[0]
    assert "- INDEX-GESTAO.md (20 objetos)" in fechamento
    assert "- INDEX-FIN.md (6 objetos)" in fechamento
    assert "- INDEX-RH.md (4 objetos)" in fechamento

    partition_files = sorted(out_dir.glob("INDEX-*.md"))
    assert [p.name for p in partition_files] == ["INDEX-FIN.md", "INDEX-GESTAO.md", "INDEX-RH.md"]

    total_partitioned = 0
    for path in partition_files:
        text = path.read_text(encoding="utf-8")
        total_partitioned += sum(1 for line in text.splitlines() if line.startswith("- "))
    assert total_partitioned == len(result.nodes)

    rh_text = (out_dir / "INDEX-RH.md").read_text(encoding="utf-8")
    assert "- RH.R04 [PROCEDURE] (leaf)" in rh_text
    assert "- RH.R01 [PROCEDURE]" in rh_text
    assert "- RH.R01 [PROCEDURE] (leaf)" not in rh_text


def test_above_threshold_pontos_cegos_complete(tmp_path):
    result = _build_large_result()
    out_dir = tmp_path / "large_blind"
    depgraph_render.render_graph(result, out_dir, _LARGE_META)
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")

    assert "## PONTOS CEGOS" in text
    assert "### SQL dinâmico não resolvido" in text
    assert "RH.R03 L10 [partial] -> RH.R04" in text
    assert "RH.R03 L20 [opaque] -> ?" in text
    assert "### Objetos sem PL/Scope" in text
    assert "- FIN.F06" in text
    assert "### Truncamento" in text
    assert "- motivo: max_objects atingido (30)" in text
    assert "- não expandido: FIN.F07" in text
    assert "- não expandido: GESTAO.G21" in text


# --------------------------------------------------------------------------
# idempotencia e orfaos
# --------------------------------------------------------------------------


def test_render_twice_above_threshold_identical_bytes(tmp_path):
    result = _build_large_result()
    out_dir = tmp_path / "idempotent"
    depgraph_render.render_graph(result, out_dir, _LARGE_META)
    first = {p.relative_to(out_dir).as_posix(): p.read_bytes() for p in _tree(out_dir)}

    depgraph_render.render_graph(result, out_dir, _LARGE_META)
    second = {p.relative_to(out_dir).as_posix(): p.read_bytes() for p in _tree(out_dir)}

    assert first == second
    assert any(name.startswith("INDEX-") for name in first)


def test_orphan_partitions_removed_when_graph_falls_back_below_threshold(tmp_path):
    large = _build_large_result()
    out_dir = tmp_path / "orphan"
    depgraph_render.render_graph(large, out_dir, _LARGE_META)
    assert sorted(p.name for p in out_dir.glob("INDEX-*.md")) == [
        "INDEX-FIN.md", "INDEX-GESTAO.md", "INDEX-RH.md",
    ]

    # index_split bem acima do numero de nos -- volta pro regime flat, e os
    # INDEX-<OWNER>.md da geracao anterior nao podem sobrar.
    depgraph_render.render_graph(large, out_dir, {"root_ref": "GESTAO.G01", "index_split": 1000})
    assert not list(out_dir.glob("INDEX-*.md"))
    text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## Hubs" not in text
    assert "## Fechamento transitivo" in text


def test_orphan_partition_removed_when_owner_disappears_from_graph(tmp_path):
    large = _build_large_result()
    out_dir = tmp_path / "shrink_owner"
    depgraph_render.render_graph(large, out_dir, _LARGE_META)
    assert (out_dir / "INDEX-RH.md").exists()

    without_rh = replace(large, nodes=[n for n in large.nodes if n.owner != "RH"])
    depgraph_render.render_graph(without_rh, out_dir, _LARGE_META)

    assert not (out_dir / "INDEX-RH.md").exists()
    assert (out_dir / "INDEX-GESTAO.md").exists()
    assert (out_dir / "INDEX-FIN.md").exists()
