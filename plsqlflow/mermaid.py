"""plsqlflow.mermaid -- renderizacao determinística de flowchart TD (T-04).

Recebe as listas FINAIS de nodes/edges (ja com SQL dinamico integrado por
report.py) na ordem ja estavel que report.py produziu (nodes por id, edges
por (line, from_id, to_id, kind)) e devolve uma string mermaid pronta. Nao
reordena nada aqui -- toda a responsabilidade de ordenacao deterministica e
de report.py; este modulo so traduz node/edge -> sintaxe mermaid.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from .graph import Edge, Node, RootTarget, make_node_id

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")

_DASHED_KINDS = {"dynamic", "override", "trigger", "cascade"}


def _sanitize_id(node_id: str) -> str:
    sanitized = _SANITIZE_RE.sub("_", node_id)
    if not sanitized or sanitized[0].isdigit():
        sanitized = "n_" + sanitized
    return sanitized


def _escape_label(text: Optional[str]) -> str:
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
        .replace('"', "&quot;")
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


def _node_label(node: Node, root_id: str) -> str:
    if node.kind in ("proc", "func"):
        label = node.id if not node.owner else "{}.{}".format(node.owner, node.object_name)
        if node.owner and node.subprogram:
            label += ".{}".format(node.subprogram)
        if node.overload:
            label += "#{}".format(node.overload)
        if node.id == root_id:
            label += " (root)"
        if node.truncated:
            label += " (+N niveis?)"
        if node.note and node.id != root_id:
            label += " [{}]".format(node.note)
        return label
    if node.kind in ("table", "view", "trigger"):
        return node.id if not node.owner else "{}.{}".format(node.owner, node.object_name)
    if node.kind == "dynsql":
        return "SQL dinamico L{}: {}".format(node.line, node.note or "")
    if node.kind == "remote":
        return node.note or node.id
    if node.kind == "wrapped":
        return "{} (wrapped)".format(node.id)
    return node.id


def _node_shape(mid: str, kind: str, label: str) -> str:
    esc = _escape_label(label)
    if kind in ("table", "view"):
        return '{}[("{}")]'.format(mid, esc)
    if kind == "remote":
        return '{}{{{{"{}"}}}}'.format(mid, esc)
    if kind == "trigger":
        return '{}[/"{}"/]'.format(mid, esc)
    if kind == "dynsql":
        return '{}{{"{}"}}'.format(mid, esc)
    return '{}["{}"]'.format(mid, esc)


def _edge_arrow(kind: str, label: Optional[str]) -> str:
    esc = _escape_label(label) if label else ""
    if kind in _DASHED_KINDS:
        return '-. "{}" .->'.format(esc) if esc else "-.->"
    return '-->|"{}"|'.format(esc) if esc else "-->"


def render(nodes: Sequence[Node], edges: Sequence[Edge], root: RootTarget) -> str:
    root_id = make_node_id(root.owner, root.object_name, root.subprogram, root.overload)
    id_map: Dict[str, str] = {n.id: _sanitize_id(n.id) for n in nodes}

    lines: List[str] = ["flowchart TD"]

    dynsql_mermaid_ids: List[str] = []
    for node in nodes:
        mid = id_map[node.id]
        label = _node_label(node, root_id)
        lines.append("    " + _node_shape(mid, node.kind, label))
        if node.kind == "dynsql":
            dynsql_mermaid_ids.append(mid)

    lines.append("")
    lines.append("    classDef dynsql stroke-dasharray: 5 5;")
    for mid in dynsql_mermaid_ids:
        lines.append("    class {} dynsql;".format(mid))

    lines.append("")
    recursion_indices: List[int] = []
    for idx, edge in enumerate(edges):
        arrow = _edge_arrow(edge.kind, edge.label)
        lines.append("    {} {} {}".format(id_map[edge.from_id], arrow, id_map[edge.to_id]))
        if edge.kind == "recursion":
            recursion_indices.append(idx)

    if recursion_indices:
        lines.append("")
        for idx in recursion_indices:
            lines.append("    linkStyle {} stroke:#c00,stroke-width:2px;".format(idx))

    lines.append("")
    lines.append("    %% Legenda:")
    lines.append("    %% retangulo = proc/func | cilindro = tabela/view | hexagono = remoto/db link")
    lines.append("    %% trapezio = trigger | losango tracejado (classe dynsql) = SQL dinamico")
    lines.append("    %% aresta solida = estatica | tracejada = dinamica/override/trigger/cascade | vermelha = recursao")

    return "\n".join(lines) + "\n"
