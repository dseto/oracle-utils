"""Construcao deterministica do grafo de execucao (T-02).

Entrada: um `Extractor` (protocolo com os metodos usados por build_graph) que
devolve as linhas ja tipadas de extract.py. No CLI real (T-04) o Extractor e
implementado chamando extract.py com uma conexao de verdade; nos testes e um
fake que le de fixtures JSON/dataclasses sinteticas -- build_graph em si nunca
toca rede/banco.

Limitacao conhecida e deliberada: plscope_calls.sql/plscope_statements.sql
(fixados pela T-01, fora do escopo desta tarefa) sao resolvidos por
(owner, object_name) -- o nivel de granularidade do PACOTE/objeto compilado,
nao por subprograma. ALL_IDENTIFIERS nao expoe qual subprograma "envolve"
cada CALL sem um parser de source (isso e trabalho de lexical.py/T-03, nao
deste modulo). Por isso, ao expandir qualquer subprograma de um objeto,
reconsideramos a lista inteira de chamadas do objeto como candidatas de saida
desse no -- uma sobre-aproximacao conservadora (nunca perde uma chamada real,
pode listar alguma chamada num no que nao e o "verdadeiro" chamador). O
detector de recursao (aresta para um no ja no caminho atual) e o visited set
continuam corretos sob essa aproximacao, e cobre tanto recursao direta quanto
mutua -- ver teste dedicado com PROC_A/PROC_B do fixture FLOW_DEMO.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Set, Tuple

from .extract import (
    FkCascadeRow,
    PlscopeCallRow,
    PlscopeStatementRow,
    TriggerRow,
    TypeHierarchyRow,
)

SYS_OWNERS = {"SYS", "SYSTEM"}
TYPE_OBJECT_KINDS = {"TYPE", "TYPE BODY"}
DML_STATEMENT_TYPES = {"INSERT", "UPDATE", "DELETE"}

_INSERT_RE = re.compile(r"INSERT\s+INTO\s+([A-Za-z0-9_$#\"\.]+)", re.IGNORECASE)
_UPDATE_RE = re.compile(r"UPDATE\s+([A-Za-z0-9_$#\"\.]+)", re.IGNORECASE)
_DELETE_RE = re.compile(r"DELETE\s+FROM\s+([A-Za-z0-9_$#\"\.]+)", re.IGNORECASE)
_TABLE_NAME_PATTERNS = (_INSERT_RE, _UPDATE_RE, _DELETE_RE)


class Extractor(Protocol):
    def calls(self, owner: str, object_name: str) -> List[PlscopeCallRow]: ...

    def statements(self, owner: str, object_name: str) -> List[PlscopeStatementRow]: ...

    def triggers(self, owner: str, table_names: List[str]) -> List[TriggerRow]: ...

    def fk_cascade(self, owner: str, table_name: str) -> List[FkCascadeRow]: ...

    def type_hierarchy(self, owner: str, type_name: str) -> List[TypeHierarchyRow]: ...


@dataclass(frozen=True)
class RootTarget:
    owner: str
    object_name: str
    subprogram: Optional[str] = None
    overload: Optional[str] = None


@dataclass
class Node:
    id: str
    kind: str  # proc|func|table|view|trigger|dynsql|remote|wrapped
    owner: Optional[str] = None
    object_name: Optional[str] = None
    subprogram: Optional[str] = None
    overload: Optional[str] = None
    line: Optional[int] = None
    confidence: str = "compiler"  # compiler|lexical|heuristic
    truncated: bool = False
    note: Optional[str] = None


@dataclass
class Edge:
    from_id: str
    to_id: str
    kind: str  # static|dynamic|trigger|cascade|override|recursion
    label: Optional[str] = None
    line: Optional[int] = None


@dataclass
class CollapsedGroup:
    parent_id: str
    kind: str
    count: int
    member_ids: List[str]
    note: str


@dataclass
class GraphResult:
    root: str
    nodes: List[Node]
    edges: List[Edge]
    collapsed: List[CollapsedGroup]
    stats: Dict[str, object]


def make_node_id(
    owner: str, object_name: str, subprogram: Optional[str], overload: Optional[str] = None
) -> str:
    node_id = "{}.{}".format(owner, object_name)
    if subprogram:
        node_id += ".{}".format(subprogram)
    if overload:
        node_id += "#{}".format(overload)
    return node_id


def make_table_id(owner: str, table_name: str) -> str:
    return "{}.{}".format(owner, table_name)


def make_trigger_id(owner: str, trigger_name: str) -> str:
    return "{}.{}".format(owner, trigger_name)


def _kind_for(decl_type: Optional[str]) -> str:
    if decl_type and decl_type.upper() == "FUNCTION":
        return "func"
    return "proc"


def extract_table_name(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    for pattern in _TABLE_NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip('"').upper().split(".")[-1]
    return None


def _event_matches(triggering_event: Optional[str], stmt_type: str) -> bool:
    if not triggering_event:
        return False
    return stmt_type.upper() in triggering_event.upper()


class _GraphBuilder:
    def __init__(self, extractor: Extractor, max_depth: int, node_budget: int):
        self.extractor = extractor
        self.max_depth = max_depth
        self.node_budget = node_budget

        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self._edge_keys: Set[Tuple[str, str, str]] = set()
        self.collapsed: List[CollapsedGroup] = []
        self._collapse_buckets: Dict[Tuple[str, str], CollapsedGroup] = {}

        self._visited_expanded: Set[str] = set()
        self._calls_cache: Dict[Tuple[str, str], List[PlscopeCallRow]] = {}
        self._overload_index_cache: Dict[Tuple[str, str], Dict[Tuple[str, str, str], List[int]]] = {}
        self._statements_processed: Set[Tuple[str, str]] = set()

        self.stats: Dict[str, object] = {"depth_reached": 0, "truncated_paths": 0}

    # ------------------------------------------------------------- helpers

    def _get_or_create_node(self, node_id: str, factory) -> Optional[Node]:
        existing = self.nodes.get(node_id)
        if existing is not None:
            return existing
        if len(self.nodes) >= self.node_budget:
            return None
        node = factory()
        self.nodes[node_id] = node
        return node

    def _add_edge(
        self, from_id: str, to_id: str, kind: str, label: Optional[str] = None, line: Optional[int] = None
    ) -> None:
        key = (from_id, to_id, kind)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        self.edges.append(Edge(from_id=from_id, to_id=to_id, kind=kind, label=label, line=line))

    def _collapse(self, caller_id: str, kind: str, member_id: str, note_label: str) -> None:
        bucket_key = (caller_id, kind)
        agg_id = "{}::collapsed:{}".format(caller_id, kind)
        if bucket_key not in self._collapse_buckets:
            group = CollapsedGroup(
                parent_id=caller_id, kind=kind, count=0, member_ids=[], note="colapsado"
            )
            self._collapse_buckets[bucket_key] = group
            self.collapsed.append(group)
            # No agregado deliberadamente ignora o orcamento: e um resumo, nao
            # um no "descoberto" de verdade -- sem ele o colapso ficaria mudo.
            self.nodes[agg_id] = Node(
                id=agg_id, kind=kind, confidence="heuristic", note="colapsado (orcamento excedido)"
            )
            self._add_edge(caller_id, agg_id, "static", label="colapsado")
        group = self._collapse_buckets[bucket_key]
        group.count += 1
        group.member_ids.append(member_id)
        group.note = "+{} {} colapsados (orcamento de {} nos excedido)".format(
            group.count, note_label, self.node_budget
        )

    def _get_calls(self, owner: str, object_name: str) -> List[PlscopeCallRow]:
        key = (owner, object_name)
        if key not in self._calls_cache:
            calls = list(self.extractor.calls(owner, object_name))
            calls.sort(key=lambda c: (c.line, c.col))
            self._calls_cache[key] = calls
        return self._calls_cache[key]

    def _overload_index(
        self, owner: str, object_name: str, calls: List[PlscopeCallRow]
    ) -> Dict[Tuple[str, str, str], List[int]]:
        key = (owner, object_name)
        if key in self._overload_index_cache:
            return self._overload_index_cache[key]
        index: Dict[Tuple[str, str, str], List[int]] = {}
        for call in calls:
            target_key = (call.decl_owner, call.decl_object, call.called_name)
            lines = index.setdefault(target_key, [])
            if call.decl_line not in lines:
                lines.append(call.decl_line)
        for lines in index.values():
            lines.sort()
        self._overload_index_cache[key] = index
        return index

    # --------------------------------------------------------------- calls

    def _expand(
        self,
        node_id: str,
        owner: str,
        object_name: str,
        depth: int,
        path: Tuple[str, ...],
    ) -> None:
        if node_id in self._visited_expanded:
            return
        self._visited_expanded.add(node_id)
        self.stats["depth_reached"] = max(int(self.stats["depth_reached"]), depth)

        if depth >= self.max_depth:
            node = self.nodes.get(node_id)
            if node is not None:
                node.truncated = True
                node.note = "profundidade maxima atingida (+N niveis?)"
            self.stats["truncated_paths"] = int(self.stats["truncated_paths"]) + 1
            return

        new_path = path + (node_id,)
        calls = self._get_calls(owner, object_name)
        overload_index = self._overload_index(owner, object_name, calls)

        # Processado antes do loop (nao depois) para que o no ancora dos
        # statements do objeto seja o primeiro a visitar o objeto na DFS
        # (tipicamente a raiz) -- deterministico e mais legivel do que o no
        # mais profundo, que seria o primeiro a "terminar" se isso rodasse
        # so no fim da funcao.
        self._process_statements(node_id, owner, object_name)

        for call in calls:
            if call.decl_owner and call.decl_owner.upper() in SYS_OWNERS:
                self._add_sys_leaf(node_id, call)
                continue

            if call.decl_object_type and call.decl_object_type.upper() in TYPE_OBJECT_KINDS:
                self._handle_override(node_id, call)
                continue

            target_key = (call.decl_owner, call.decl_object, call.called_name)
            overload_lines = overload_index.get(target_key, [])
            overload_label = None
            if len(overload_lines) > 1:
                overload_label = str(overload_lines.index(call.decl_line) + 1)

            target_id = make_node_id(call.decl_owner, call.decl_object, call.called_name, overload_label)

            if target_id in new_path:
                self._add_edge(
                    node_id, target_id, "recursion", label="recursao: {}".format(call.called_name), line=call.line
                )
                continue

            target_node = self._get_or_create_node(
                target_id,
                lambda c=call, oid=overload_label: Node(
                    id=make_node_id(c.decl_owner, c.decl_object, c.called_name, oid),
                    kind=_kind_for(c.decl_type),
                    owner=c.decl_owner,
                    object_name=c.decl_object,
                    subprogram=c.called_name,
                    overload=oid,
                    line=c.decl_line,
                    confidence="compiler",
                ),
            )
            if target_node is None:
                self._collapse(node_id, "proc", target_id, "subprogramas")
                continue

            self._add_edge(node_id, target_id, "static", label=None, line=call.line)
            self._expand(target_node.id, call.decl_owner, call.decl_object, depth + 1, new_path)

    def _add_sys_leaf(self, caller_id: str, call: PlscopeCallRow) -> None:
        leaf_id = make_node_id(call.decl_owner, call.decl_object, call.called_name, None)
        node = self._get_or_create_node(
            leaf_id,
            lambda c=call: Node(
                id=make_node_id(c.decl_owner, c.decl_object, c.called_name, None),
                kind=_kind_for(c.decl_type),
                owner=c.decl_owner,
                object_name=c.decl_object,
                subprogram=c.called_name,
                line=c.decl_line,
                confidence="compiler",
                note="folha SYS/builtin, nao expandida",
            ),
        )
        if node is None:
            self._collapse(caller_id, "proc", leaf_id, "chamadas SYS")
            return
        self._add_edge(caller_id, leaf_id, "static", label=call.called_name, line=call.line)

    # ---------------------------------------------------------- OO/OVERRIDING

    def _handle_override(self, caller_id: str, call: PlscopeCallRow) -> None:
        hierarchy = self.extractor.type_hierarchy(call.decl_owner, call.decl_object)
        base_id = make_node_id(call.decl_owner, call.decl_object, call.called_name, None)
        base_node = self._get_or_create_node(
            base_id,
            lambda c=call: Node(
                id=make_node_id(c.decl_owner, c.decl_object, c.called_name, None),
                kind="func",
                owner=c.decl_owner,
                object_name=c.decl_object,
                subprogram=c.called_name,
                line=c.decl_line,
                confidence="compiler",
                note="metodo de supertipo",
            ),
        )
        if base_node is None:
            self._collapse(caller_id, "proc", base_id, "metodos de supertipo")
            return
        self._add_edge(caller_id, base_id, "static", label=call.called_name, line=call.line)

        candidates = sorted(
            (
                row
                for row in hierarchy
                if row.overriding and row.method_name and row.method_name.upper() == call.called_name.upper()
            ),
            key=lambda r: r.type_name,
        )
        for candidate in candidates:
            cand_id = make_node_id(call.decl_owner, candidate.type_name, call.called_name, None)
            cand_node = self._get_or_create_node(
                cand_id,
                lambda c=candidate, owner=call.decl_owner, method=call.called_name: Node(
                    id=make_node_id(owner, c.type_name, method, None),
                    kind="func",
                    owner=owner,
                    object_name=c.type_name,
                    subprogram=method,
                    confidence="heuristic",
                    note="candidato OVERRIDING (dispatch e runtime, nao resolvido)",
                ),
            )
            if cand_node is None:
                self._collapse(base_id, "override", cand_id, "candidatos OVERRIDING")
                continue
            self._add_edge(base_id, cand_id, "override", label="override?")

    # --------------------------------------------------- triggers / cascata

    def _process_statements(self, anchor_id: str, owner: str, object_name: str) -> None:
        key = (owner, object_name)
        if key in self._statements_processed:
            return
        self._statements_processed.add(key)

        statements = [s for s in self.extractor.statements(owner, object_name) if s.stmt_type in DML_STATEMENT_TYPES]
        if not statements:
            return

        table_map: Dict[str, List[PlscopeStatementRow]] = {}
        for stmt in statements:
            table_name = extract_table_name(stmt.text)
            if not table_name:
                continue
            table_id = make_table_id(owner, table_name)
            table_node = self._get_or_create_node(
                table_id,
                lambda tn=table_name: Node(
                    id=make_table_id(owner, tn), kind="table", owner=owner, object_name=tn, confidence="lexical"
                ),
            )
            if table_node is None:
                self._collapse(anchor_id, "table", table_id, "tabelas")
                continue
            self._add_edge(anchor_id, table_id, "static", label=stmt.stmt_type, line=stmt.line)
            table_map.setdefault(table_name, []).append(stmt)

        if not table_map:
            return

        trigger_rows = self.extractor.triggers(owner, sorted(table_map))
        for table_name, stmts in table_map.items():
            table_id = make_table_id(owner, table_name)
            for stmt in stmts:
                for trig in trigger_rows:
                    if trig.table_name.upper() != table_name.upper() or trig.status != "ENABLED":
                        continue
                    if not _event_matches(trig.triggering_event, stmt.stmt_type):
                        continue
                    self._add_trigger_edge(table_id, trig, stmt)
                if stmt.stmt_type == "DELETE":
                    self._process_fk_cascade(table_id, owner, table_name)

    def _add_trigger_edge(self, table_id: str, trig: TriggerRow, stmt: PlscopeStatementRow) -> None:
        trig_id = make_trigger_id(trig.table_owner, trig.trigger_name)
        trig_node = self._get_or_create_node(
            trig_id,
            lambda t=trig: Node(
                id=make_trigger_id(t.table_owner, t.trigger_name),
                kind="trigger",
                owner=t.table_owner,
                object_name=t.trigger_name,
                confidence="compiler",
            ),
        )
        if trig_node is None:
            self._collapse(table_id, "trigger", trig_id, "triggers")
            return
        self._add_edge(
            table_id,
            trig_id,
            "trigger",
            label="{} {}".format(trig.trigger_type, trig.triggering_event),
            line=stmt.line,
        )

    def _process_fk_cascade(self, table_id: str, owner: str, table_name: str) -> None:
        for fk in self.extractor.fk_cascade(owner, table_name):
            child_id = make_table_id(fk.child_owner, fk.child_table)
            child_node = self._get_or_create_node(
                child_id,
                lambda f=fk: Node(
                    id=make_table_id(f.child_owner, f.child_table),
                    kind="table",
                    owner=f.child_owner,
                    object_name=f.child_table,
                    confidence="compiler",
                ),
            )
            if child_node is None:
                self._collapse(table_id, "table", child_id, "tabelas em cascata")
                continue
            self._add_edge(
                table_id, child_id, "cascade", label="{} ({})".format(fk.delete_rule, fk.constraint_name)
            )

    # ---------------------------------------------------------------- build

    def build(self, root: RootTarget) -> GraphResult:
        root_id = make_node_id(root.owner, root.object_name, root.subprogram, root.overload)
        self.nodes[root_id] = Node(
            id=root_id,
            kind="proc",
            owner=root.owner,
            object_name=root.object_name,
            subprogram=root.subprogram,
            overload=root.overload,
            confidence="compiler",
        )
        self._expand(root_id, root.owner, root.object_name, 0, ())

        stats = dict(self.stats)
        stats["nodes"] = len(self.nodes)
        stats["edges"] = len(self.edges)
        stats["collapsed_groups"] = len(self.collapsed)

        nodes = sorted(self.nodes.values(), key=lambda n: n.id)
        edges = sorted(
            self.edges,
            key=lambda e: (e.line if e.line is not None else float("inf"), e.from_id, e.to_id, e.kind),
        )
        collapsed = sorted(self.collapsed, key=lambda g: (g.parent_id, g.kind))
        return GraphResult(root=root_id, nodes=nodes, edges=edges, collapsed=collapsed, stats=stats)


def build_graph(
    extractor: Extractor, root: RootTarget, max_depth: int = 10, node_budget: int = 120
) -> GraphResult:
    builder = _GraphBuilder(extractor, max_depth=max_depth, node_budget=node_budget)
    return builder.build(root)
