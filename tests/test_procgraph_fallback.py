"""Testes offline (T-04, contrato depgraph-granular) para o fallback de
grao OBJETO em plsqlflow/procgraph.py.

Nenhum destes casos toca rede/banco. Dois fakes, mesmo padrao de
tests/test_procgraph_bfs.py (motor fino) e tests/test_depgraph_bfs.py
(motor de objeto):

- `FakeProcExtractor` implementa `procgraph.ProcExtractor` (identifiers/
  statements/resolve_owner/object_wrapped) sobre uma fixture sintetica
  INLINE (sem arquivo JSON -- o conjunto de objetos deste teste e pequeno
  o bastante para caber direto no modulo, e T-04 nao esta autorizada a
  criar `tests/fixtures/*.json` novo).
- `FakeDepExtractor` implementa `depgraph.DepExtractor` (so os metodos que
  `_DepGraphEngine.run()`/`build_dep_graph` de fato chamam sem
  `expand_triggers`/`populate_table_columns`: deps_direct, object_catalog,
  plscope_check, synonym -- mesmo subconjunto que `SyntheticExtractor` de
  test_depgraph_bfs.py usa) -- e o extractor que o fallback de T-04 recebe
  via `build_proc_graph(..., dep_extractor=...)`.

Nota de projeto sobre `resolve_owner` (ver docstring de
plsqlflow/procgraph.py, secao "Resolucao inter-package"): a implementacao
real seria uma consulta pontual equivalente a
`SELECT owner, object_name FROM all_identifiers WHERE signature = :sig`,
mas isso so encontra o alvo se ELE MESMO tiver contribuido a
DECLARATION/DEFINITION com aquela signature -- o que e IMPOSSIVEL para um
objeto sem PL/Scope (ele nunca escreve nada em ALL_IDENTIFIERS). Este
fake, portanto, resolve `resolve_owner` por um mapa EXPLICITO
(signature -> owner/objeto) em vez de derivar so das arvores de
identifiers: e o jeito honesto de representar que a consulta real
provavelmente cruza uma fonte mais ampla (ex.: ALL_DEPENDENCIES do
CHAMADOR + ALL_PROCEDURES do alvo, nao PL/Scope) -- capacidade fora do
escopo desta tarefa (so `plsqlflow/procgraph.py` mesmo), mas que precisa
existir de algum jeito para o fallback ser alcancavel via CALL.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from plsqlflow import extract
from plsqlflow.procgraph import ProcGraphResult, build_proc_graph

# --------------------------------------------------------------------------
# Fixture inline (motor fino): "trees" no mesmo formato de
# tests/fixtures/procgraph_demo.json -- {"object_type", "identifiers",
# "statements"}. Cada objeto so precisa de PACKAGE BODY (nenhuma raiz deste
# arquivo usa semeadura de 2 partes, entao SPEC nunca e necessaria).
# --------------------------------------------------------------------------


def _id_row(usage_id, ctx, line, col, name, type_, usage, sig=None):
    return {
        "usage_id": usage_id,
        "usage_context_id": ctx,
        "line": line,
        "col": col,
        "name": name,
        "type": type_,
        "usage": usage,
        "signature": sig,
    }


def _pkg_body(pkg_name: str, proc_name: str, proc_sig: str, calls: Sequence[Tuple[str, str]] = ()) -> Dict[str, Any]:
    """Monta uma PACKAGE BODY com UMA procedure publica (`proc_name`,
    `usage_context_id=1`) que faz zero ou mais CALLs (`calls`: lista de
    (nome_chamado, signature_chamada), usage_context_id=2, 3, ...).
    Cobre A/CYC_A/D/D2 (todos "chamador com 1 CALL") -- X/Z/PARTIAL usam
    `calls=()` (folha)."""
    identifiers = [
        _id_row(1, 0, 1, 1, pkg_name, "PACKAGE", "DEFINITION"),
        _id_row(2, 1, 3, 1, proc_name, "PROCEDURE", "DEFINITION", proc_sig),
    ]
    next_id = 3
    for called_name, called_sig in calls:
        identifiers.append(_id_row(next_id, 2, 4, 3, called_name, "PROCEDURE", "CALL", called_sig))
        next_id += 1
    return {"trees": [{"object_type": "PACKAGE BODY", "identifiers": identifiers, "statements": []}]}


def _empty_object() -> Dict[str, Any]:
    """Objeto sem PL/Scope utilizavel: ALL_IDENTIFIERS/ALL_STATEMENTS
    vazios (nem sequer a DEFINITION da propria PACKAGE BODY) -- o estado
    real de um objeto compilado sem `IDENTIFIERS:ALL`."""
    return {"trees": [{"object_type": "PACKAGE BODY", "identifiers": [], "statements": []}]}


def _partial_object(pkg_name: str, proc_name: str, proc_sig: str) -> Dict[str, Any]:
    """Identifiers presentes, statements AUSENTES -- objeto PARCIAL:
    continua fino (NAO cai no fallback), com o gap de STATEMENTS declarado
    a parte (ver test_partial_plscope_object_stays_subprogram_grain)."""
    identifiers = [
        _id_row(1, 0, 1, 1, pkg_name, "PACKAGE", "DEFINITION"),
        _id_row(2, 1, 3, 1, proc_name, "PROCEDURE", "DEFINITION", proc_sig),
    ]
    return {"trees": [{"object_type": "PACKAGE BODY", "identifiers": identifiers, "statements": []}]}


PROC_OBJECTS: Dict[str, Dict[str, Any]] = {
    # cadeia anti-omissao (A fino -> B sem plscope -> C fino, so alcancavel
    # via ALL_DEPENDENCIES a partir de B, no lado do dep_extractor)
    "GESTAO.A": _pkg_body("A", "P1", "SIG_A_P1", calls=[("P1", "SIG_B_P1")]),
    "GESTAO.B": _empty_object(),
    # objeto PARCIAL -- identifiers sim, statements nao
    "GESTAO.PARTIAL": _partial_object("PARTIAL", "P1", "SIG_PARTIAL_P1"),
    # objeto wrapped -- motivo proprio, distinto de "sem identifiers"
    "GESTAO.W": _empty_object(),
    # ciclo atravessando o fallback: CYC_A -> CYC_B (sem plscope) -> CYC_A
    "GESTAO.CYC_A": _pkg_body("CYC_A", "P1", "SIG_CYC_A_P1", calls=[("P1", "SIG_CYC_B_P1")]),
    "GESTAO.CYC_B": _empty_object(),
    # teste 5: objeto ja visitado pelo motor fino (X) nao pode ser
    # retocado pelo fallback disparado por N (sem plscope)
    "GESTAO.D": _pkg_body("D", "P1", "SIG_D_P1", calls=[("P1", "SIG_D_X")]),
    "GESTAO.X": _pkg_body("X", "P1", "SIG_D_X"),  # folha; DEFINITION carrega a MESMA signature que D chama
    "GESTAO.N": _empty_object(),
    # teste 6: objeto ja expandido pelo fallback (Z, via M sem plscope)
    # nao pode ser relido pelo motor fino quando D2 tenta chamar Z depois
    "GESTAO.M": _empty_object(),
    "GESTAO.Z": _pkg_body("Z", "P1", "SIG_D2_Z"),  # folha
    "GESTAO.D2": _pkg_body("D2", "P1", "SIG_D2_P1", calls=[("P1", "SIG_D2_Z")]),
}

# resolve_owner: mapa EXPLICITO signature -> (owner, objeto) -- ver nota de
# projeto na docstring do modulo. Cobre TANTO objetos com PL/Scope (B/CYC_B/
# N/M nao aparecem aqui como ALVO porque eles proprios nunca sao chamados
# por signature neste fixture -- so aparecem como ORIGEM) QUANTO os que nao
# tem (B, CYC_B), que e exatamente o caso que T-04 precisa resolver.
SIGNATURE_OWNER: Dict[str, Tuple[str, str]] = {
    "SIG_B_P1": ("GESTAO", "B"),
    "SIG_CYC_B_P1": ("GESTAO", "CYC_B"),
    "SIG_D_X": ("GESTAO", "X"),
    "SIG_D2_Z": ("GESTAO", "Z"),
}

WRAPPED_OBJECTS = {"GESTAO.W"}


class FakeProcExtractor:
    """Fake de `procgraph.ProcExtractor` -- mesmo padrao de
    tests/test_procgraph_bfs.py::FakeExtractor, acrescido de
    `object_wrapped` (T-04) e `resolve_owner` alimentado por
    SIGNATURE_OWNER (ver nota de projeto na docstring do modulo) em vez de
    derivado das arvores."""

    def __init__(self) -> None:
        self.objects = PROC_OBJECTS
        self.id_calls: Dict[str, int] = {}
        self.stmt_calls: Dict[str, int] = {}

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
        return SIGNATURE_OWNER.get(signature)

    def object_wrapped(self, owner: str, object_name: str) -> bool:
        return self._ref(owner, object_name) in WRAPPED_OBJECTS


# --------------------------------------------------------------------------
# Fake do lado do fallback (_DepGraphEngine reusado): so os metodos que
# `_DepGraphEngine.run()` de fato chama sem expand_triggers/
# populate_table_columns -- mesmo subconjunto de
# tests/test_depgraph_bfs.py::SyntheticExtractor.
# --------------------------------------------------------------------------

# owner -> lista de (object_name, object_type, status)
CATALOG: Dict[str, List[Tuple[str, str, str]]] = {
    "GESTAO": [
        ("B", "PACKAGE BODY", "VALID"),
        ("C", "PACKAGE BODY", "VALID"),
        ("W", "PACKAGE BODY", "VALID"),
        ("CYC_B", "PACKAGE BODY", "VALID"),
        ("N", "PACKAGE BODY", "VALID"),
        ("M", "PACKAGE BODY", "VALID"),
        ("X", "PACKAGE BODY", "VALID"),
        ("Z", "PACKAGE BODY", "VALID"),
    ],
}

# objetos com PL/Scope COMPLETO do lado do dep_extractor (IDENTIFIERS:ALL +
# STATEMENTS:ALL) -- os demais do CATALOG entram em needs_recompile.
DEP_PLSCOPE_FULL = {"C", "X", "Z"}

# (owner, name) -> lista de dependencias diretas (owner, name, type)
DEPS_DIRECT: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = {
    ("GESTAO", "B"): [("GESTAO", "C", "PACKAGE")],
    ("GESTAO", "C"): [],
    ("GESTAO", "W"): [],
    ("GESTAO", "CYC_B"): [("GESTAO", "CYC_A", "PACKAGE")],
    ("GESTAO", "N"): [("GESTAO", "X", "PACKAGE")],
    ("GESTAO", "M"): [("GESTAO", "Z", "PACKAGE")],
    ("GESTAO", "Z"): [],
    ("GESTAO", "X"): [],
}


class FakeDepExtractor:
    """Fake de `depgraph.DepExtractor` para o fallback de grao objeto (T-04)
    -- mesmo padrao de tests/test_depgraph_bfs.py::SyntheticExtractor, com
    contadores de chamada POR CHAVE (owner, name) para provar que um objeto
    ja coberto pelo motor fino nunca e retocado por este extractor (item 1
    de "TRADUCAO DE VISITED-SET" na docstring de plsqlflow/procgraph.py)."""

    def __init__(self) -> None:
        self.deps_direct_calls: List[Tuple[str, str]] = []
        self.object_catalog_calls: List[str] = []
        self.plscope_check_calls: List[str] = []
        self.synonym_calls: List[Tuple[str, str]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        key = (owner.upper(), name.upper())
        self.deps_direct_calls.append(key)
        rows = DEPS_DIRECT.get(key, [])
        return [
            extract.DepsDirectRow(
                referenced_owner=o, referenced_name=n, referenced_type=t, dependency_type="HARD"
            )
            for (o, n, t) in rows
        ]

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        self.object_catalog_calls.append(owner.upper())
        return [
            extract.ObjectCatalogRow(
                owner=owner.upper(), object_name=name, object_type=type_, status=status,
                last_ddl_time="2026-08-15 10:00:00",
            )
            for (name, type_, status) in CATALOG.get(owner.upper(), [])
        ]

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        self.plscope_check_calls.append(owner.upper())
        rows = []
        for (name, type_, _status) in CATALOG.get(owner.upper(), []):
            settings = "IDENTIFIERS:ALL,STATEMENTS:ALL" if name in DEP_PLSCOPE_FULL else None
            rows.append(
                extract.PlscopeCheckRow(owner=owner.upper(), name=name, type=type_, plscope_settings=settings)
            )
        return rows

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        self.synonym_calls.append((owner.upper(), name.upper()))
        return []


def _run_with_timeout(fn, timeout: float = 5.0):
    """Mesmo arnes de tests/test_procgraph_bfs.py::_run_with_timeout --
    duplicado aqui de proposito (arquivo isolado, sem import cruzado entre
    modulos de teste): roda a travessia numa thread separada para provar
    que ela devolve controle sozinha. O motor NUNCA usa timeout como
    mecanismo de parada -- isto e so o arnes do teste."""
    box: Dict[str, Any] = {}

    def _target() -> None:
        box["result"] = fn()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "travessia nao terminou dentro do timeout -- possivel loop infinito"
    return box["result"]


def _node(result: ProcGraphResult, ref: str):
    for n in result.nodes:
        candidate = "{}.{}".format(n.owner, n.object_name)
        if n.subprogram:
            candidate = "{}.{}".format(candidate, n.subprogram)
        if candidate == ref:
            return n
    return None


def _edges_from(result: ProcGraphResult, from_ref: str) -> List:
    return [e for e in result.edges if e.from_ref == from_ref]


# --------------------------------------------------------- anti-omissao (1)


def test_chain_a_b_c_c_appears_and_b_is_marked_object_grain():
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()

    result = build_proc_graph(
        proc_extractor, ("GESTAO", "A", "P1"), dep_extractor=dep_extractor
    )

    # C e o cerne do teste: alcancavel SO atraves do fallback de B (sem
    # PL/Scope) -- se o motor omitisse, C simplesmente nao apareceria.
    c_node = _node(result, "GESTAO.C")
    assert c_node is not None, "C tem que aparecer no grafo -- anti-omissao"
    assert c_node.grain == "object"

    b_node = _node(result, "GESTAO.B")
    assert b_node is not None
    assert b_node.grain == "object"
    assert b_node.note is not None and "PL/Scope" in b_node.note

    assert "GESTAO.B" in result.needs_recompile

    # A continua fino -- o fallback de B nao pode contaminar o chamador.
    a_node = _node(result, "GESTAO.A.P1")
    assert a_node is not None
    assert a_node.grain == "subprogram"

    # a aresta de A para o fallback aponta pro no de 2 partes de B.
    a_targets = {e.to_ref for e in _edges_from(result, "GESTAO.A.P1")}
    assert "GESTAO.B" in a_targets

    # e a aresta de B (grao objeto, via ALL_DEPENDENCIES) chega ate C.
    b_targets = {e.to_ref for e in _edges_from(result, "GESTAO.B")}
    assert "GESTAO.C" in b_targets


def test_chain_a_b_c_determinism_two_runs_identical():
    result1 = build_proc_graph(
        FakeProcExtractor(), ("GESTAO", "A", "P1"), dep_extractor=FakeDepExtractor()
    )
    result2 = build_proc_graph(
        FakeProcExtractor(), ("GESTAO", "A", "P1"), dep_extractor=FakeDepExtractor()
    )

    assert result1.nodes == result2.nodes
    assert result1.edges == result2.edges
    assert result1.not_expanded == result2.not_expanded
    assert result1.blind_spots == result2.blind_spots
    assert result1.needs_recompile == result2.needs_recompile
    assert result1.stats == result2.stats


# ---------------------------------------------------- PL/Scope parcial (2)


def test_partial_plscope_object_stays_subprogram_grain():
    # CORRECAO DE BLOQUEANTE (verificacao independente, rodada 5): esta
    # fixture ja modelava "identifiers sim, statements nao" (docstring de
    # `_partial_object`), mas a versao anterior deste teste afirmava
    # `blind_spots == []` -- o gap nunca era declarado em lugar nenhum
    # (nem blind_spots, nem needs_recompile), e o teste passava porque
    # NADA declarava o gap, nao porque o gap nao existisse. E exatamente o
    # padrao "nome do teste promete mais do que o corpo prova" apontado
    # pela verificacao: o nome diz "stays subprogram grain" (verdadeiro) e
    # ficava silencioso sobre a ausencia de declaracao (o defeito real).
    #
    # Sem dep_extractor de proposito: se o motor tentasse (por engano) cair
    # em fallback aqui, a ausencia de dep_extractor declararia o gap via
    # `truncated=True` -- as asserts abaixo provam que isso NUNCA acontece
    # para um objeto so PARCIAL (identifiers sim, statements nao): ele
    # continua em grao SUBPROGRAMA (CALL atribuida normalmente), e o gap de
    # STATEMENTS e declarado A PARTE (blind_spots + needs_recompile),
    # nunca via rebaixamento nem via silencio.
    proc_extractor = FakeProcExtractor()

    result = build_proc_graph(proc_extractor, ("GESTAO", "PARTIAL", "P1"))

    node = _node(result, "GESTAO.PARTIAL.P1")
    assert node is not None
    assert node.grain == "subprogram"
    assert node.plscope_identifiers is True
    assert node.plscope_statements is False

    assert result.truncated is False
    assert result.blind_spots == [
        "GESTAO.PARTIAL -> ? (objeto sem STATEMENTS:ALL -- acesso a tabela/estado/trigger/SQL "
        "dinamico deste objeto e invisivel ao PL/Scope; CALLs continuam atribuidas normalmente, "
        "so o que vem de STATEMENT esta faltando aqui)"
    ]
    assert result.needs_recompile == ["GESTAO.PARTIAL"]


def test_partial_plscope_gap_declared_once_per_object_not_per_subprogram():
    # O gap e do OBJETO (a arvore PL/Scope e lida e cacheada uma vez por
    # objeto -- ver `_load_object`), nao de cada subprograma que sai dele.
    # `PARTIAL` so tem um subprograma publico nesta fixture, entao a prova
    # de dedup passa pelo numero de LEITURAS do objeto (`id_calls`/
    # `stmt_calls`, mesmo contador ja usado por
    # `test_cache_reads_flow_demo_object_exactly_once` em
    # tests/test_procgraph_bfs.py): se `_load_object` fosse chamado mais
    # de uma vez para o mesmo objeto, a declaracao duplicaria junto.
    proc_extractor = FakeProcExtractor()

    result = build_proc_graph(proc_extractor, ("GESTAO", "PARTIAL"))

    partial_spots = [s for s in result.blind_spots if s.startswith("GESTAO.PARTIAL ->")]
    assert len(partial_spots) == 1
    assert result.needs_recompile.count("GESTAO.PARTIAL") == 1
    assert proc_extractor.id_calls.get("GESTAO.PARTIAL") == 1
    assert proc_extractor.stmt_calls.get("GESTAO.PARTIAL") == 1


def test_full_statements_all_but_zero_sql_is_not_a_false_gap():
    # Falso-positivo que a correcao precisa EVITAR (achado ao vivo contra
    # GESTAO_OO.T_PARECER_COMPLIANCE.ADICIONAR_METRICA, metodo de TYPE
    # cujo corpo e so manipulacao de colecao, sem SELECT/INSERT/EXECUTE
    # IMMEDIATE nenhum -- STATEMENTS:ALL estava plenamente ativo e
    # ALL_STATEMENTS tinha 0 linhas, por natureza, nao por falta de
    # cobertura). `GESTAO.X` na fixture modela o mesmo formato: esta em
    # DEP_PLSCOPE_FULL (settings dizem STATEMENTS:ALL) mas o objeto tem
    # `statements=[]` em PROC_OBJECTS (zero SQL no corpo). COM
    # dep_extractor presente, a settings REAL prevalece sobre a inferencia
    # por presenca de linha -- nenhum ponto cego falso, nenhuma entrada
    # falsa em needs_recompile.
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()

    result = build_proc_graph(
        proc_extractor, ("GESTAO", "X", "P1"), dep_extractor=dep_extractor
    )

    node = _node(result, "GESTAO.X.P1")
    assert node is not None
    assert node.grain == "subprogram"
    assert node.plscope_identifiers is True
    assert node.plscope_statements is True  # settings prevalecem, nao bool([])

    assert not any(s.startswith("GESTAO.X ->") for s in result.blind_spots)
    assert "GESTAO.X" not in result.needs_recompile


# --------------------------------------------------------------- wrapped (3)


def test_wrapped_object_falls_back_with_its_own_reason():
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()

    result = build_proc_graph(proc_extractor, ("GESTAO", "W", "P1"), dep_extractor=dep_extractor)

    node = _node(result, "GESTAO.W")
    assert node is not None
    assert node.grain == "object"
    assert node.note is not None
    assert "wrapped" in node.note.lower()

    assert "GESTAO.W" in result.needs_recompile


# ------------------------------------------------------------- terminacao (4)


def test_cycle_crossing_fallback_terminates_with_cycle_declared():
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()

    result = _run_with_timeout(
        lambda: build_proc_graph(
            proc_extractor, ("GESTAO", "CYC_A", "P1"), dep_extractor=dep_extractor
        ),
        timeout=5.0,
    )

    edge_pairs = {(e.from_ref, e.to_ref) for e in result.edges}
    # CYC_A.P1 -> fallback de CYC_B (aresta de CALL redirecionada, T-04)
    assert ("GESTAO.CYC_A.P1", "GESTAO.CYC_B") in edge_pairs
    # CYC_B -> CYC_A (aresta generica do _DepGraphEngine; CYC_A ja e
    # territorio fino, entao o fallback so registra a aresta, nao reenfileira)
    assert ("GESTAO.CYC_B", "GESTAO.CYC_A") in edge_pairs

    # nenhum no duplicado -- visited-set compartilhado segurou o ciclo.
    assert sum(1 for n in result.nodes if n.owner == "GESTAO" and n.object_name == "CYC_A" and n.subprogram == "P1") == 1
    assert sum(1 for n in result.nodes if n.owner == "GESTAO" and n.object_name == "CYC_B" and n.grain == "object") == 1


# --------------------------------------------- visited-set compartilhado (5, 6)


def test_fine_visited_object_not_reexpanded_by_fallback():
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()

    # D (fino) e processado ANTES de N (sem plscope) -- root primeiro,
    # extra-root depois, FIFO garante a ordem. D.P1 chama X (resolve por
    # signature, reclamando X para o territorio fino no ENQUEUE) antes de N
    # ser desenfileirado e disparar fallback -- N tambem depende de X via
    # ALL_DEPENDENCIES, e esse e o ponto que este teste prova nunca tocar.
    result = build_proc_graph(
        proc_extractor,
        ("GESTAO", "D", "P1"),
        roots=[("GESTAO", "N", "P1")],
        dep_extractor=dep_extractor,
    )

    x_fine = _node(result, "GESTAO.X.P1")
    assert x_fine is not None
    assert x_fine.grain == "subprogram"

    # X NUNCA foi tratado como no de fallback (2 partes) -- prova de que o
    # motor de objeto nao o retocou.
    assert _node(result, "GESTAO.X") is None

    # prova definitiva: o _DepGraphEngine reusado nunca chamou deps_direct
    # para X -- ele nem chegou a ser enfileirado la (visited pre-populado).
    assert ("GESTAO", "X") not in dep_extractor.deps_direct_calls

    n_node = _node(result, "GESTAO.N")
    assert n_node is not None and n_node.grain == "object"
    assert "GESTAO.N" in result.needs_recompile


def test_fallback_expanded_object_not_reprocessed_by_fine_engine():
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()

    # M (sem plscope) e processado ANTES de D2 -- o fallback de M alcanca Z
    # (via ALL_DEPENDENCIES) e o transforma num no de 2 partes ANTES de D2
    # tentar resolver sua propria CALL para Z por signature.
    result = build_proc_graph(
        proc_extractor,
        ("GESTAO", "M", "P1"),
        roots=[("GESTAO", "D2", "P1")],
        dep_extractor=dep_extractor,
    )

    z_fallback = _node(result, "GESTAO.Z")
    assert z_fallback is not None
    assert z_fallback.grain == "object"

    # Z nunca virou no de subprograma -- a CALL de D2 foi redirecionada
    # para o no de fallback existente, sem re-resolver.
    assert _node(result, "GESTAO.Z.P1") is None

    # prova definitiva: o motor fino NUNCA releu Z via plscope_identifiers.
    assert proc_extractor.id_calls.get("GESTAO.Z", 0) == 0

    d2_targets = {e.to_ref for e in _edges_from(result, "GESTAO.D2.P1")}
    assert "GESTAO.Z" in d2_targets


# ------------------------------------------------- degradado (sem dep_extractor)


def test_fallback_without_dep_extractor_declares_gap_never_omits():
    proc_extractor = FakeProcExtractor()

    # Sem dep_extractor: o fallback de B nao pode consultar
    # ALL_DEPENDENCIES -- a regra dura ("nunca omite em silencio") exige que
    # isso fique DECLARADO (truncated + not_expanded + blind_spots), nunca
    # finja cobertura nem derrube a travessia inteira com excecao.
    result = build_proc_graph(proc_extractor, ("GESTAO", "A", "P1"))

    b_node = _node(result, "GESTAO.B")
    assert b_node is not None
    assert b_node.grain == "object"
    assert b_node.is_leaf is True

    assert result.truncated is True
    assert result.truncation_reason is not None
    assert "GESTAO.B" in result.not_expanded
    assert any("GESTAO.B" in item for item in result.blind_spots)

    # A continua integro mesmo com o gap declarado em B.
    a_node = _node(result, "GESTAO.A.P1")
    assert a_node is not None and a_node.grain == "subprogram"
