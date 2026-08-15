"""plsqlflow.report -- monta o relatorio final (grafo + SQL dinamico + stats + mermaid), T-04.

build_graph (T-02) deliberadamente NAO processa EXECUTE IMMEDIATE/OPEN FOR/
DBMS_SQL.PARSE (ver limitacao documentada em graph.py). Este modulo fecha essa
lacuna: para cada objeto ja presente no grafo, busca de novo os statements do
objeto, filtra os que o graph.py ignorou, resolve cada um via
dynsql.resolve_dynamic_sql usando o codigo-fonte do objeto (extractor.source,
metodo que graph.Extractor nao exige mas que qualquer implementacao real ou de
teste deste modulo precisa fornecer -- Protocol estrutural, nao exige tocar
graph.py) e injeta nodes/edges "dynsql" no grafo final.

Simplificacao aceitavel e documentada: PL/Scope nao expoe de qual subprograma
um STATEMENT pertence (mesma limitacao de graph.py, por objeto compilado, nao
por subprograma) -- entao, para ancorar as arestas do SQL dinamico de um
objeto, escolhemos deterministicamente: o no raiz, se ele pertencer a esse
(owner, object_name); senao o no de subprograma com o id lexicograficamente
menor entre os que pertencem a esse objeto. Isso reproduz o comportamento real
de graph.py._process_statements quando o objeto contem o proprio alvo (o caso
comum), e degrada de forma estavel e testavel nos demais casos.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, Tuple

from . import mermaid
from .dynsql import resolve_dynamic_sql
from .extract import (
    FetchSourceRow,
    FkCascadeRow,
    PlscopeCallRow,
    PlscopeStatementRow,
    TriggerRow,
    TypeHierarchyRow,
)
from .graph import (
    Edge,
    Node,
    RootTarget,
    build_graph,
    extract_table_name,
    make_node_id,
    make_table_id,
)

DYNSQL_STMT_TYPES = {"EXECUTE IMMEDIATE", "OPEN FOR", "DBMS_SQL.PARSE"}


class Extractor(Protocol):
    def calls(self, owner: str, object_name: str) -> List[PlscopeCallRow]: ...

    def statements(self, owner: str, object_name: str) -> List[PlscopeStatementRow]: ...

    def triggers(self, owner: str, table_names: List[str]) -> List[TriggerRow]: ...

    def fk_cascade(self, owner: str, table_name: str) -> List[FkCascadeRow]: ...

    def type_hierarchy(self, owner: str, type_name: str) -> List[TypeHierarchyRow]: ...

    def source(self, owner: str, object_name: str) -> List[FetchSourceRow]: ...


def _dynsql_node_id(owner: str, object_name: str, line: int) -> str:
    return "{}.{}::dynsql:{}".format(owner, object_name, line)


def _pick_anchor(
    nodes_by_id: Dict[str, Node], owner: str, object_name: str, root_id: str
) -> Optional[str]:
    root_node = nodes_by_id.get(root_id)
    if root_node is not None and root_node.owner == owner and root_node.object_name == object_name:
        return root_id
    candidates = sorted(
        n.id for n in nodes_by_id.values() if n.owner == owner and n.object_name == object_name
    )
    return candidates[0] if candidates else None


def _integrate_dynamic_sql(
    extractor: Extractor,
    nodes: Dict[str, Node],
    edges: List[Edge],
    root_id: str,
) -> List[Dict[str, object]]:
    blind_spots: List[Dict[str, object]] = []
    visited_objects = sorted(
        {(n.owner, n.object_name) for n in nodes.values() if n.owner and n.object_name}
    )
    for owner, object_name in visited_objects:
        statements = extractor.statements(owner, object_name)
        dyn_statements = sorted(
            (s for s in statements if s.stmt_type in DYNSQL_STMT_TYPES), key=lambda s: s.line
        )
        if not dyn_statements:
            continue
        anchor_id = _pick_anchor(nodes, owner, object_name, root_id)
        if anchor_id is None:
            continue
        source_rows = list(extractor.source(owner, object_name))
        for stmt in dyn_statements:
            result = resolve_dynamic_sql(source_rows, line=stmt.line)
            dyn_id = _dynsql_node_id(owner, object_name, stmt.line)
            if result.resolved:
                nodes[dyn_id] = Node(
                    id=dyn_id,
                    kind="dynsql",
                    owner=owner,
                    object_name=object_name,
                    line=stmt.line,
                    confidence="lexical",
                    note=result.sql_text,
                )
                edges.append(
                    Edge(from_id=anchor_id, to_id=dyn_id, kind="dynamic", label="resolvido", line=stmt.line)
                )
                table_name = extract_table_name(result.sql_text)
                if table_name:
                    table_id = make_table_id(owner, table_name)
                    if table_id not in nodes:
                        nodes[table_id] = Node(
                            id=table_id,
                            kind="table",
                            owner=owner,
                            object_name=table_name,
                            confidence="lexical",
                        )
                    edges.append(
                        Edge(
                            from_id=dyn_id,
                            to_id=table_id,
                            kind="dynamic",
                            label="resolvido -> tabela",
                            line=stmt.line,
                        )
                    )
            else:
                nodes[dyn_id] = Node(
                    id=dyn_id,
                    kind="dynsql",
                    owner=owner,
                    object_name=object_name,
                    line=stmt.line,
                    confidence="heuristic",
                    truncated=False,
                    note=result.fragment,
                )
                edges.append(
                    Edge(
                        from_id=anchor_id, to_id=dyn_id, kind="dynamic", label="irresolvivel", line=stmt.line
                    )
                )
                blind_spots.append(
                    {
                        "type": "dynsql_unresolved",
                        "at": "{}.{}:{}".format(owner, object_name, stmt.line),
                        "fragment": result.fragment,
                    }
                )
    return blind_spots


def _override_blind_spots(edges: List[Edge]) -> List[Dict[str, object]]:
    groups: Dict[str, List[str]] = {}
    for edge in edges:
        if edge.kind == "override":
            groups.setdefault(edge.from_id, []).append(edge.to_id)
    result: List[Dict[str, object]] = []
    for from_id in sorted(groups):
        result.append(
            {"type": "override_open", "at": from_id, "candidates": sorted(set(groups[from_id]))}
        )
    return result


def _pct_plscope(nodes: Dict[str, Node]) -> float:
    """% de nos "contaveis" (owner+object_name setado, confianca reconhecida)
    resolvidos pelo compilador (PL/Scope). Formula: 100 * count(confidence ==
    "compiler") / count(confidence in {"compiler","lexical","heuristic"}),
    entre os nos com owner e object_name preenchidos (exclui agregados de
    colapso de orcamento, que nao representam um objeto real). Arredondado a
    1 casa decimal. 0.0 se nao houver nenhum no contavel."""
    counted = [
        n
        for n in nodes.values()
        if n.owner and n.object_name and n.confidence in ("compiler", "lexical", "heuristic")
    ]
    if not counted:
        return 0.0
    compiler_count = sum(1 for n in counted if n.confidence == "compiler")
    return round(100.0 * compiler_count / len(counted), 1)


def _node_to_dict(node: Node) -> Dict[str, object]:
    return {
        "id": node.id,
        "kind": node.kind,
        "owner": node.owner,
        "object_name": node.object_name,
        "subprogram": node.subprogram,
        "overload": node.overload,
        "line": node.line,
        "confidence": node.confidence,
        "truncated": node.truncated,
        "note": node.note,
    }


def _edge_to_dict(edge: Edge) -> Dict[str, object]:
    return {
        "from": edge.from_id,
        "to": edge.to_id,
        "kind": edge.kind,
        "label": edge.label,
        "line": edge.line,
    }


def _sort_key_edge(edge: Edge) -> Tuple[float, str, str, str]:
    return (edge.line if edge.line is not None else float("inf"), edge.from_id, edge.to_id, edge.kind)


def build_report(
    extractor: Extractor, root: RootTarget, max_depth: int = 10, node_budget: int = 120
) -> Dict[str, object]:
    graph_result = build_graph(extractor, root, max_depth=max_depth, node_budget=node_budget)
    root_id = make_node_id(root.owner, root.object_name, root.subprogram, root.overload)

    nodes: Dict[str, Node] = {n.id: n for n in graph_result.nodes}
    edges: List[Edge] = list(graph_result.edges)

    blind_spots = _integrate_dynamic_sql(extractor, nodes, edges, root_id)
    blind_spots.extend(_override_blind_spots(edges))

    ordered_nodes = sorted(nodes.values(), key=lambda n: n.id)
    ordered_edges = sorted(edges, key=_sort_key_edge)

    # nodes/edges aqui sao os contados por build_graph (T-02), antes da
    # integracao de SQL dinamico -- report.py acrescenta so pct_plscope,
    # conforme o contrato desta tarefa.
    stats = dict(graph_result.stats)
    stats["pct_plscope"] = _pct_plscope(nodes)

    mermaid_text = mermaid.render(ordered_nodes, ordered_edges, root)

    return {
        "root": {
            "owner": root.owner,
            "object": root.object_name,
            "sub": root.subprogram,
            "overload": root.overload,
        },
        "nodes": [_node_to_dict(n) for n in ordered_nodes],
        "edges": [_edge_to_dict(e) for e in ordered_edges],
        "stats": stats,
        "blind_spots": blind_spots,
        "mermaid": mermaid_text,
    }
