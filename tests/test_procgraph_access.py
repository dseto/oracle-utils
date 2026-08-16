"""Testes offline (T-05, contrato depgraph-granular) para
plsqlflow/procgraph_access.py -- o seam de acesso que
plsqlflow/procgraph.py ja chama (`expand_access`).

Nenhum caso toca rede/banco. O golden de READ/WRITE reusa a arvore REAL de
GESTAO.FLOW_DEMO (tests/fixtures/plscope_tree.json, T-01) -- inclusive o
texto literal do INSERT de LOG_MSG e do INSERT do trigger
TRG_FLOW_DEMO_LOG, ambos colados diretamente do fonte lido no banco dev em
2026-08-15 (`all_source`), para a comparacao contra o regex antigo ser
honesta. Estado de package e a descoberta de trigger usam fixtures
sinteticas pequenas construidas inline (mesmo padrao de
tests/test_procgraph_bfs.py para os casos que a arvore real nao cobre).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from plsqlflow import depgraph_enrich
from plsqlflow.attribute import Assignment, IdentifierRow, assign_context
from plsqlflow.procgraph import ProcEdge, build_proc_graph
from plsqlflow.procgraph_access import expand_access

REPO = Path(__file__).resolve().parent.parent
FLOW_DEMO_FIXTURE = REPO / "tests" / "fixtures" / "plscope_tree.json"


def _load_flow_demo() -> Dict[str, Any]:
    return json.loads(FLOW_DEMO_FIXTURE.read_text(encoding="utf-8"))


def _identifiers_from(rows: List[Dict[str, Any]]) -> List[IdentifierRow]:
    return [
        IdentifierRow(
            usage_id=row["usage_id"],
            usage_context_id=row["usage_context_id"],
            line=row["line"],
            col=row["col"],
            name=row["name"],
            type=row["type"],
            usage=row["usage"],
            signature=row.get("signature"),
        )
        for row in rows
    ]


def _flow_demo_cache() -> SimpleNamespace:
    """Duck-typed `_ObjectCache` minimo -- so os dois atributos que
    `procgraph_access` le (`body_identifiers`/`body_type`). Deliberadamente
    NAO importa a dataclass privada de procgraph.py (mesmo desacoplamento
    que o proprio modulo sob teste pratica)."""
    fixture = _load_flow_demo()
    return SimpleNamespace(
        body_identifiers=_identifiers_from(fixture["identifiers"]),
        body_type=fixture["object_type"],
    )


def _assignment_for(fixture: Dict[str, Any], usage_id: int) -> Assignment:
    identifiers = _identifiers_from(fixture["identifiers"])
    statements = [
        # StatementRow shape esperado por assign_context -- reconstrucao
        # minima, mesmo padrao de tests/test_attribute.py.
        SimpleNamespace(
            usage_id=row["usage_id"],
            usage_context_id=row["usage_context_id"],
            line=row["line"],
            stmt_type=row["stmt_type"],
        )
        for row in fixture["statements"]
    ]
    from plsqlflow.attribute import StatementRow

    stmt_rows = [
        StatementRow(usage_id=s.usage_id, usage_context_id=s.usage_context_id, line=s.line, stmt_type=s.stmt_type)
        for s in statements
    ]
    assignments = assign_context(identifiers, stmt_rows, fixture["object_type"])
    return next(a for a in assignments if a.usage_id == usage_id)


# --------------------------------------------------------------- entrega 1


def test_log_msg_write_has_compiler_column_msg():
    # PROVA do contrato: LOG_MSG escreve em FLOW_DEMO_LOG com a coluna MSG
    # vinda do compilador (usage_id=6 = o INSERT, linha 6, secao 2 do
    # plano).
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)
    assert insert_assignment.kind == "STMT"
    assert insert_assignment.enclosing == "LOG_MSG"

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
    )

    write_edges = [e for e in edges if e.edge_type == "WRITE"]
    assert len(write_edges) == 1
    edge = write_edges[0]
    assert isinstance(edge, ProcEdge)
    assert edge.from_ref == "GESTAO.FLOW_DEMO.LOG_MSG"
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.op == "INSERT"
    assert edge.cols == ["MSG"]
    assert edge.line == 6


def test_main_never_gets_a_write_edge_to_flow_demo_log():
    # Segunda metade da prova: MAIN NAO pode ter essa aresta. MAIN so tem
    # CALLs no golden (secao 2 do plano) -- nenhum Assignment kind=STMT
    # atribuido a MAIN, entao o seam nunca e chamado com statement para
    # MAIN, e a unica outra forma de chamada (statement=None, entrega 2)
    # nunca produz READ/WRITE. As duas verificacoes ficam explicitas
    # abaixo.
    fixture = _load_flow_demo()
    identifiers = _identifiers_from(fixture["identifiers"])
    from plsqlflow.attribute import StatementRow

    stmt_rows = [
        StatementRow(usage_id=s["usage_id"], usage_context_id=s["usage_context_id"], line=s["line"], stmt_type=s["stmt_type"])
        for s in fixture["statements"]
    ]
    assignments = assign_context(identifiers, stmt_rows, fixture["object_type"])
    main_stmt_assignments = [a for a in assignments if a.enclosing == "MAIN" and a.kind == "STMT"]
    assert main_stmt_assignments == []

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="MAIN",
        statement=None,
        object_cache=_flow_demo_cache(),
    )
    assert not any(e.edge_type in ("READ", "WRITE") for e in edges)


def test_compiler_columns_at_least_as_complete_as_regex():
    # Comparacao explicita contra o regex antigo (depgraph_enrich, NAO
    # tocado). Caso 1: com o texto real do INSERT (colado do fonte lido em
    # GESTAO.FLOW_DEMO no banco dev), regex e compilador batem -- mesmo
    # conjunto de colunas (case-insensitive: o fonte escreve "msg" minusculo,
    # o compilador reporta "MSG" -- ALL_IDENTIFIERS normaliza identificador
    # nao-quotado para maiusculo). Caso 2: SEM o texto (regex.
    # _extract_write_cols exige `text`; o compilador nao precisa de texto
    # nenhum, so da arvore PL/Scope) o regex nao acha NADA enquanto o
    # compilador continua achando a coluna -- a demonstracao mais direta de
    # "nunca menos" que este contrato pede.
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)
    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
    )
    compiler_cols = {c.upper() for c in (next(e for e in edges if e.edge_type == "WRITE").cols or [])}

    real_source_text = "INSERT INTO flow_demo_log (msg) VALUES (p_msg);"
    regex_cols_with_text = {c.upper() for c in (depgraph_enrich._extract_write_cols(real_source_text, "INSERT") or [])}
    assert regex_cols_with_text <= compiler_cols
    assert regex_cols_with_text == {"MSG"}
    assert compiler_cols == {"MSG"}

    regex_cols_without_text = depgraph_enrich._extract_write_cols(None, "INSERT")
    assert regex_cols_without_text is None  # regex fica sem NADA sem o texto-fonte
    assert compiler_cols == {"MSG"}  # compilador nao depende de texto nenhum


def test_trigger_body_write_has_compiler_columns_in_source_order():
    # Segunda evidencia real (GESTAO.TRG_FLOW_DEMO_LOG, fonte lido do banco
    # dev): "INSERT INTO flow_demo_audit (log_id, acao) VALUES (...)" --
    # prova que a ordem das colunas vem de (line, col), nao de usage_id
    # (LOG_ID tem usage_id MAIOR que ACAO na arvore real, mas aparece
    # PRIMEIRO no texto e tem que aparecer primeiro na lista).
    identifiers = [
        IdentifierRow(1, 0, 1, 9, "TRG_FLOW_DEMO_LOG", "TRIGGER", "DECLARATION", "CCCF5F75225193E6C290768FFF7B5C96"),
        IdentifierRow(2, 1, 1, 9, "TRG_FLOW_DEMO_LOG", "TRIGGER", "DEFINITION", "CCCF5F75225193E6C290768FFF7B5C96"),
        IdentifierRow(4, 3, 5, 15, "FLOW_DEMO_AUDIT", "TABLE", "REFERENCE"),
        IdentifierRow(5, 3, 5, 40, "ACAO", "COLUMN", "REFERENCE"),
        IdentifierRow(6, 3, 5, 32, "LOG_ID", "COLUMN", "REFERENCE"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="TRIGGER")
    statement = Assignment(usage_id=3, line=5, kind="STMT", target="INSERT", enclosing="TRG_FLOW_DEMO_LOG")

    edges = expand_access(
        owner="GESTAO",
        object_name="TRG_FLOW_DEMO_LOG",
        subprogram="TRG_FLOW_DEMO_LOG",
        statement=statement,
        object_cache=cache,
    )
    write_edges = [e for e in edges if e.edge_type == "WRITE"]
    assert len(write_edges) == 1
    assert write_edges[0].to_ref == "GESTAO.FLOW_DEMO_AUDIT"
    assert write_edges[0].cols == ["LOG_ID", "ACAO"]


def test_read_edge_for_select_statement():
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "READER", "PROCEDURE", "DEFINITION", "SIG_READER"),
        IdentifierRow(4, 3, 5, 10, "SOME_TABLE", "TABLE", "REFERENCE"),
        IdentifierRow(5, 3, 5, 20, "SOME_COL", "COLUMN", "REFERENCE"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY")
    statement = Assignment(usage_id=3, line=5, kind="STMT", target="SELECT", enclosing="READER")

    edges = expand_access(
        owner="GESTAO", object_name="PKG", subprogram="READER", statement=statement, object_cache=cache
    )
    read_edges = [e for e in edges if e.edge_type == "READ"]
    assert len(read_edges) == 1
    assert read_edges[0].to_ref == "GESTAO.SOME_TABLE"
    assert read_edges[0].op is None


def test_unknown_target_marker_when_no_table_found():
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "P", "PROCEDURE", "DEFINITION", "SIG_P"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY")
    statement = Assignment(usage_id=3, line=5, kind="STMT", target="SELECT", enclosing="P")

    edges = expand_access(owner="GESTAO", object_name="PKG", subprogram="P", statement=statement, object_cache=cache)
    assert len(edges) == 1
    assert edges[0].to_ref == depgraph_enrich.UNKNOWN_TARGET


# --------------------------------------------------------------- entrega 2


def _pkg_state_identifiers() -> List[IdentifierRow]:
    """Fixture sintetica: PKG_STATE com estado de package (G_COUNTER),
    subprograma WRITER (assina) e READER (le), mais um subprograma
    LOCAL_ONLY com variavel LOCAL homonima do TIPO estado (VARIABLE) mas
    declarada dentro de si mesmo -- negativo do contrato (nunca vira
    estado compartilhado)."""
    return [
        IdentifierRow(1, 0, 1, 1, "PKG_STATE", "PACKAGE", "DEFINITION"),
        # G_COUNTER declarado direto sob a raiz (contexto=1=raiz) -- estado.
        IdentifierRow(2, 1, 2, 3, "G_COUNTER", "VARIABLE", "DECLARATION"),
        IdentifierRow(3, 2, 2, 20, "NUMBER", "NUMBER DATATYPE", "REFERENCE"),
        # WRITER: assina G_COUNTER.
        IdentifierRow(10, 1, 5, 1, "WRITER", "PROCEDURE", "DEFINITION", "SIG_WRITER"),
        IdentifierRow(11, 10, 6, 5, "G_COUNTER", "VARIABLE", "ASSIGNMENT"),
        # READER: le G_COUNTER.
        IdentifierRow(20, 1, 9, 1, "READER", "PROCEDURE", "DEFINITION", "SIG_READER"),
        IdentifierRow(21, 20, 10, 5, "G_COUNTER", "VARIABLE", "REFERENCE"),
        # LOCAL_ONLY: V_LOCAL declarada e assinada DENTRO de si mesma --
        # jamais estado, mesmo sendo VARIABLE.
        IdentifierRow(30, 1, 13, 1, "LOCAL_ONLY", "PROCEDURE", "DEFINITION", "SIG_LOCAL_ONLY"),
        IdentifierRow(31, 30, 14, 5, "V_LOCAL", "VARIABLE", "DECLARATION"),
        IdentifierRow(32, 31, 14, 15, "NUMBER", "NUMBER DATATYPE", "REFERENCE"),
        IdentifierRow(33, 30, 15, 5, "V_LOCAL", "VARIABLE", "ASSIGNMENT"),
        # G_UNUSED: declarado sob a raiz, NUNCA referenciado/assinado em
        # lugar nenhum -- prova que DECLARATION sozinha nao gera aresta.
        IdentifierRow(40, 1, 17, 3, "G_UNUSED", "VARIABLE", "DECLARATION"),
    ]


def _pkg_state_cache() -> SimpleNamespace:
    return SimpleNamespace(body_identifiers=_pkg_state_identifiers(), body_type="PACKAGE BODY")


def test_state_write_edge_for_package_variable():
    edges = expand_access(
        owner="GESTAO", object_name="PKG_STATE", subprogram="WRITER", statement=None, object_cache=_pkg_state_cache()
    )
    state_edges = [e for e in edges if e.edge_type in ("STATE_READ", "STATE_WRITE")]
    assert len(state_edges) == 1
    edge = state_edges[0]
    assert edge.edge_type == "STATE_WRITE"
    assert edge.from_ref == "GESTAO.PKG_STATE.WRITER"
    assert edge.to_ref == "GESTAO.PKG_STATE.__STATE__"
    assert edge.line == 6


def test_state_read_edge_for_package_variable():
    edges = expand_access(
        owner="GESTAO", object_name="PKG_STATE", subprogram="READER", statement=None, object_cache=_pkg_state_cache()
    )
    state_edges = [e for e in edges if e.edge_type in ("STATE_READ", "STATE_WRITE")]
    assert len(state_edges) == 1
    edge = state_edges[0]
    assert edge.edge_type == "STATE_READ"
    assert edge.from_ref == "GESTAO.PKG_STATE.READER"
    assert edge.to_ref == "GESTAO.PKG_STATE.__STATE__"


def test_local_variable_never_becomes_state_synthetic():
    # Negativo do contrato: LOCAL_ONLY assina V_LOCAL (mesmo TIPO de
    # estado, VARIABLE), mas a DECLARATION dela esta dentro do proprio
    # LOCAL_ONLY (contexto=30=a DEFINITION do subprograma), nao sob a
    # raiz do PACKAGE -- nunca pode virar STATE_WRITE.
    edges = expand_access(
        owner="GESTAO",
        object_name="PKG_STATE",
        subprogram="LOCAL_ONLY",
        statement=None,
        object_cache=_pkg_state_cache(),
    )
    assert not any(e.edge_type in ("STATE_READ", "STATE_WRITE") for e in edges)


def test_local_variable_never_becomes_state_real_flow_demo():
    # Mesmo negativo, mas contra a arvore REAL (fato provado no plano,
    # secao 2 item 4): V_SQL e local a RUN_DYNAMIC (usage_id=28,
    # contexto=25=a DEFINITION de RUN_DYNAMIC) e e assinada em usage_id=31
    # -- nunca pode gerar STATE_WRITE mesmo sendo VARIABLE.
    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="RUN_DYNAMIC",
        statement=None,
        object_cache=_flow_demo_cache(),
    )
    assert not any(e.edge_type in ("STATE_READ", "STATE_WRITE") for e in edges)


def test_declaration_alone_generates_no_edge():
    # G_UNUSED e declarado sob a raiz do package mas NUNCA
    # referenciado/assinado -- nao ha subprograma nenhum que possa gerar
    # aresta para ele, porque so ASSIGNMENT/REFERENCE viram aresta
    # (DECLARATION sozinha nunca gera). Varrendo todos os subprogramas da
    # fixture, nenhuma aresta aponta para G_UNUSED em lugar nenhum.
    for subprogram in ("WRITER", "READER", "LOCAL_ONLY"):
        edges = expand_access(
            owner="GESTAO",
            object_name="PKG_STATE",
            subprogram=subprogram,
            statement=None,
            object_cache=_pkg_state_cache(),
        )
        assert all("G_UNUSED" not in (e.reason or "") for e in edges)
    # E, mais direto: nenhum STATE_* nasce so da DECLARATION -- a fixture
    # so contem os 2 edges esperados (WRITER->G_COUNTER, READER->G_COUNTER)
    # em todo o objeto, nunca 3.
    all_state_edges = []
    for subprogram in ("WRITER", "READER", "LOCAL_ONLY"):
        all_state_edges.extend(
            e
            for e in expand_access(
                owner="GESTAO",
                object_name="PKG_STATE",
                subprogram=subprogram,
                statement=None,
                object_cache=_pkg_state_cache(),
            )
            if e.edge_type in ("STATE_READ", "STATE_WRITE")
        )
    assert len(all_state_edges) == 2


# --------------------------------------------------------------- entrega 3


class _TriggerRow:
    def __init__(self, table_owner: str, table_name: str, trigger_name: str, triggering_event: str):
        self.table_owner = table_owner
        self.table_name = table_name
        self.trigger_name = trigger_name
        self.triggering_event = triggering_event


def test_write_discovers_trigger_and_returns_subprogram_shaped_edge():
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)

    class _Extractor:
        def triggers(self, owner: str, table_names: List[str]) -> List[_TriggerRow]:
            assert owner == "GESTAO"
            assert table_names == ["FLOW_DEMO_LOG"]
            return [_TriggerRow("GESTAO", "FLOW_DEMO_LOG", "TRG_FLOW_DEMO_LOG", "INSERT")]

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
        extractor=_Extractor(),
    )
    trigger_edges = [e for e in edges if e.edge_type == "TRIGGER_FIRES"]
    assert len(trigger_edges) == 1
    edge = trigger_edges[0]
    assert edge.from_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.to_ref == "GESTAO.TRG_FLOW_DEMO_LOG.TRG_FLOW_DEMO_LOG"  # ref de SUBPROGRAMA, 3 partes
    assert edge.op == "INSERT"


def test_no_trigger_edge_when_extractor_lacks_triggers_method():
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)
    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
        extractor=None,
    )
    assert not any(e.edge_type == "TRIGGER_FIRES" for e in edges)


# ---- integracao completa: ciclo de trigger via build_proc_graph ----


def _run_with_timeout(fn, timeout: float = 5.0):
    """Mesmo arnes de tests/test_procgraph_bfs.py::_run_with_timeout
    (duplicado deliberadamente -- teste nao importa de outro modulo de
    teste): prova de terminacao rodando a travessia numa thread separada.
    O motor NUNCA usa timeout como mecanismo de parada; isto e so o arnes
    do teste."""
    box: Dict[str, Any] = {}

    def _target() -> None:
        box["result"] = fn()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "travessia nao terminou dentro do timeout -- possivel loop infinito"
    return box["result"]


class _TriggerCycleExtractor:
    """Fake de `procgraph.ProcExtractor` (+`triggers`) para o ciclo
    T1 -> TRG1 escreve T2 -> TRG2 escreve T1. `objects` segue o mesmo
    formato de tests/fixtures/procgraph_demo.json (trees por
    object_type)."""

    def __init__(self):
        self.objects: Dict[str, Dict[str, Any]] = {
            "GESTAO.P_START": {
                "trees": [
                    {
                        "object_type": "PACKAGE BODY",
                        "identifiers": [
                            {"usage_id": 1, "usage_context_id": 0, "line": 1, "col": 1, "name": "P_START", "type": "PACKAGE", "usage": "DEFINITION", "signature": None},
                            {"usage_id": 2, "usage_context_id": 1, "line": 3, "col": 1, "name": "START", "type": "PROCEDURE", "usage": "DEFINITION", "signature": "SIG_START"},
                        ],
                        "statements": [
                            {"usage_id": 3, "usage_context_id": 2, "line": 4, "stmt_type": "INSERT"},
                        ],
                        # identificadores filhos do statement 3 (tabela T1) --
                        # anexados via post-processing abaixo (mesmo tree).
                    }
                ],
                "stmt_children": {3: [{"usage_id": 4, "usage_context_id": 3, "line": 4, "col": 10, "name": "T1", "type": "TABLE", "usage": "REFERENCE", "signature": None}]},
            },
            "GESTAO.TRG1": {
                "trees": [
                    {
                        "object_type": "TRIGGER",
                        "identifiers": [
                            {"usage_id": 1, "usage_context_id": 0, "line": 1, "col": 1, "name": "TRG1", "type": "TRIGGER", "usage": "DECLARATION", "signature": "SIG_TRG1"},
                            {"usage_id": 2, "usage_context_id": 1, "line": 1, "col": 1, "name": "TRG1", "type": "TRIGGER", "usage": "DEFINITION", "signature": "SIG_TRG1"},
                        ],
                        "statements": [
                            {"usage_id": 3, "usage_context_id": 2, "line": 2, "stmt_type": "INSERT"},
                        ],
                    }
                ],
                "stmt_children": {3: [{"usage_id": 4, "usage_context_id": 3, "line": 2, "col": 10, "name": "T2", "type": "TABLE", "usage": "REFERENCE", "signature": None}]},
            },
            "GESTAO.TRG2": {
                "trees": [
                    {
                        "object_type": "TRIGGER",
                        "identifiers": [
                            {"usage_id": 1, "usage_context_id": 0, "line": 1, "col": 1, "name": "TRG2", "type": "TRIGGER", "usage": "DECLARATION", "signature": "SIG_TRG2"},
                            {"usage_id": 2, "usage_context_id": 1, "line": 1, "col": 1, "name": "TRG2", "type": "TRIGGER", "usage": "DEFINITION", "signature": "SIG_TRG2"},
                        ],
                        "statements": [
                            {"usage_id": 3, "usage_context_id": 2, "line": 2, "stmt_type": "INSERT"},
                        ],
                    }
                ],
                # TRG2 escreve de volta em T1 -- fecha o ciclo.
                "stmt_children": {3: [{"usage_id": 4, "usage_context_id": 3, "line": 2, "col": 10, "name": "T1", "type": "TABLE", "usage": "REFERENCE", "signature": None}]},
            },
        }
        self.id_calls: Dict[str, int] = {}

    def _ref(self, owner: str, object_name: str) -> str:
        return "{}.{}".format(owner.upper(), object_name.upper())

    def plscope_identifiers(self, owner: str, object_name: str) -> List[Any]:
        ref = self._ref(owner, object_name)
        self.id_calls[ref] = self.id_calls.get(ref, 0) + 1
        obj = self.objects.get(ref)
        if obj is None:
            return []
        rows: List[Any] = []
        for tree in obj["trees"]:
            for row in tree["identifiers"]:
                rows.append(SimpleNamespace(object_type=tree["object_type"], **row))
        for children in obj.get("stmt_children", {}).values():
            for row in children:
                rows.append(SimpleNamespace(object_type=obj["trees"][0]["object_type"], **row))
        return rows

    def plscope_statements(self, owner: str, object_name: str) -> List[Any]:
        ref = self._ref(owner, object_name)
        obj = self.objects.get(ref)
        if obj is None:
            return []
        rows: List[Any] = []
        for tree in obj["trees"]:
            for row in tree["statements"]:
                rows.append(SimpleNamespace(object_type=tree["object_type"], **row))
        return rows

    def resolve_owner(self, signature: str) -> Optional[Tuple[str, str]]:
        return None

    def triggers(self, owner: str, table_names: List[str]) -> List[_TriggerRow]:
        mapping = {"T1": "TRG1", "T2": "TRG2"}
        out = []
        for name in table_names:
            trg = mapping.get(name.upper())
            if trg:
                out.append(_TriggerRow(owner, name.upper(), trg, "INSERT"))
        return out


def test_trigger_cycle_between_two_tables_terminates_and_is_declared():
    # PROVA do contrato: T1 -> TRG1 escreve T2 -> TRG2 escreve T1. A
    # travessia via build_proc_graph (motor real, T-03 + seam de T-05)
    # tem que TERMINAR sob timeout curto e o ciclo tem que aparecer
    # declarado no grafo -- via o visited-set do motor (nunca cap/
    # timeout/contador, regra dura do contrato).
    extractor = _TriggerCycleExtractor()

    result = _run_with_timeout(
        lambda: build_proc_graph(extractor, ("GESTAO", "P_START", "START")), timeout=5.0
    )

    edge_pairs = {(e.from_ref, e.to_ref, e.edge_type) for e in result.edges}
    assert ("GESTAO.T1", "GESTAO.TRG1.TRG1", "TRIGGER_FIRES") in edge_pairs
    assert ("GESTAO.T2", "GESTAO.TRG2.TRG2", "TRIGGER_FIRES") in edge_pairs

    refs = {(n.owner, n.object_name, n.subprogram) for n in result.nodes}
    assert ("GESTAO", "TRG1", "TRG1") in refs
    assert ("GESTAO", "TRG2", "TRG2") in refs

    write_pairs = {(e.from_ref, e.to_ref) for e in result.edges if e.edge_type == "WRITE"}
    assert ("GESTAO.P_START.START", "GESTAO.T1") in write_pairs
    assert ("GESTAO.TRG1.TRG1", "GESTAO.T2") in write_pairs
    assert ("GESTAO.TRG2.TRG2", "GESTAO.T1") in write_pairs

    # Cada trigger e visitado UMA vez so (visited-set) mesmo com o ciclo.
    assert extractor.id_calls["GESTAO.TRG1"] == 1
    assert extractor.id_calls["GESTAO.TRG2"] == 1


# ------------------------------------------------------- determinismo


def test_expand_access_is_deterministic():
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)

    edges1 = expand_access(
        owner="GESTAO", object_name="FLOW_DEMO", subprogram="LOG_MSG", statement=insert_assignment, object_cache=_flow_demo_cache()
    )
    edges2 = expand_access(
        owner="GESTAO", object_name="FLOW_DEMO", subprogram="LOG_MSG", statement=insert_assignment, object_cache=_flow_demo_cache()
    )
    assert edges1 == edges2
