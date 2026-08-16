"""Testes offline (T-07, contrato depgraph-granular) para
plsqlflow/procgraph_cycles.py -- deteccao de SCC (Tarjan) sobre o grafo de
subprogramas.

Modulo puro: a maioria dos casos aqui constroi `ProcEdge` diretamente, sem
banco nem `ProcExtractor` -- so o caso de recursao mutua PROC_A/PROC_B
reusa a fixture real (tests/fixtures/procgraph_demo.json, via
`FakeExtractor`/`build_proc_graph` de test_procgraph_bfs.py) para provar
que `find_cycles` consome a saida de verdade do motor T-03, nao so uma
forma inventada de aresta.
"""
from __future__ import annotations

import random
import threading
from typing import Any, Dict

from plsqlflow.procgraph import ProcEdge, build_proc_graph
from plsqlflow.procgraph_cycles import (
    DEFAULT_CYCLE_EDGE_TYPES,
    CycleDetectionResult,
    find_cycles,
)
from tests.test_procgraph_bfs import FakeExtractor, _load_fixture


def _edge(from_ref: str, to_ref: str, edge_type: str = "CALL", line: int = 1) -> ProcEdge:
    return ProcEdge(from_ref=from_ref, to_ref=to_ref, edge_type=edge_type, line=line)


def _run_with_timeout(fn, timeout: float = 5.0):
    """Mesmo arnes de test_procgraph_bfs.py: prova que a deteccao termina
    sozinha (sem depender de nenhum sinal externo) mesmo num grafo grande e
    profundo -- e o teste que prova o item 1 do contrato (Tarjan iterativo,
    nao recursivo)."""
    box: Dict[str, Any] = {}

    def _target() -> None:
        box["result"] = fn()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "deteccao nao terminou dentro do timeout -- possivel loop infinito"
    return box["result"]


# --------------------------------------------------------- recursao direta


def test_direct_recursion_is_a_single_member_group():
    edges = [_edge("GESTAO.PKG_REC.P", "GESTAO.PKG_REC.P")]

    result = find_cycles(edges)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.members == ("GESTAO.PKG_REC.P",)
    assert group.kind == "direct_recursion"
    assert group.objects == (("GESTAO", "PKG_REC"),)
    assert group.size == 1


def test_scc_of_one_member_without_self_loop_is_not_a_cycle():
    # A chama B, B nao chama ninguem de volta -- cada um vira SCC trivial
    # de 1 membro, SEM self-loop. Nenhum dos dois pode aparecer no
    # resultado (regra dura do item 2 do contrato).
    edges = [_edge("GESTAO.PKG_X.A", "GESTAO.PKG_X.B")]

    result = find_cycles(edges)

    assert result.groups == []


# ------------------------------------------------- recursao mutua (fixture real)


def test_mutual_recursion_proc_a_proc_b_from_real_fixture():
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    proc_result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))

    result = find_cycles(proc_result.edges)

    group = next(
        g
        for g in result.groups
        if set(g.members) == {"GESTAO.FLOW_DEMO.PROC_A", "GESTAO.FLOW_DEMO.PROC_B"}
    )
    assert group.kind == "same_object"
    assert group.objects == (("GESTAO", "FLOW_DEMO"),)
    assert (
        "GESTAO.FLOW_DEMO.PROC_A",
        "GESTAO.FLOW_DEMO.PROC_B",
        "CALL",
    ) in group.internal_edges
    assert (
        "GESTAO.FLOW_DEMO.PROC_B",
        "GESTAO.FLOW_DEMO.PROC_A",
        "CALL",
    ) in group.internal_edges


# --------------------------------------------------------- ciclo inter-package


def test_inter_package_cycle_is_classified_cross_object():
    edges = [
        _edge("GESTAO.PKG_A.P1", "GESTAO.PKG_B.P2"),
        _edge("GESTAO.PKG_B.P2", "GESTAO.PKG_A.P1"),
    ]

    result = find_cycles(edges)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert set(group.members) == {"GESTAO.PKG_A.P1", "GESTAO.PKG_B.P2"}
    assert group.kind == "cross_object"
    assert set(group.objects) == {("GESTAO", "PKG_A"), ("GESTAO", "PKG_B")}


def test_three_hop_cycle_across_three_packages():
    edges = [
        _edge("GESTAO.PKG_CH_A.P1", "GESTAO.PKG_CH_B.P2"),
        _edge("GESTAO.PKG_CH_B.P2", "GESTAO.PKG_CH_C.P3"),
        _edge("GESTAO.PKG_CH_C.P3", "GESTAO.PKG_CH_A.P1"),
    ]

    result = find_cycles(edges)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert set(group.members) == {
        "GESTAO.PKG_CH_A.P1",
        "GESTAO.PKG_CH_B.P2",
        "GESTAO.PKG_CH_C.P3",
    }
    assert group.kind == "cross_object"
    assert len(group.internal_edges) == 3


# ------------------------------------------------------------ grafo aciclico


def test_linear_chain_produces_no_groups():
    edges = [
        _edge("GESTAO.PKG_X.A", "GESTAO.PKG_X.B"),
        _edge("GESTAO.PKG_X.B", "GESTAO.PKG_Y.C"),
    ]

    result = find_cycles(edges)

    assert result.groups == []
    assert result.stats["groups"] == 0


# ------------------------------------------------------- ciclos disjuntos


def test_two_disjoint_cycles_become_two_separate_groups():
    edges = [
        # ciclo 1: recursao mutua dentro do mesmo objeto
        _edge("GESTAO.PKG_X.A", "GESTAO.PKG_X.B"),
        _edge("GESTAO.PKG_X.B", "GESTAO.PKG_X.A"),
        # ciclo 2: inter-package, totalmente desconexo do ciclo 1
        _edge("GESTAO.PKG_Y.C", "GESTAO.PKG_Z.D"),
        _edge("GESTAO.PKG_Z.D", "GESTAO.PKG_Y.C"),
        # aresta solta, nao participa de ciclo nenhum
        _edge("GESTAO.PKG_Y.C", "GESTAO.PKG_W.E"),
    ]

    result = find_cycles(edges)

    assert len(result.groups) == 2
    member_sets = [set(g.members) for g in result.groups]
    assert {"GESTAO.PKG_X.A", "GESTAO.PKG_X.B"} in member_sets
    assert {"GESTAO.PKG_Y.C", "GESTAO.PKG_Z.D"} in member_sets
    kinds = {frozenset(g.members): g.kind for g in result.groups}
    assert kinds[frozenset({"GESTAO.PKG_X.A", "GESTAO.PKG_X.B"})] == "same_object"
    assert kinds[frozenset({"GESTAO.PKG_Y.C", "GESTAO.PKG_Z.D"})] == "cross_object"


# ----------------------------------------------------------------- determinismo


def test_determinism_two_runs_identical_output():
    edges = [
        _edge("GESTAO.PKG_A.P1", "GESTAO.PKG_B.P2"),
        _edge("GESTAO.PKG_B.P2", "GESTAO.PKG_C.P3"),
        _edge("GESTAO.PKG_C.P3", "GESTAO.PKG_A.P1"),
        _edge("GESTAO.PKG_REC.P", "GESTAO.PKG_REC.P"),
    ]

    result1 = find_cycles(edges)
    result2 = find_cycles(edges)

    assert result1.groups == result2.groups
    assert result1.stats == result2.stats


def test_determinism_shuffled_edges_same_output():
    edges = [
        _edge("GESTAO.PKG_A.P1", "GESTAO.PKG_B.P2"),
        _edge("GESTAO.PKG_B.P2", "GESTAO.PKG_C.P3"),
        _edge("GESTAO.PKG_C.P3", "GESTAO.PKG_A.P1"),
        _edge("GESTAO.PKG_X.A", "GESTAO.PKG_X.B"),
        _edge("GESTAO.PKG_X.B", "GESTAO.PKG_X.A"),
        _edge("GESTAO.PKG_REC.P", "GESTAO.PKG_REC.P"),
        _edge("GESTAO.PKG_LEAF.L1", "GESTAO.PKG_LEAF.L2"),
    ]

    baseline = find_cycles(edges)

    rng = random.Random(42)
    for _ in range(20):
        shuffled = list(edges)
        rng.shuffle(shuffled)
        result = find_cycles(shuffled)
        assert result.groups == baseline.groups
        assert result.stats == baseline.stats


# ------------------------------------------------------- quais arestas contam


def test_read_write_edges_do_not_form_cycle_by_default():
    # A le/escreve a mesma tabela que B, e vice-versa -- estruturalmente
    # ciclico no grafo bruto, mas READ/WRITE nao e aresta de CHAMADA: por
    # default nao pode virar "ciclo de execucao".
    edges = [
        _edge("GESTAO.PKG_X.A", "GESTAO.T1", edge_type="WRITE"),
        _edge("GESTAO.T1", "GESTAO.PKG_X.A", edge_type="READ"),
        _edge("GESTAO.PKG_X.B", "GESTAO.T1", edge_type="STATE_WRITE"),
        _edge("GESTAO.T1", "GESTAO.PKG_X.B", edge_type="STATE_READ"),
    ]

    result = find_cycles(edges)

    assert result.groups == []


def test_read_write_edges_can_be_opted_in_via_edge_types_param():
    edges = [
        _edge("GESTAO.PKG_X.A", "GESTAO.T1", edge_type="WRITE"),
        _edge("GESTAO.T1", "GESTAO.PKG_X.A", edge_type="READ"),
    ]

    result = find_cycles(edges, edge_types=frozenset({"READ", "WRITE"}))

    assert len(result.groups) == 1
    assert set(result.groups[0].members) == {"GESTAO.PKG_X.A", "GESTAO.T1"}


def test_trigger_cycle_counts_by_default_and_is_flagged():
    # T1 grava T2 via TRG1, TRG2 (disparado pela gravacao) grava T1 de
    # volta -- ciclo real de execucao (disparo implicito, nao CALL
    # explicita), mesmo exemplo do plano (secao "Protecao contra recursao
    # infinita").
    edges = [
        _edge("GESTAO.PKG_T.TRG1", "GESTAO.PKG_T.TRG2", edge_type="TRIGGER"),
        _edge("GESTAO.PKG_T.TRG2", "GESTAO.PKG_T.TRG1", edge_type="TRIGGER"),
    ]

    result = find_cycles(edges)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.via_trigger is True
    assert group.kind == "same_object"


def test_default_edge_types_constant_is_call_and_trigger():
    # TRIGGER_FIRES e o nome CANONICO do disparo de trigger no repo
    # (depgraph.EDGE_TYPES, que procgraph_access reusa); "TRIGGER" fica como
    # sinonimo aceito. Os dois precisam estar no default: um tipo de aresta de
    # trigger de fora do conjunto nao vira falso positivo, vira ciclo real que
    # NAO e reportado -- omissao silenciosa.
    assert DEFAULT_CYCLE_EDGE_TYPES == frozenset({"CALL", "TRIGGER_FIRES", "TRIGGER"})


def test_canonical_trigger_fires_edge_type_forms_a_cycle():
    # Regressao do acoplamento entre T-05 e T-07: enquanto T-05 nao existia,
    # o default so tinha o nome adivinhado "TRIGGER". Como depgraph.EDGE_TYPES
    # (reusado por procgraph_access) usa "TRIGGER_FIRES", um ciclo de trigger
    # real passaria despercebido. Este teste trava o nome canonico.
    edges = [
        _edge("GESTAO.T1.__TABLE__", "GESTAO.TRG1.__TRIGGER__", "TRIGGER_FIRES"),
        _edge("GESTAO.TRG1.__TRIGGER__", "GESTAO.T2.__TABLE__", "WRITE"),
        _edge("GESTAO.T2.__TABLE__", "GESTAO.TRG2.__TRIGGER__", "TRIGGER_FIRES"),
        _edge("GESTAO.TRG2.__TRIGGER__", "GESTAO.T1.__TABLE__", "WRITE"),
        # fecha o ciclo so por arestas de controle, sem depender de WRITE
        _edge("GESTAO.TRG1.__TRIGGER__", "GESTAO.TRG2.__TRIGGER__", "CALL"),
        _edge("GESTAO.TRG2.__TRIGGER__", "GESTAO.TRG1.__TRIGGER__", "TRIGGER_FIRES"),
    ]

    result = find_cycles(edges)

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.via_trigger is True
    assert set(group.members) == {"GESTAO.TRG1.__TRIGGER__", "GESTAO.TRG2.__TRIGGER__"}


# ------------------------------------------------------- grafo grande/profundo


def test_deep_chain_of_2000_nodes_does_not_overflow_recursion():
    # Cadeia de 2000 nos fechada em ciclo (N0 -> N1 -> ... -> N1999 -> N0):
    # uma implementacao RECURSIVA de Tarjan estouraria o limite de recursao
    # do Python bem antes disso. Prova o item 1 do contrato (pilha
    # explicita) e, de brinde, que o SCC inteiro (2000 membros) e detectado
    # corretamente.
    n = 2000
    refs = ["GESTAO.PKG_BIG.N{:04d}".format(i) for i in range(n)]
    edges = [_edge(refs[i], refs[(i + 1) % n]) for i in range(n)]

    result = _run_with_timeout(lambda: find_cycles(edges), timeout=10.0)

    assert isinstance(result, CycleDetectionResult)
    assert len(result.groups) == 1
    assert result.groups[0].size == n
    assert result.groups[0].kind == "same_object"


def test_deep_acyclic_chain_of_2000_nodes_produces_no_groups():
    # Mesma profundidade, mas SEM fechar o ciclo -- prova que a pilha
    # explicita tambem termina limpo (sem grupo nenhum) num grafo aciclico
    # profundo, nao so no caso ciclico acima.
    n = 2000
    refs = ["GESTAO.PKG_BIG.N{:04d}".format(i) for i in range(n)]
    edges = [_edge(refs[i], refs[i + 1]) for i in range(n - 1)]

    result = _run_with_timeout(lambda: find_cycles(edges), timeout=10.0)

    assert result.groups == []
