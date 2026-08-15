"""plsqlflow.depgraph_render -- Fase 5 do plano (T-04): materializa um
`DepGraphResult` (T-02 BFS + T-03 READ/WRITE/DYNAMIC_SQL mesclados + T-04
triggers) em disco, na arvore de `docs/plano-oracle-dependency-graph.md`
secao 5:

    <out_dir>/
    |-- INDEX.md
    |-- edges.jsonl
    |-- nodes/<OWNER>.<OBJETO>.md
    |-- recompile.sql      # so se result.needs_recompile nao estiver vazio
    `-- meta.json

REGRAS DE BYTE-EXATIDAO (criterio de aceite literal do T-04 -- "regerar o
grafo contra o mesmo banco produz arquivos identicos byte a byte"):
- toda escrita usa `encoding="utf-8"` e `newline="\\n"` explicitos -- em
  Windows, o modo texto padrao traduziria "\\n" para "\\r\\n" e quebraria a
  comparacao byte a byte do golden test;
- `edges.jsonl`: uma aresta por linha, `json.dumps(..., sort_keys=True,
  ensure_ascii=False)`, ordenada por `(from_ref, to_ref, edge_type, line
  ou -1)`;
- `meta.json`: SEM timestamp de relogio -- so `chain_hash` (SHA-256 sobre
  a lista ordenada de `(owner, name, type, last_ddl_time)` de todos os
  nos), versao do extrator (`plsqlflow.__version__`) e `meta_params`
  (parametros usados, ex.: flags de CLI -- dado do CHAMADOR, nao lido daqui);
- `recompile.sql`: ASCII puro, so gerado quando ha objeto sem PL/Scope,
  nunca executado por este modulo (so grava o script para o DBA revisar).

Decisao registrada (fora do texto literal da tarefa): antes de escrever
`nodes/`, os `.md` antigos do diretorio sao removidos -- sem isso, um
objeto que saiu do grafo entre duas geracoes deixaria um arquivo orfao
para tras, o que violaria o espirito de "regerar produz o MESMO resultado"
(o diretorio inteiro, nao so os arquivos novos, tem que refletir o
`DepGraphResult` atual).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import __version__ as _PLSQLFLOW_VERSION
from .depgraph import DepEdge, DepGraphResult, DepNode
from .extract import TabColumnRow

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")

_CALL_EDGE_TYPES = ("CALL", "SYNONYM_RESOLVES_TO")
_TABLE_EDGE_TYPES = ("READ", "WRITE")

_PLSCOPE_CLAUSE = "PLSCOPE_SETTINGS='IDENTIFIERS:ALL, STATEMENTS:ALL'"


# --------------------------------------------------------------------------
# nome de arquivo / referencia
# --------------------------------------------------------------------------


def sanitize_component(component: str) -> str:
    """Sanitiza um componente OWNER/OBJETO para nome de arquivo, seguindo a
    convencao de `.claude/skills/dep-graph/SKILL.md` (sem `$`, `#`, `.`) --
    generalizada aqui para trocar QUALQUER caractere fora de
    `[A-Za-z0-9_]` por `_`. Toda ref do grafo ja chega normalizada em
    maiusculas (`owner.upper()`/`object_name.upper()` em `depgraph.py`),
    entao nao ha colisao de caixa entre nos distintos no NTFS
    (case-insensitive) -- so colisao teorica se dois nomes distintos
    colapsarem no mesmo `_` apos sanitizacao (ex.: `A$B` e `A#B`), caso
    extremo documentado e fora do escopo do MVP."""
    return _SANITIZE_RE.sub("_", component.upper())


def node_filename(owner: str, object_name: str) -> str:
    return "{}.{}.md".format(sanitize_component(owner), sanitize_component(object_name))


def _ref(node: DepNode) -> str:
    return "{}.{}".format(node.owner, node.object_name)


def _edge_sort_key(e: DepEdge) -> Tuple[str, str, str, int]:
    return (e.from_ref, e.to_ref, e.edge_type, e.line if e.line is not None else -1)


def _finalize(lines: List[str]) -> str:
    """Remove linhas em branco penduradas no final e fecha o arquivo com
    exatamente um `\\n` -- essencial pra byte-exatidao (duas geracoes
    tem que produzir a mesma sequencia de bytes, sem variar espacamento
    residual)."""
    trimmed = list(lines)
    while trimmed and trimmed[-1] == "":
        trimmed.pop()
    return "\n".join(trimmed) + "\n"


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# --------------------------------------------------------------------------
# node .md (plano, secao 5 -- ordem de secao FIXA)
# --------------------------------------------------------------------------


def _format_metadata_line(node: DepNode) -> str:
    parts = [
        "tipo: {}".format(node.object_type),
        "status: {}".format(node.status),
        "plscope: {}".format("sim" if node.plscope else "não"),
    ]
    # trigger_status (T-04): extensao alem dos 4 campos literais do
    # template -- so aparece em nos TRIGGER (plano, secao 4.4: status
    # ENABLED/DISABLED "registrado no no").
    if node.trigger_status is not None:
        parts.append("trigger_status: {}".format(node.trigger_status))
    if node.source_first_line is not None and node.source_last_line is not None:
        parts.append("source: linhas {}-{}".format(node.source_first_line, node.source_last_line))
    return "- " + " | ".join(parts)


def _render_call_out_lines(ref: str, outbound: Sequence[DepEdge]) -> List[str]:
    entries = sorted(
        (e for e in outbound if e.edge_type in _CALL_EDGE_TYPES),
        key=lambda e: (e.to_ref, e.edge_type),
    )
    return ["- {} ({})".format(e.to_ref, e.edge_type) for e in entries]


def _render_call_in_lines(ref: str, inbound: Sequence[DepEdge]) -> List[str]:
    entries = sorted(
        (e for e in inbound if e.edge_type in _CALL_EDGE_TYPES),
        key=lambda e: (e.from_ref, e.edge_type),
    )
    return ["- {} ({})".format(e.from_ref, e.edge_type) for e in entries]


def _table_access_entry(e: DepEdge, other_ref: str, outbound: bool) -> Tuple[Tuple[int, str, int, str], str]:
    line_no = e.line if e.line is not None else -1
    sort_key = (0 if outbound else 1, other_ref, line_no, e.edge_type)
    line_label = "L{}".format(e.line) if e.line is not None else "L?"
    arrow = "->" if outbound else "<-"
    tag = "R" if e.edge_type == "READ" else "W:{}".format(e.op or "?")
    text = "- {} {} {} {}".format(tag, line_label, arrow, other_ref)
    if e.cols:
        text += " (cols: {})".format(", ".join(e.cols))
    return sort_key, text


def _render_table_access_lines(outbound: Sequence[DepEdge], inbound: Sequence[DepEdge]) -> List[str]:
    """`## Tabelas acessadas` -- outbound (no PL/SQL le/escreve a tabela)
    E inbound (a tabela e lida/escrita por outro objeto) no MESMO nome de
    secao: satisfaz "arestas aparecem nos dois nos" (plano, secao 5) sem
    exigir uma secao dedicada so pro lado tabela."""
    entries = [
        _table_access_entry(e, e.to_ref, outbound=True)
        for e in outbound
        if e.edge_type in _TABLE_EDGE_TYPES
    ] + [
        _table_access_entry(e, e.from_ref, outbound=False)
        for e in inbound
        if e.edge_type in _TABLE_EDGE_TYPES
    ]
    entries.sort(key=lambda pair: pair[0])
    return [text for _, text in entries]


def _render_data_type(col: TabColumnRow) -> str:
    dt = col.data_type or "UNKNOWN"
    upper = dt.upper()
    if upper.startswith("TIMESTAMP"):
        return dt
    if upper == "NUMBER" and col.data_precision is not None:
        if col.data_scale:
            return "{}({},{})".format(dt, col.data_precision, col.data_scale)
        return "{}({})".format(dt, col.data_precision)
    if upper in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR") and col.data_length is not None:
        return "{}({})".format(dt, col.data_length)
    return dt


def _render_columns_lines(node: DepNode) -> List[str]:
    if node.object_type != "TABLE" or not node.columns:
        return []
    lines = []
    for col in sorted(node.columns, key=lambda c: c.column_id):
        nullability = "NULL" if (col.nullable or "Y").upper() == "Y" else "NOT NULL"
        lines.append("- {} {} {}".format(col.column_name, _render_data_type(col), nullability))
    return lines


def _render_trigger_lines(
    outbound: Sequence[DepEdge], inbound: Sequence[DepEdge], nodes_by_ref: Dict[str, DepNode]
) -> List[str]:
    lines: List[str] = []
    fires_out = sorted((e for e in outbound if e.edge_type == "TRIGGER_FIRES"), key=lambda e: e.to_ref)
    for e in fires_out:
        target = nodes_by_ref.get(e.to_ref)
        status = target.trigger_status if target is not None else None
        text = "- {} evento:{}".format(e.to_ref, e.op or "?")
        if status:
            text += " status:{}".format(status)
        lines.append(text)

    fires_in = sorted((e for e in inbound if e.edge_type == "TRIGGER_FIRES"), key=lambda e: e.from_ref)
    for e in fires_in:
        lines.append("- {} evento:{}".format(e.from_ref, e.op or "?"))

    return lines


def _render_dynsql_lines(outbound: Sequence[DepEdge], snippets: Dict[str, str]) -> List[str]:
    dyn = sorted(
        (e for e in outbound if e.edge_type == "DYNAMIC_SQL"),
        key=lambda e: (e.line if e.line is not None else -1, e.to_ref),
    )
    lines: List[str] = []
    for e in dyn:
        line_label = "L{}".format(e.line) if e.line is not None else "L?"
        lines.append("- {} [{}] -> {}".format(line_label, e.confidence, e.to_ref))
        snippet = snippets.get(e.snippet_ref) if e.snippet_ref else None
        if snippet:
            lines.append("  trecho ({}):".format(e.snippet_ref))
            lines.append("  ```")
            for snippet_line in snippet.splitlines():
                lines.append("  {}".format(snippet_line))
            lines.append("  ```")
    return lines


def render_node_md(
    node: DepNode,
    edges: Sequence[DepEdge],
    nodes_by_ref: Dict[str, DepNode],
    snippets: Dict[str, str],
) -> str:
    """Monta o texto de `nodes/<OWNER>.<OBJETO>.md` -- secoes na ordem
    EXATA do plano (secao 5): Chama / Chamado por / Tabelas acessadas /
    Colunas / Triggers ativados / SQL Dinamico. Secao vazia e omitida
    (cabecalho incluso)."""
    ref = _ref(node)
    outbound = [e for e in edges if e.from_ref == ref]
    inbound = [e for e in edges if e.to_ref == ref]

    sections: List[Tuple[str, List[str]]] = [
        ("## Chama (outbound)", _render_call_out_lines(ref, outbound)),
        ("## Chamado por (inbound)", _render_call_in_lines(ref, inbound)),
        ("## Tabelas acessadas", _render_table_access_lines(outbound, inbound)),
        ("## Colunas", _render_columns_lines(node)),
        ("## Triggers ativados", _render_trigger_lines(outbound, inbound, nodes_by_ref)),
        ("## SQL Dinâmico", _render_dynsql_lines(outbound, snippets)),
    ]

    lines: List[str] = ["# {}".format(ref), _format_metadata_line(node), ""]
    for title, content in sections:
        if not content:
            continue
        lines.append(title)
        lines.extend(content)
        lines.append("")

    return _finalize(lines)


# --------------------------------------------------------------------------
# edges.jsonl
# --------------------------------------------------------------------------


def render_edges_jsonl(edges: Sequence[DepEdge]) -> str:
    ordered = sorted(edges, key=_edge_sort_key)
    lines = []
    for e in ordered:
        payload = {
            "from_ref": e.from_ref,
            "to_ref": e.to_ref,
            "edge_type": e.edge_type,
            "line": e.line,
            "op": e.op,
            "cols": e.cols,
            "dynamic": e.dynamic,
            "confidence": e.confidence,
            "context": e.context,
            "snippet_ref": e.snippet_ref,
        }
        lines.append(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# INDEX.md
# --------------------------------------------------------------------------


def _blind_spot_lines(result: DepGraphResult) -> List[str]:
    lines: List[str] = []

    dyn_issues = sorted(
        (e for e in result.edges if e.edge_type == "DYNAMIC_SQL" and e.confidence in ("partial", "opaque")),
        key=_edge_sort_key,
    )
    if dyn_issues:
        lines.append("### SQL dinâmico não resolvido")
        for e in dyn_issues:
            line_label = "L{}".format(e.line) if e.line is not None else "L?"
            lines.append("- {} {} [{}] -> {}".format(e.from_ref, line_label, e.confidence, e.to_ref))
        lines.append("")

    no_plscope = sorted(set(result.needs_recompile))
    if no_plscope:
        lines.append("### Objetos sem PL/Scope")
        for ref in no_plscope:
            lines.append("- {}".format(ref))
        lines.append("")

    if result.truncated:
        lines.append("### Truncamento")
        lines.append("- motivo: {}".format(result.truncation_reason))
        for ref in sorted(set(result.not_expanded)):
            lines.append("- não expandido: {}".format(ref))
        lines.append("")

    return lines


def render_index_md(result: DepGraphResult, meta_params: Dict[str, Any]) -> str:
    root_ref = meta_params.get("root_ref")
    lines: List[str] = []
    lines.append("# Grafo de dependências{}".format(": {}".format(root_ref) if root_ref else ""))
    lines.append("")

    lines.append("## Estatísticas")
    for key in sorted(result.stats):
        lines.append("- {}: {}".format(key, result.stats[key]))
    lines.append("")

    # Fechamento transitivo: lista flat de tudo que a raiz alcança (plano,
    # secao 5) -- evita o consumidor ter que refazer BFS manual so pra
    # saber "quais objetos existem neste grafo".
    lines.append("## Fechamento transitivo")
    for node in sorted(result.nodes, key=lambda n: (n.owner, n.object_name)):
        marker = " (leaf)" if node.is_leaf else ""
        lines.append("- {} [{}]{}".format(_ref(node), node.object_type, marker))
    lines.append("")

    lines.append("## PONTOS CEGOS")
    blind_lines = _blind_spot_lines(result)
    if blind_lines:
        lines.extend(blind_lines)
    else:
        lines.append("- nenhum")

    return _finalize(lines)


# --------------------------------------------------------------------------
# recompile.sql (ASCII puro, so gerado se houver objeto sem PL/Scope)
# --------------------------------------------------------------------------


def _recompile_statements(object_type: str, owner: str, name: str) -> List[str]:
    ident = "{}.{}".format(owner, name)
    ot = (object_type or "").upper()
    if ot.startswith("PACKAGE"):
        return [
            "ALTER PACKAGE {} COMPILE {};".format(ident, _PLSCOPE_CLAUSE),
            "ALTER PACKAGE {} COMPILE BODY {};".format(ident, _PLSCOPE_CLAUSE),
        ]
    if ot.startswith("TYPE"):
        return [
            "ALTER TYPE {} COMPILE {};".format(ident, _PLSCOPE_CLAUSE),
            "ALTER TYPE {} COMPILE BODY {};".format(ident, _PLSCOPE_CLAUSE),
        ]
    if ot == "TRIGGER":
        return ["ALTER TRIGGER {} COMPILE {};".format(ident, _PLSCOPE_CLAUSE)]
    if ot in ("PROCEDURE", "FUNCTION"):
        return ["ALTER {} {} COMPILE {};".format(ot, ident, _PLSCOPE_CLAUSE)]
    return ["ALTER {} {} COMPILE {};".format(ot or "UNKNOWN", ident, _PLSCOPE_CLAUSE)]


def render_recompile_sql(result: DepGraphResult, nodes_by_ref: Dict[str, DepNode]) -> str:
    lines = [
        "-- recompile.sql -- gerado por /oracle-dependency-graph (plsqlflow depgraph).",
        "-- Revisar e executar manualmente -- o pipeline NUNCA roda isto.",
        "-- Objetos abaixo nao tem PLSCOPE_SETTINGS com IDENTIFIERS:ALL: as",
        "-- arestas CALL/READ/WRITE deles vieram so de ALL_DEPENDENCIES, sem",
        "-- numero de linha (plano, secao 4.1).",
        "",
    ]
    for ref in sorted(set(result.needs_recompile)):
        node = nodes_by_ref.get(ref)
        object_type = node.object_type if node is not None else "UNKNOWN"
        owner, _, name = ref.partition(".")
        lines.extend(_recompile_statements(object_type, owner, name))
    text = "\n".join(lines) + "\n"
    non_ascii = [ch for ch in text if ord(ch) > 127]
    if non_ascii:
        raise ValueError(
            "recompile.sql tem que ser ASCII puro; encontrado caractere nao-ASCII {!r}".format(non_ascii[0])
        )
    return text


# --------------------------------------------------------------------------
# meta.json (sem timestamp -- chain_hash determinístico)
# --------------------------------------------------------------------------


def compute_chain_hash(nodes: Sequence[DepNode]) -> str:
    """SHA-256 sobre a lista ORDENADA de `(owner, name, type,
    last_ddl_time)` de todos os nos (plano, secao 4.2) -- dá de graça a
    deteccao de staleness (banco mudou -> hash muda) sem depender de
    relogio."""
    items = sorted(
        (n.owner, n.object_name, n.object_type, "" if n.last_ddl_time is None else str(n.last_ddl_time))
        for n in nodes
    )
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_meta_json(result: DepGraphResult, meta_params: Dict[str, Any]) -> str:
    payload = {
        "chain_hash": compute_chain_hash(result.nodes),
        "extractor_version": _PLSQLFLOW_VERSION,
        "params": meta_params,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------
# render_graph -- entrada publica (Entrega 2 do T-04)
# --------------------------------------------------------------------------


def render_graph(
    result: DepGraphResult, out_dir: Union[Path, str], meta_params: Optional[Dict[str, Any]] = None
) -> List[Path]:
    """Grava a arvore completa (plano, secao 5) em `out_dir`. Nenhuma
    conexao de banco, nenhuma dependencia de relogio -- `result` (T-02 +
    T-03 mesclado + T-04) e `meta_params` (parametros do pipeline, ex.:
    flags de CLI; `meta_params["root_ref"]` opcionalmente rotula o
    cabecalho do INDEX.md) sao a unica entrada. Devolve a lista ordenada
    de todo arquivo escrito.

    Idempotencia byte a byte: duas chamadas com o mesmo `result`/
    `meta_params` sobre o mesmo `out_dir` produzem os mesmos bytes em
    todo arquivo -- nenhuma escrita usa relogio, iteracao de `set` sem
    ordenar antes, ou modo texto padrao (que trocaria "\\n" por "\\r\\n"
    no Windows)."""
    out_dir = Path(out_dir)
    meta_params = dict(meta_params or {})
    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_dir = out_dir / "nodes"
    if nodes_dir.exists():
        for old_file in nodes_dir.glob("*.md"):
            old_file.unlink()
    else:
        nodes_dir.mkdir(parents=True, exist_ok=True)

    nodes_by_ref = {_ref(n): n for n in result.nodes}
    written: List[Path] = []

    for node in result.nodes:
        path = nodes_dir / node_filename(node.owner, node.object_name)
        text = render_node_md(node, result.edges, nodes_by_ref, result.snippets)
        _write_text(path, text)
        written.append(path)

    edges_path = out_dir / "edges.jsonl"
    _write_text(edges_path, render_edges_jsonl(result.edges))
    written.append(edges_path)

    index_path = out_dir / "INDEX.md"
    _write_text(index_path, render_index_md(result, meta_params))
    written.append(index_path)

    if result.needs_recompile:
        recompile_path = out_dir / "recompile.sql"
        _write_text(recompile_path, render_recompile_sql(result, nodes_by_ref))
        written.append(recompile_path)
    else:
        # idempotencia: se uma geracao anterior deixou um recompile.sql
        # (grafo mudou e needs_recompile esvaziou), remove -- o disco tem
        # que refletir o result atual, nao um resíduo de execucao passada.
        stale_recompile = out_dir / "recompile.sql"
        if stale_recompile.exists():
            stale_recompile.unlink()

    meta_path = out_dir / "meta.json"
    _write_text(meta_path, render_meta_json(result, meta_params))
    written.append(meta_path)

    return sorted(written)
