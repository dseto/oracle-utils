"""Testes offline (T-03, contrato depgraph-granular) para
plsqlflow/procgraph.py -- o motor de BFS de SUBPROGRAMAS.

Nenhum destes casos toca rede/banco: o `FakeExtractor` le
tests/fixtures/procgraph_demo.json (varios objetos sinteticos, mais a
arvore REAL de GESTAO.FLOW_DEMO reaproveitada de plscope_tree.json/T-01) e
implementa `procgraph.ProcExtractor`, incluindo `resolve_owner` -- o
equivalente offline da consulta pontual "quem define esta signature?" que
o motor usa para atravessar packages (ver docstring de
plsqlflow/procgraph.py, secao "Resolucao inter-package").
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from plsqlflow.procgraph import ProcGraphResult, build_proc_graph

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "procgraph_demo.json"


def _load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class FakeExtractor:
    """Fake de `procgraph.ProcExtractor` alimentado pela fixture.
    `id_calls`/`stmt_calls` contam quantas vezes CADA objeto foi lido --
    e o que prova a entrega 3 do contrato (cache por objeto, T-03): visitar
    dois subprogramas do mesmo objeto nao pode reler nada."""

    def __init__(self, fixture: Dict[str, Any]):
        self.objects = fixture["objects"]
        self.id_calls: Dict[str, int] = {}
        self.stmt_calls: Dict[str, int] = {}
        self._sig_owner: Dict[str, Tuple[str, str]] = {}
        for ref, obj in self.objects.items():
            owner, _, name = ref.partition(".")
            for tree in obj["trees"]:
                for row in tree["identifiers"]:
                    if row["usage"] in ("DEFINITION", "DECLARATION") and row.get("signature"):
                        self._sig_owner.setdefault(row["signature"], (owner, name))

    @staticmethod
    def _ref(owner: str, object_name: str) -> str:
        return "{}.{}".format(owner.upper(), object_name.upper())

    def plscope_identifiers(self, owner: str, object_name: str) -> List[Any]:
        ref = self._ref(owner, object_name)
        self.id_calls[ref] = self.id_calls.get(ref, 0) + 1
        obj = self.objects.get(ref)
        if obj is None:
            return []
        rows = []
        for tree in obj["trees"]:
            for row in tree["identifiers"]:
                rows.append(SimpleNamespace(object_type=tree["object_type"], **row))
        return rows

    def plscope_statements(self, owner: str, object_name: str) -> List[Any]:
        ref = self._ref(owner, object_name)
        self.stmt_calls[ref] = self.stmt_calls.get(ref, 0) + 1
        obj = self.objects.get(ref)
        if obj is None:
            return []
        rows = []
        for tree in obj["trees"]:
            for row in tree["statements"]:
                rows.append(SimpleNamespace(object_type=tree["object_type"], **row))
        return rows

    def resolve_owner(self, signature: str) -> Optional[Tuple[str, str]]:
        return self._sig_owner.get(signature)


def _run_with_timeout(fn, timeout: float = 5.0):
    """Prova de terminacao (item b do contrato: 'timeout curto do teste').
    O motor em si NUNCA usa timeout como mecanismo de parada (proibido
    pelo plano) -- isto e so o arnes do TESTE, rodando a travessia numa
    thread separada para provar que ela devolve controle sozinha, sem
    depender de nenhum sinal externo. Falha (thread ainda viva apos o
    timeout) significa loop infinito real no motor."""
    box: Dict[str, Any] = {}

    def _target() -> None:
        box["result"] = fn()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "travessia nao terminou dentro do timeout -- possivel loop infinito"
    return box["result"]


def _edges_from(result: ProcGraphResult, from_ref: str) -> List:
    return [e for e in result.edges if e.from_ref == from_ref]


# --------------------------------------------------------------- golden (a)


def test_golden_flow_demo_main_chain_no_false_edges():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))

    main_ref = "GESTAO.FLOW_DEMO.MAIN"
    main_targets = {e.to_ref for e in _edges_from(result, main_ref)}
    assert main_targets == {
        "GESTAO.FLOW_DEMO.PROC_A",
        "GESTAO.FLOW_DEMO.RUN_DYNAMIC",
        "GESTAO.FLOW_DEMO.CALC_OVERLOAD#1",
        "GESTAO.FLOW_DEMO.CALC_OVERLOAD#2",
    }

    proc_a_targets = {e.to_ref for e in _edges_from(result, "GESTAO.FLOW_DEMO.PROC_A")}
    assert proc_a_targets == {"GESTAO.FLOW_DEMO.LOG_MSG", "GESTAO.FLOW_DEMO.PROC_B"}

    proc_b_targets = {e.to_ref for e in _edges_from(result, "GESTAO.FLOW_DEMO.PROC_B")}
    assert proc_b_targets == {"GESTAO.FLOW_DEMO.PROC_A"}  # recursao mutua

    calc2_edges = _edges_from(result, "GESTAO.FLOW_DEMO.CALC_OVERLOAD#2")
    assert len(calc2_edges) == 1
    assert calc2_edges[0].resolved is False
    assert "LENGTH" in calc2_edges[0].to_ref


def test_golden_main_never_calls_length_directly():
    # Assercao NEGATIVA do contrato: e o que distingue este motor do modo
    # flow (graph.py), que reconsidera as chamadas do OBJETO inteiro em
    # cada no expandido e faria MAIN "chamar" LENGTH por sobre-aproximacao.
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))

    main_edges = _edges_from(result, "GESTAO.FLOW_DEMO.MAIN")
    assert not any("LENGTH" in e.to_ref for e in main_edges)


def test_golden_main_never_writes_flow_demo_log():
    # Segunda assercao negativa obrigatoria: quem insere em FLOW_DEMO_LOG e
    # LOG_MSG (via STMT, seam de T-05), nunca MAIN.
    #
    # O escopo do assert e proposital e NAO pode ser alargado para
    # `result.edges`: a aresta LOG_MSG -> FLOW_DEMO_LOG e o resultado CERTO
    # assim que T-05 implementar `expand_access` (hoje o seam e stub e o
    # grafo nao tem nenhuma aresta de acesso a tabela). Um assert sobre o
    # grafo inteiro passaria hoje por acidente e quebraria exatamente quando
    # a T-05 acertasse -- convidando quem mexe depois a apagar a assercao
    # negativa em vez de manter a garantia. O que o contrato exige e que a
    # escrita nao seja atribuida a MAIN, e e so isso que se afirma aqui.
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))

    main_edges = _edges_from(result, "GESTAO.FLOW_DEMO.MAIN")
    assert not any("FLOW_DEMO_LOG" in e.to_ref for e in main_edges)


def test_golden_init_enters_queue_with_first_subprogram():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))

    init_node = next(n for n in result.nodes if n.subprogram == "__INIT__")
    assert init_node.owner == "GESTAO"
    assert init_node.object_name == "FLOW_DEMO"
    assert init_node.grain == "subprogram"


# ----------------------------------------------------------- cache (d)


def test_cache_reads_flow_demo_object_exactly_once():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))

    # MAIN puxa PROC_A/RUN_DYNAMIC/CALC_OVERLOAD#1/#2; PROC_A puxa
    # LOG_MSG/PROC_B; PROC_B puxa PROC_A de volta -- 8 subprogramas (contando
    # __INIT__) do MESMO objeto GESTAO.FLOW_DEMO. A leitura tem que
    # acontecer uma unica vez.
    assert extractor.id_calls["GESTAO.FLOW_DEMO"] == 1
    assert extractor.stmt_calls["GESTAO.FLOW_DEMO"] == 1


# ------------------------------------------------------- determinismo (e)


def test_determinism_two_runs_identical_output():
    fixture = _load_fixture()

    result1 = build_proc_graph(FakeExtractor(fixture), ("GESTAO", "FLOW_DEMO", "MAIN"))
    result2 = build_proc_graph(FakeExtractor(fixture), ("GESTAO", "FLOW_DEMO", "MAIN"))

    assert result1.nodes == result2.nodes
    assert result1.edges == result2.edges
    assert result1.not_expanded == result2.not_expanded
    assert result1.blind_spots == result2.blind_spots
    assert result1.stats == result2.stats


# -------------------------------------------------------- terminacao (b)


def test_termination_mutual_call_between_two_packages():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    result = _run_with_timeout(
        lambda: build_proc_graph(extractor, ("GESTAO", "PKG_M1", "P1")), timeout=5.0
    )

    edges = {(e.from_ref, e.to_ref) for e in result.edges}
    assert ("GESTAO.PKG_M1.P1", "GESTAO.PKG_M2.P2") in edges
    assert ("GESTAO.PKG_M2.P2", "GESTAO.PKG_M1.P1") in edges

    refs = {n.owner + "." + n.object_name + "." + n.subprogram for n in result.nodes}
    assert "GESTAO.PKG_M1.P1" in refs
    assert "GESTAO.PKG_M2.P2" in refs


def test_termination_direct_recursion():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    result = _run_with_timeout(
        lambda: build_proc_graph(extractor, ("GESTAO", "PKG_REC", "P")), timeout=5.0
    )

    edges = {(e.from_ref, e.to_ref) for e in result.edges}
    assert ("GESTAO.PKG_REC.P", "GESTAO.PKG_REC.P") in edges
    # so uma unica ProcNode P -- visited-set impediu reenfileirar.
    assert sum(1 for n in result.nodes if n.subprogram == "P") == 1


# --------------------------------------------------- travessia feliz (c)


def test_inter_package_chain_reaches_all_three_objects():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    result = build_proc_graph(extractor, ("GESTAO", "PKG_CH_A", "P1"))

    refs = {(n.owner, n.object_name, n.subprogram) for n in result.nodes}
    assert ("GESTAO", "PKG_CH_A", "P1") in refs
    assert ("GESTAO", "PKG_CH_B", "P2") in refs
    assert ("GESTAO", "PKG_CH_C", "P3") in refs

    edge_pairs = {(e.from_ref, e.to_ref) for e in result.edges}
    assert ("GESTAO.PKG_CH_A.P1", "GESTAO.PKG_CH_B.P2") in edge_pairs
    assert ("GESTAO.PKG_CH_B.P2", "GESTAO.PKG_CH_C.P3") in edge_pairs

    # P3 e folha (nao chama nada) -- prova que a cadeia nao inventa nada
    # alem do que a signature realmente resolve.
    p3_node = next(n for n in result.nodes if n.subprogram == "P3")
    assert p3_node.is_leaf is True


# ------------------------------------------------------- semeadura 2-partes


def test_two_part_root_seeds_all_public_subprograms():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    result = build_proc_graph(extractor, ("GESTAO", "PKG_API"))

    subprograms = {n.subprogram for n in result.nodes if n.object_name == "PKG_API"}
    assert "PUB1" in subprograms
    assert "PUB2" in subprograms


# --------------------------------------------------------- caps opt-in (f)


def test_max_objects_cap_truncates_with_declared_reason():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    # PKG_CH_A (raiz) + PKG_CH_B cabem no cap de 2; PKG_CH_C fica de fora.
    result = build_proc_graph(extractor, ("GESTAO", "PKG_CH_A", "P1"), max_objects=2)

    assert result.truncated is True
    assert result.truncation_reason is not None
    assert result.not_expanded  # truncamento item a item, nunca silencioso
    assert any("PKG_CH_C" in item for item in result.not_expanded)

    refs = {(n.owner, n.object_name, n.subprogram) for n in result.nodes}
    assert ("GESTAO", "PKG_CH_C", "P3") not in refs


def test_multiple_roots_share_the_same_visited_set():
    # Entrega 2 do contrato ("aceite multiplas raizes", mesmo padrao de
    # depgraph.build_dep_graph(..., roots=...)): duas raizes de 3 partes,
    # uma delas ja alcancavel pela outra (PROC_A e alcancado por MAIN) --
    # tem que virar UM SO no, nunca duplicado nem reprocessado.
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    result = build_proc_graph(
        extractor,
        ("GESTAO", "FLOW_DEMO", "MAIN"),
        roots=[("GESTAO", "FLOW_DEMO", "PROC_A")],
    )

    proc_a_nodes = [n for n in result.nodes if n.subprogram == "PROC_A"]
    assert len(proc_a_nodes) == 1
    assert extractor.id_calls["GESTAO.FLOW_DEMO"] == 1


def test_no_cap_means_no_truncation():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)

    result = build_proc_graph(extractor, ("GESTAO", "PKG_CH_A", "P1"))

    assert result.truncated is False
    assert result.truncation_reason is None
    assert result.not_expanded == []

    refs = {(n.owner, n.object_name, n.subprogram) for n in result.nodes}
    assert ("GESTAO", "PKG_CH_C", "P3") in refs
