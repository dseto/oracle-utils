"""plsqlflow.depgraph_render -- Fase 5 do plano (T-04): materializa um
`DepGraphResult` (T-02 BFS + T-03 READ/WRITE/DYNAMIC_SQL mesclados + T-04
triggers) em disco, na arvore de `docs/plano-oracle-dependency-graph.md`
secao 5:

    <out_dir>/
    |-- INDEX.md
    |-- INDEX-<OWNER>.md   # so se o grafo passar de --index-split nos (T-04)
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
    outbound: Sequence[DepEdge],
    inbound: Sequence[DepEdge],
    nodes_by_ref: Dict[str, DepNode],
    snippets: Dict[str, str],
) -> str:
    """Monta o texto de `nodes/<OWNER>.<OBJETO>.md` -- secoes na ordem
    EXATA do plano (secao 5): Chama / Chamado por / Tabelas acessadas /
    Colunas / Triggers ativados / SQL Dinamico. Secao vazia e omitida
    (cabecalho incluso).

    T-04 (escala): `outbound`/`inbound` chegam JA FILTRADOS pra este no --
    `render_graph` indexa `result.edges` uma unica vez (`from_ref ->
    [aresta]`, `to_ref -> [aresta]`) em vez de varrer a lista inteira a
    cada chamada (O(nos) chamadas x O(arestas) por chamada = O(nos x
    arestas), minutos de CPU num grafo de 20 mil nos / 200 mil arestas).
    Unico chamador no repo e `render_graph`; a assinatura mudou de
    `edges: Sequence[DepEdge]` pra `outbound`/`inbound` separados porque
    filtrar aqui dentro exigiria receber a lista inteira de novo,
    reintroduzindo o custo quadratico que esta mudanca elimina."""
    ref = _ref(node)

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


# T-04 (escala): acima de `--index-split` nos, o INDEX.md por-no vira
# ilegivel (20 mil linhas so na secao de fechamento transitivo) -- este
# limiar decide entre o formato flat de sempre e o sumario com hubs +
# particao por owner. `_DEFAULT_INDEX_SPLIT` espelha o default de
# `cli.build_depgraph_arg_parser` (`--index-split`, default 1000) e so e
# usado quando `meta_params` nao traz a chave "index_split" -- chamadores
# antigos (ex.: `META_PARAMS` de tests/test_depgraph_render.py, que nao
# conhece esta tarefa) continuam produzindo o formato flat sem precisar
# saber que o limiar existe, e os golden tests (grafo de 5 nos) nunca
# cruzam o limiar de qualquer forma.
_DEFAULT_INDEX_SPLIT = 1000
_HUB_LIMIT = 20


def _closure_section_lines(result: DepGraphResult) -> List[str]:
    """`## Fechamento transitivo` no formato FLAT de sempre -- lista uma
    linha por no. So chamada abaixo do limiar; preserva byte a byte o
    golden test (`tests/fixtures/depgraph_golden/INDEX.md`)."""
    lines = ["## Fechamento transitivo"]
    for node in sorted(result.nodes, key=lambda n: (n.owner, n.object_name)):
        marker = " (leaf)" if node.is_leaf else ""
        lines.append("- {} [{}]{}".format(_ref(node), node.object_type, marker))
    lines.append("")
    return lines


def _degree_counts(result: DepGraphResult) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Uma unica passada por `result.edges` contando entrada/saida por
    `ref` -- O(arestas), nao O(nos x arestas). Refs que nao correspondem a
    nenhum no do grafo (UNKNOWN_TARGET = "?") entram no dicionario mas
    nunca sao consultadas (nenhum `DepNode` tem essa ref), entao nao viram
    hub por engano."""
    out_count: Dict[str, int] = {}
    in_count: Dict[str, int] = {}
    for e in result.edges:
        out_count[e.from_ref] = out_count.get(e.from_ref, 0) + 1
        in_count[e.to_ref] = in_count.get(e.to_ref, 0) + 1
    return out_count, in_count


def _top_hubs(result: DepGraphResult) -> List[Tuple[DepNode, int, int]]:
    """Os `_HUB_LIMIT` nos de maior grau (entrada+saida), desempate
    deterministico por `(-grau, owner, object_name)` -- sem isso, duas
    geracoes do MESMO `DepGraphResult` poderiam listar hubs empatados em
    ordem diferente (dict/set nao tem ordem garantida entre processos)."""
    out_count, in_count = _degree_counts(result)
    candidates = [
        (node, in_count.get(_ref(node), 0), out_count.get(_ref(node), 0)) for node in result.nodes
    ]
    candidates.sort(key=lambda triple: (-(triple[1] + triple[2]), triple[0].owner, triple[0].object_name))
    return candidates[:_HUB_LIMIT]


def _hub_section_lines(result: DepGraphResult) -> List[str]:
    """`## Hubs` -- "por onde comecar" do consumidor num grafo grande: os
    objetos mais conectados, com as contagens visiveis (plano, secao
    escopo item 4)."""
    lines = ["## Hubs"]
    for node, in_count, out_count in _top_hubs(result):
        total = in_count + out_count
        lines.append(
            "- {} [{}] (grau: {}, entrada: {}, saída: {})".format(
                _ref(node), node.object_type, total, in_count, out_count
            )
        )
    lines.append("")
    return lines


def _nodes_by_owner(result: DepGraphResult) -> Dict[str, List[DepNode]]:
    grouped: Dict[str, List[DepNode]] = {}
    for node in result.nodes:
        grouped.setdefault(node.owner, []).append(node)
    return grouped


def index_owner_filename(owner: str) -> str:
    return "INDEX-{}.md".format(sanitize_component(owner))


def _partition_summary_lines(result: DepGraphResult) -> List[str]:
    """Substitui `## Fechamento transitivo` quando o grafo passa do
    limiar: em vez da lista de nos inteira, aponta os arquivos
    `INDEX-<OWNER>.md` com a contagem de cada um -- o consumidor sabe onde
    procurar sem abrir todos."""
    counts: Dict[str, int] = {}
    for node in result.nodes:
        counts[node.owner] = counts.get(node.owner, 0) + 1

    lines = ["## Fechamento transitivo"]
    for owner in sorted(counts):
        lines.append("- {} ({} objetos)".format(index_owner_filename(owner), counts[owner]))
    lines.append("")
    return lines


def render_index_owner_md(owner: str, nodes: Sequence[DepNode]) -> str:
    """`INDEX-<OWNER>.md` -- fechamento transitivo de UM schema, no MESMO
    formato de linha (`- OWNER.OBJETO [TIPO]`, sufixo ` (leaf)`) que o
    `INDEX.md` flat usa abaixo do limiar -- quem ja sabe grepar o formato
    de hoje continua sabendo, so muda o arquivo onde procurar."""
    lines = ["# Fechamento transitivo: {}".format(owner), ""]
    for node in sorted(nodes, key=lambda n: (n.owner, n.object_name)):
        marker = " (leaf)" if node.is_leaf else ""
        lines.append("- {} [{}]{}".format(_ref(node), node.object_type, marker))
    return _finalize(lines)


def render_index_md(result: DepGraphResult, meta_params: Dict[str, Any]) -> str:
    root_ref = meta_params.get("root_ref")
    index_split = int(meta_params.get("index_split", _DEFAULT_INDEX_SPLIT))

    lines: List[str] = []
    lines.append("# Grafo de dependências{}".format(": {}".format(root_ref) if root_ref else ""))
    lines.append("")

    lines.append("## Estatísticas")
    for key in sorted(result.stats):
        lines.append("- {}: {}".format(key, result.stats[key]))
    lines.append("")

    if len(result.nodes) > index_split:
        # Grafo grande: sumario (hubs + particao por owner) em vez da
        # lista flat -- ver secao 4 do escopo do contrato depgraph-scale.
        lines.extend(_hub_section_lines(result))
        lines.extend(_partition_summary_lines(result))
    else:
        # Abaixo do limiar: NADA MUDA -- formato flat byte a byte igual ao
        # golden test (tests/fixtures/depgraph_golden/INDEX.md).
        lines.extend(_closure_section_lines(result))

    # PONTOS CEGOS e obrigatorio nos DOIS regimes, completo, nunca
    # truncado -- e a garantia de honestidade da skill (contrato,
    # escopo item 4).
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

    # Indice de arestas (T-04, escala): duas passadas sobre `result.edges`
    # em vez de uma varredura por no. `dict.setdefault(...).append(...)`
    # preserva a ordem de chegada de `result.edges` (que ja chega ordenado
    # de `to_result()`) -- isso importa porque os `sorted(...)` estaveis
    # dentro de `_render_call_out_lines`/`_render_table_access_lines`/etc.
    # dependem da ordem relativa de entrada pra desempatar chaves iguais;
    # reordenar aqui mudaria bytes do node .md pra arestas empatadas.
    # `to_ref`/`from_ref` que nao correspondem a nenhum no do grafo
    # (UNKNOWN_TARGET = "?", emitido por depgraph_enrich quando o SQL
    # dinamico nao resolve o alvo) simplesmente nunca sao consultados --
    # nao ha KeyError porque o acesso abaixo usa `.get(ref, [])`.
    outbound_by_ref: Dict[str, List[DepEdge]] = {}
    inbound_by_ref: Dict[str, List[DepEdge]] = {}
    for e in result.edges:
        outbound_by_ref.setdefault(e.from_ref, []).append(e)
        inbound_by_ref.setdefault(e.to_ref, []).append(e)

    written: List[Path] = []

    for node in result.nodes:
        ref = _ref(node)
        path = nodes_dir / node_filename(node.owner, node.object_name)
        text = render_node_md(
            node,
            outbound_by_ref.get(ref, []),
            inbound_by_ref.get(ref, []),
            nodes_by_ref,
            result.snippets,
        )
        _write_text(path, text)
        written.append(path)

    edges_path = out_dir / "edges.jsonl"
    _write_text(edges_path, render_edges_jsonl(result.edges))
    written.append(edges_path)

    index_path = out_dir / "INDEX.md"
    _write_text(index_path, render_index_md(result, meta_params))
    written.append(index_path)

    # INDEX-<OWNER>.md (T-04, escala): so existem quando o grafo passa do
    # limiar (mesma conta de `render_index_md`, refeita aqui pra decidir
    # quais arquivos gravar). Cada owner presente no grafo ganha uma
    # particao; owners que saem do grafo entre duas geracoes (ou o grafo
    # inteiro cai de volta abaixo do limiar) nao podem deixar arquivo
    # orfao pra tras -- mesma logica de `nodes/*.md`/`recompile.sql`
    # acima: o diretorio inteiro reflete o `result` atual, nao residuo de
    # execucao passada.
    index_split = int(meta_params.get("index_split", _DEFAULT_INDEX_SPLIT))
    expected_partition_names: List[str] = []
    if len(result.nodes) > index_split:
        for owner, owner_nodes in sorted(_nodes_by_owner(result).items()):
            filename = index_owner_filename(owner)
            expected_partition_names.append(filename)
            partition_path = out_dir / filename
            _write_text(partition_path, render_index_owner_md(owner, owner_nodes))
            written.append(partition_path)

    for old_partition in out_dir.glob("INDEX-*.md"):
        if old_partition.name not in expected_partition_names:
            old_partition.unlink()

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
