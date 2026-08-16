"""plsqlflow.procgraph_render -- T-08 (contrato depgraph-granular): materializa
um `ProcGraphResult` (T-03 BFS de subprogramas + T-04 fallback de grao objeto +
T-05 acesso/estado/trigger) em disco, no MESMO espirito estrutural de
`plsqlflow/depgraph_render.py` (grao objeto, ja congelado -- este modulo NUNCA
o edita, so segue a convencao: sanitizacao de nome de arquivo reusada de la
via `sanitize_component`, `_finalize`/byte-exatidao duplicados aqui pelo mesmo
motivo que `procgraph_access.py` duplica helpers de `attribute.py` -- import de
simbolo privado de outro modulo nao e o padrao deste repo).

    <out_dir>/
    |-- INDEX.md              # cabecalho + Estatisticas + Nos + COBERTURA + Ciclos + PONTOS CEGOS
    |-- edges.jsonl
    |-- nodes/<OWNER>.<OBJETO>.<SUBPROGRAMA>.md   # subprograma normal
    |-- nodes/<OWNER>.<OBJETO>.md                 # no de fallback grao objeto (T-04)
    `-- meta.json

============================================================================
SECAO 6 DO PLANO -- COBERTURA E OBRIGACAO DE PROVA, NAO RELATORIO
============================================================================

`build_coverage` roda ANTES de qualquer escrita em disco (`render_graph`
chama primeiro, mkdir/escrita so depois) e LEVANTA `CoverageError` -- nunca
"conserta" um contador -- quando uma das tres somas do plano nao fecha:

    objetos alcancados = grao subprograma + grao objeto (por motivo) + folhas de fronteira
    calls por objeto   = atribuidos a subprograma + atribuidos a __INIT__/__SPEC__ + nao atribuidos
    statements idem

Decisao de projeto registrada (a soma tem que poder QUEBRAR de verdade, senao
o teste que prova "levanta excecao" seria vazio):

- "objetos alcancados": os TRES baldes vem do proprio campo `ProcNode.grain`
  ("subprogram"/"object"/"unresolved" -- os tres unicos valores que este
  motor produz, ver procgraph.py). A soma dos baldes SEMPRE fecha contra
  `len(result.nodes)` a menos que um no carregue um `grain` desconhecido
  (bug de contrato futuro, ex.: T-XX adiciona um 4o grain sem atualizar esta
  reconciliacao) -- e exatamente esse cenario que o teste de quebra força.
  Contagem de OBJETOS DISTINTOS (owner, objeto) por balde 1/2 entra como
  informacao adicional na mesma linha (nao faz parte do invariante, que e
  por NO -- um objeto grao subprograma contribui varios nos, um por
  subprograma; um objeto grao objeto ou nao-resolvido corresponde,
  tipicamente, a um no so, mas isso NAO e garantido por construcao para o
  balde nao-resolvido: uma CALL sem `owner_hint` vira no
  `UNKNOWN.UNKNOWN.<nome_chamado>` -- owner/objeto sinteticos compartilhados
  por QUALQUER alvo externo sem pista de dono (ex.: LENGTH, SUBSTR, TO_CHAR
  chamados de lugares diferentes) -- por isso o balde 3 e contado por NO,
  nao por par (owner,objeto), e nao existe um numero "objetos distintos"
  informativo para ele).
- "calls"/"statements": classificados pela FORMA do `from_ref` de cada
  aresta (nunca pelo `to_ref` -- ver "IDENTIDADE DE NO PARA RENDER" abaixo
  para o motivo de `to_ref`/refs de no nao serem confiaveis para este fim).
  `from_ref` de aresta E SEMPRE `OWNER.OBJETO.SUBPROGRAMA[...]` (3+
  segmentos, gerado por `_ProcGraphEngine._ref`) OU `OWNER.OBJETO` (2
  segmentos, arestas copiadas do fallback via `_sync_fallback_results`) --
  nunca outra forma, dado o motor atual. `from_ref.split(".", 2)`: 2
  segmentos -> "nao atribuido" (aresta de grao objeto, PL/Scope nao
  disponivel para atribuir a um subprograma exato); 3+ segmentos com o
  terceiro igual a `__INIT__`/`__SPEC__` -> bucket proprio; caso contrario
  -> "atribuido a subprograma". Um `from_ref` com MENOS de 2 segmentos
  (malformado) cai fora dos tres baldes -- e o gatilho de quebra usado pelo
  teste de "soma nao fecha".
  `edge_type == "CALL"` alimenta a linha "calls"; qualquer OUTRO edge_type
  (READ/WRITE/STATE_READ/STATE_WRITE/TRIGGER_FIRES, e o que mais o merge do
  fallback trouxer -- SYNONYM_RESOLVES_TO/DYNAMIC_SQL) alimenta a linha
  "statements" -- e a fronteira natural entre "isto e uma chamada" e "isto e
  um efeito de um comando/estado", coerente com T-01 (CALL vs STMT).

============================================================================
SECAO 3 DO CONTRATO T-08 -- DEGRADACAO POR CAPACIDADE OPCIONAL AUSENTE
============================================================================

`ProcGraphResult` nao carrega isso (e propriedade do EXTRATOR usado, nao do
resultado) -- quem monta o pipeline (CLI real ou teste) passa
`meta_params["capabilities"]`, um dict `{"resolve_owner": bool,
"object_wrapped": bool, "triggers": bool}` (tipicamente construido com
`capabilities_from_extractor(extractor)`, `hasattr` puro, mesmo padrao
`hasattr` que `procgraph.py`/`procgraph_access.py` usam para checar estes
metodos opcionais do Protocol). Chave ausente do dict (`None`) e tratada
como "nao informado" -- distinto de `False` ("perguntamos, extractor nao
tem") -- mas os dois casos imprimem o aviso explicito na secao COBERTURA:
um extractor sem `resolve_owner` faz a travessia inter-package degradar
silenciosamente para "nao resolvido" (o mapa fica mais raso do que o
processo real) e isso tem que aparecer "em alto e bom som" no INDEX.md, nao
so em log.

============================================================================
IDENTIDADE DE NO PARA RENDER -- limitacao conhecida, registrada aqui
============================================================================

`ProcGraphResult` nao expoe o dict `ref -> ProcNode` que o motor usa
internamente (so listas `nodes`/`edges`, T-03) -- `node_ref(node)`
reconstroi o ref a partir dos campos do `ProcNode` (`OWNER.OBJETO.
SUBPROGRAMA` quando `subprogram` e nao-vazio, `OWNER.OBJETO` caso
contrario). Isso bate exatamente com todo `from_ref`/`to_ref` que o motor
produz, EXCETO um caso de canto ja existente em `procgraph.py`
(`_unresolved_ref`, modulo congelado, nao tocado aqui): uma CALL sem
`owner_hint` (nenhum indicio de dono -- ex.: chamada a uma funcao SYS.
STANDARD como LENGTH sem que `resolve_owner` a ache) usa a ref real
`__EXTERNAL__.<nome>.<signature>` na aresta, mas o `ProcNode`
correspondente carrega `owner="UNKNOWN"`, `object_name="UNKNOWN"`,
`subprogram=<nome>` -- sem o `signature` (o campo nao existe em `ProcNode`).
`node_ref` reconstroi `"UNKNOWN.UNKNOWN.<nome>"`, que NAO bate com o
`__EXTERNAL__...` real da aresta. Consequencia aceita (nao e um bug deste
modulo -- e informacao que `ProcNode` simplesmente nao carrega, e
`procgraph.py` esta congelado): o arquivo `nodes/UNKNOWN.UNKNOWN.<nome>.md`
desse no sintetico renderiza sem a secao "Chamado por" (o indice
outbound/inbound e construido a partir das refs LITERAIS das arestas, entao
a aresta continua correta e visivel em `edges.jsonl`/no arquivo do
CHAMADOR -- so o arquivo do PROPRIO no externo fica incompleto). no
`## COBERTURA` isso nao pesa: a reconciliacao usa `grain`/forma de
`from_ref`, nunca esta reconstrucao de ref.

============================================================================
DECISOES REGISTRADAS FORA DO TEXTO LITERAL DA TAREFA
============================================================================

- `recompile.sql` (existe no modo objeto, `depgraph_render.py`) NAO e
  gerado aqui: `ProcNode` nao carrega `object_type` (ao contrario de
  `DepNode`), entao nao ha como montar o `ALTER <TIPO> ... COMPILE
  PLSCOPE_SETTINGS=...` correto (PACKAGE precisa de duas linhas, TRIGGER de
  uma, etc. -- ver `depgraph_render._recompile_statements`). Os refs de
  `result.needs_recompile` continuam listados na secao COBERTURA (dado
  visivel, nunca omitido); quem precisa do script pronto roda o modo
  objeto (`python -m plsqlflow depgraph OWNER.OBJETO`, sem `--granular`)
  sobre o mesmo ref -- o `recompile.sql` daquele modo cobre o mesmo objeto.
- `--index-split` (particionamento de INDEX por owner, secao 7 do plano,
  "Escala") NAO e implementado nesta tarefa: o escopo de prova do T-08
  (Plans.md) lista COBERTURA + Ciclos + CLI + nao-regressao -- particao e
  uma otimizacao de escala ortogonal, fora da lista de prova, e nao
  implementa-la aqui nao quebra nenhum criterio de aceite. INDEX.md sempre
  usa o formato flat (lista de nos).
- mermaid: nao implementado (a propria secao 5 do contrato pede "NAO por
  default" e trata a implementacao como opcional -- "se implementar, so
  atras de flag"). Nao implementar e a escolha mais simples que ja
  satisfaz "nao aparece por default".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import __version__ as _PLSQLFLOW_VERSION
from .depgraph_render import sanitize_component
from .procgraph import ProcEdge, ProcGraphResult, ProcNode
from .procgraph_cycles import find_cycles

_VALID_GRAINS = ("subprogram", "object", "unresolved")
_INIT_SPEC_SUBPROGRAMS = ("__INIT__", "__SPEC__")
_CALL_EDGE_TYPE = "CALL"
_TABLE_EDGE_TYPES = ("READ", "WRITE")
_STATE_EDGE_TYPES = ("STATE_READ", "STATE_WRITE")
_TRIGGER_EDGE_TYPE = "TRIGGER_FIRES"

_CAPABILITY_NAMES = ("resolve_owner", "object_wrapped", "triggers")

_CAPABILITY_LABELS = {
    "resolve_owner": "resolve_owner (resolucao de CALL inter-package por signature)",
    "object_wrapped": "object_wrapped (deteccao de objeto PL/SQL wrapped)",
    "triggers": "triggers (descoberta de trigger de tabela escrita)",
}

_CAPABILITY_ABSENT_WARNING = {
    "resolve_owner": (
        "CALLs para packages ainda nao lidos podem NAO ter sido seguidas -- toda CALL "
        "que dependeria desta capacidade para atravessar objeto vira um no NAO-RESOLVIDO "
        "declarado (nunca omitido), mas o mapa pode estar mais raso do que o processo real."
    ),
    "object_wrapped": (
        "objeto PL/SQL wrapped sem esta capacidade cai no motivo generico 'sem PL/Scope "
        "identifiers' em vez do motivo especifico 'wrapped' -- a cobertura continua "
        "correta (o objeto ainda cai em grao objeto), so o motivo relatado e menos preciso."
    ),
    "triggers": (
        "tabela escrita nunca gera aresta TRIGGER_FIRES -- trigger disparado pela escrita "
        "fica fora do mapa (gap declarado aqui, nao silencioso: nenhum trigger aparece em "
        "nenhum no deste grafo)."
    ),
}


class CoverageError(ValueError):
    """Levantada quando a reconciliacao de contadores da secao COBERTURA nao
    fecha (docs/plano-depgraph-granular.md, secao 6) -- regra dura do
    contrato: numero que nao bate e a assinatura da omissao silenciosa, e o
    grafo NAO pode ser gravado como valido. `render_graph` levanta isto
    ANTES de tocar o disco (nenhum arquivo parcial fica para tras)."""


# --------------------------------------------------------------------------
# ref / nome de arquivo (ver docstring do modulo, "IDENTIDADE DE NO")
# --------------------------------------------------------------------------


def node_ref(node: ProcNode) -> str:
    if node.subprogram:
        return "{}.{}.{}".format(node.owner, node.object_name, node.subprogram)
    return "{}.{}".format(node.owner, node.object_name)


def node_filename(owner: str, object_name: str, subprogram: str) -> str:
    """Mesma sanitizacao de `depgraph_render.sanitize_component` (reusada,
    nao reinventada) aplicada a cada componente -- inclusive `subprogram`,
    que pode carregar `.` (aninhamento, ex.: `EXTERNA.INTERNA`) ou `#`
    (sufixo de overload, ex.: `CALC_OVERLOAD#2`), ambos fora de
    `[A-Za-z0-9_]` e portanto colapsados em `_` -- mesma colisao teorica ja
    documentada e aceita em `depgraph_render.sanitize_component` (ex.:
    `A.B` e `A#B` colapsam no mesmo nome), fora do escopo do MVP."""
    if subprogram:
        return "{}.{}.{}.md".format(
            sanitize_component(owner), sanitize_component(object_name), sanitize_component(subprogram)
        )
    return "{}.{}.md".format(sanitize_component(owner), sanitize_component(object_name))


def _finalize(lines: List[str]) -> str:
    trimmed = list(lines)
    while trimmed and trimmed[-1] == "":
        trimmed.pop()
    return "\n".join(trimmed) + "\n"


def _write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _edge_sort_key(e: ProcEdge) -> Tuple[str, str, str, int]:
    return (e.from_ref, e.to_ref, e.edge_type, e.line if e.line is not None else -1)


# --------------------------------------------------------------------------
# COBERTURA -- reconciliacao de contadores (ver docstring do modulo)
# --------------------------------------------------------------------------


@dataclass
class CoverageReport:
    nodes_total: int
    nodes_subprogram: int
    nodes_object: int
    nodes_unresolved: int
    calls_total: int
    calls_subprogram: int
    calls_init_spec: int
    calls_unattributed: int
    statements_total: int
    statements_subprogram: int
    statements_init_spec: int
    statements_unattributed: int
    objects_subprogram: int
    objects_object: int
    reasons: List[str] = field(default_factory=list)
    unresolved_targets: List[str] = field(default_factory=list)
    needs_recompile: List[str] = field(default_factory=list)
    capabilities: Dict[str, Optional[bool]] = field(default_factory=dict)


def _classify_from_ref(from_ref: str) -> Optional[str]:
    """"subprogram" | "init_spec" | "object" (nao atribuido) | None
    (malformado -- gatilho de `CoverageError`). Ver docstring do modulo,
    secao COBERTURA, para a justificativa completa."""
    parts = (from_ref or "").split(".", 2)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    if len(parts) == 2:
        return "object"
    if parts[2] in _INIT_SPEC_SUBPROGRAMS:
        return "init_spec"
    return "subprogram"


def _reconcile_edges(edges: Sequence[ProcEdge], label: str) -> Dict[str, int]:
    counts = {"subprogram": 0, "init_spec": 0, "object": 0}
    anomalies: List[str] = []
    for e in edges:
        bucket = _classify_from_ref(e.from_ref)
        if bucket is None:
            anomalies.append("{} -> {} ({})".format(e.from_ref, e.to_ref, e.edge_type))
            continue
        counts[bucket] += 1

    total = sum(counts.values())
    if total != len(edges) or anomalies:
        raise CoverageError(
            "COBERTURA nao fecha ({}): {} arestas totais, {} classificadas "
            "(subprograma={}, __INIT__/__SPEC__={}, nao_atribuido={}) -- arestas com "
            "from_ref malformado: {}".format(
                label,
                len(edges),
                total,
                counts["subprogram"],
                counts["init_spec"],
                counts["object"],
                "; ".join(anomalies) if anomalies else "nenhuma",
            )
        )
    return counts


def build_coverage(
    result: ProcGraphResult, capabilities: Optional[Dict[str, Optional[bool]]] = None
) -> CoverageReport:
    """Reconciliacao de contadores da secao 6 do plano. Levanta
    `CoverageError` (nunca "conserta" o numero) quando qualquer uma das
    tres somas nao fecha -- ver docstring do modulo para o desenho exato de
    cada balde. Chamada por `render_graph` ANTES de qualquer escrita em
    disco."""
    capabilities = dict(capabilities or {})
    nodes = result.nodes
    edges = result.edges

    bucket_counts = {"subprogram": 0, "object": 0, "unresolved": 0}
    anomalous_nodes: List[str] = []
    for n in nodes:
        if n.grain in _VALID_GRAINS:
            bucket_counts[n.grain] += 1
        else:
            anomalous_nodes.append(
                "{}.{}.{} (grain={!r})".format(n.owner, n.object_name, n.subprogram, n.grain)
            )

    nodes_total = len(nodes)
    nodes_sum = sum(bucket_counts.values())
    if nodes_sum != nodes_total or anomalous_nodes:
        raise CoverageError(
            "COBERTURA nao fecha (objetos alcancados): {} nos totais, {} classificados "
            "(subprograma={}, objeto={}, nao_resolvido={}) -- nos com grain desconhecido: "
            "{}".format(
                nodes_total,
                nodes_sum,
                bucket_counts["subprogram"],
                bucket_counts["object"],
                bucket_counts["unresolved"],
                "; ".join(anomalous_nodes) if anomalous_nodes else "nenhum",
            )
        )

    call_edges = [e for e in edges if e.edge_type == _CALL_EDGE_TYPE]
    stmt_edges = [e for e in edges if e.edge_type != _CALL_EDGE_TYPE]
    call_counts = _reconcile_edges(call_edges, "calls")
    stmt_counts = _reconcile_edges(stmt_edges, "statements")

    objects_subprogram = {(n.owner, n.object_name) for n in nodes if n.grain == "subprogram"}
    objects_object = {(n.owner, n.object_name) for n in nodes if n.grain == "object"}

    reasons = sorted(
        "{}.{}: {}".format(n.owner, n.object_name, n.note or "sem motivo registrado")
        for n in nodes
        if n.grain == "object"
    )
    unresolved_targets = sorted(
        "{}.{}.{}: {}".format(n.owner, n.object_name, n.subprogram, n.note or "sem motivo registrado")
        for n in nodes
        if n.grain == "unresolved"
    )

    return CoverageReport(
        nodes_total=nodes_total,
        nodes_subprogram=bucket_counts["subprogram"],
        nodes_object=bucket_counts["object"],
        nodes_unresolved=bucket_counts["unresolved"],
        calls_total=len(call_edges),
        calls_subprogram=call_counts["subprogram"],
        calls_init_spec=call_counts["init_spec"],
        calls_unattributed=call_counts["object"],
        statements_total=len(stmt_edges),
        statements_subprogram=stmt_counts["subprogram"],
        statements_init_spec=stmt_counts["init_spec"],
        statements_unattributed=stmt_counts["object"],
        objects_subprogram=len(objects_subprogram),
        objects_object=len(objects_object),
        reasons=reasons,
        unresolved_targets=unresolved_targets,
        needs_recompile=sorted(set(result.needs_recompile)),
        capabilities=capabilities,
    )


def capabilities_from_extractor(extractor: Any) -> Dict[str, bool]:
    """`{"resolve_owner": bool, "object_wrapped": bool, "triggers": bool}`
    via `hasattr` puro -- mesmo criterio de opcionalidade que
    `procgraph.py`/`procgraph_access.py` usam para checar estes metodos em
    tempo de execucao. Uso tipico: `meta_params["capabilities"] =
    capabilities_from_extractor(extractor)` antes de chamar `render_graph`."""
    return {name: hasattr(extractor, name) for name in _CAPABILITY_NAMES}


def _capability_lines(capabilities: Dict[str, Optional[bool]]) -> List[str]:
    lines = []
    for name in _CAPABILITY_NAMES:
        value = capabilities.get(name)
        label = _CAPABILITY_LABELS[name]
        if value is True:
            lines.append("- {}: disponivel".format(label))
        elif value is False:
            lines.append("- {}: AUSENTE -- {}".format(label, _CAPABILITY_ABSENT_WARNING[name]))
        else:
            lines.append(
                "- {}: NAO INFORMADO pelo chamador -- tratar como possivelmente ausente. {}".format(
                    label, _CAPABILITY_ABSENT_WARNING[name]
                )
            )
    return lines


def _coverage_lines(coverage: CoverageReport) -> List[str]:
    lines = ["## COBERTURA", ""]
    lines.append("Capacidades opcionais do extractor usado nesta geracao:")
    lines.extend(_capability_lines(coverage.capabilities))
    lines.append("")
    lines.append(
        "objetos alcancados = {} grao subprograma ({} objetos distintos) + {} grao "
        "objeto ({} objetos distintos, motivo por objeto abaixo) + {} folhas de "
        "fronteira/nao resolvidas = {} nos".format(
            coverage.nodes_subprogram,
            coverage.objects_subprogram,
            coverage.nodes_object,
            coverage.objects_object,
            coverage.nodes_unresolved,
            coverage.nodes_total,
        )
    )
    lines.append(
        "calls por objeto = {} atribuidas a subprograma + {} atribuidas a "
        "__INIT__/__SPEC__ + {} nao atribuidas (grao objeto) = {} calls".format(
            coverage.calls_subprogram,
            coverage.calls_init_spec,
            coverage.calls_unattributed,
            coverage.calls_total,
        )
    )
    lines.append(
        "statements por objeto = {} atribuidos a subprograma + {} atribuidos a "
        "__INIT__/__SPEC__ + {} nao atribuidos (grao objeto) = {} arestas de "
        "acesso/estado/trigger/SQL dinamico".format(
            coverage.statements_subprogram,
            coverage.statements_init_spec,
            coverage.statements_unattributed,
            coverage.statements_total,
        )
    )
    lines.append("")

    if coverage.reasons:
        lines.append("### Rebaixados para grao objeto (motivo por objeto)")
        lines.extend("- {}".format(r) for r in coverage.reasons)
        lines.append("")

    if coverage.unresolved_targets:
        lines.append("### Nao resolvidos (folha de fronteira)")
        lines.extend("- {}".format(r) for r in coverage.unresolved_targets)
        lines.append("")

    if coverage.needs_recompile:
        lines.append("### Recompilaveis (needs_recompile)")
        lines.append(
            "- sem recompile.sql pronto neste modo (ProcNode nao carrega object_type); "
            "rode 'python -m plsqlflow depgraph OWNER.OBJETO' (sem --granular) sobre os "
            "refs abaixo para gerar o script de recompilacao:"
        )
        lines.extend("- {}".format(r) for r in coverage.needs_recompile)
        lines.append("")

    return lines


# --------------------------------------------------------------------------
# Ciclos (T-07, consumido -- nao editado)
# --------------------------------------------------------------------------

_CYCLE_KIND_LABELS = {
    "direct_recursion": "recursao direta",
    "same_object": "recursao mutua (mesmo objeto)",
    "cross_object": "acoplamento inter-package",
}


def _cycles_lines(result: ProcGraphResult) -> List[str]:
    detection = find_cycles(result.edges)
    lines = ["## Ciclos", ""]
    if not detection.groups:
        lines.append("- nenhum")
        lines.append("")
        return lines

    for i, group in enumerate(detection.groups, start=1):
        kind_label = _CYCLE_KIND_LABELS.get(group.kind, group.kind)
        trigger_note = " -- passa por trigger" if group.via_trigger else ""
        lines.append("### ciclo-{}: {}{}".format(i, kind_label, trigger_note))
        lines.append("- membros: {}".format(", ".join(group.members)))
        objs = ", ".join("{}.{}".format(o, n) for o, n in group.objects)
        lines.append("- objetos envolvidos: {}".format(objs))
        for from_ref, to_ref, edge_type in group.internal_edges:
            lines.append("  - {} -> {} ({})".format(from_ref, to_ref, edge_type))
        lines.append("")
    return lines


# --------------------------------------------------------------------------
# nodes/<ref>.md
# --------------------------------------------------------------------------


def _format_metadata_line(node: ProcNode) -> str:
    parts = [
        "grao: {}".format(node.grain),
        "plscope_identifiers: {}".format("sim" if node.plscope_identifiers else "nao"),
        "plscope_statements: {}".format("sim" if node.plscope_statements else "nao"),
    ]
    if node.is_leaf:
        parts.append("leaf: sim")
    if node.note:
        parts.append("nota: {}".format(node.note))
    return "- " + " | ".join(parts)


def _render_call_out_lines(outbound: Sequence[ProcEdge]) -> List[str]:
    entries = sorted(
        (e for e in outbound if e.edge_type == _CALL_EDGE_TYPE),
        key=lambda e: (e.to_ref, e.line if e.line is not None else -1),
    )
    lines = []
    for e in entries:
        line_label = "L{}".format(e.line) if e.line is not None else "L?"
        text = "- {} -> {}".format(line_label, e.to_ref)
        if not e.resolved:
            text += " [NAO-RESOLVIDO: {}]".format(e.reason or "motivo nao registrado")
        lines.append(text)
    return lines


def _render_call_in_lines(inbound: Sequence[ProcEdge]) -> List[str]:
    entries = sorted(
        (e for e in inbound if e.edge_type == _CALL_EDGE_TYPE),
        key=lambda e: (e.from_ref, e.line if e.line is not None else -1),
    )
    return [
        "- {} <- {}".format("L{}".format(e.line) if e.line is not None else "L?", e.from_ref)
        for e in entries
    ]


def _render_table_lines(outbound: Sequence[ProcEdge], inbound: Sequence[ProcEdge]) -> List[str]:
    entries: List[Tuple[Tuple[int, str, int], str]] = []
    for e in outbound:
        if e.edge_type not in _TABLE_EDGE_TYPES:
            continue
        line_label = "L{}".format(e.line) if e.line is not None else "L?"
        tag = "R" if e.edge_type == "READ" else "W:{}".format(e.op or "?")
        text = "- {} {} -> {}".format(tag, line_label, e.to_ref)
        if e.cols:
            text += " (cols: {})".format(", ".join(e.cols))
        entries.append(((0, e.to_ref, e.line if e.line is not None else -1), text))
    for e in inbound:
        if e.edge_type not in _TABLE_EDGE_TYPES:
            continue
        line_label = "L{}".format(e.line) if e.line is not None else "L?"
        tag = "R" if e.edge_type == "READ" else "W:{}".format(e.op or "?")
        text = "- {} {} <- {}".format(tag, line_label, e.from_ref)
        if e.cols:
            text += " (cols: {})".format(", ".join(e.cols))
        entries.append(((1, e.from_ref, e.line if e.line is not None else -1), text))
    entries.sort(key=lambda pair: pair[0])
    return [text for _, text in entries]


def _render_state_lines(outbound: Sequence[ProcEdge]) -> List[str]:
    entries = sorted(
        (e for e in outbound if e.edge_type in _STATE_EDGE_TYPES),
        key=lambda e: (e.edge_type, e.line if e.line is not None else -1),
    )
    lines = []
    for e in entries:
        line_label = "L{}".format(e.line) if e.line is not None else "L?"
        verb = "le" if e.edge_type == "STATE_READ" else "escreve"
        lines.append("- {} {} {}".format(line_label, verb, e.to_ref))
    return lines


def _render_trigger_lines(outbound: Sequence[ProcEdge], inbound: Sequence[ProcEdge]) -> List[str]:
    lines = []
    fires_out = sorted(
        (e for e in outbound if e.edge_type == _TRIGGER_EDGE_TYPE), key=lambda e: e.to_ref
    )
    for e in fires_out:
        lines.append("- dispara {} evento:{}".format(e.to_ref, e.op or "?"))
    fires_in = sorted(
        (e for e in inbound if e.edge_type == _TRIGGER_EDGE_TYPE), key=lambda e: e.from_ref
    )
    for e in fires_in:
        lines.append("- disparado por {} evento:{}".format(e.from_ref, e.op or "?"))
    return lines


def render_node_md(node: ProcNode, ref: str, outbound: Sequence[ProcEdge], inbound: Sequence[ProcEdge]) -> str:
    lines: List[str] = ["# {}".format(ref), _format_metadata_line(node), ""]

    sections: List[Tuple[str, List[str]]] = [
        ("## Chama (outbound)", _render_call_out_lines(outbound)),
        ("## Chamado por (inbound)", _render_call_in_lines(inbound)),
        ("## Tabelas acessadas", _render_table_lines(outbound, inbound)),
        ("## Estado de package", _render_state_lines(outbound)),
        ("## Triggers ativados", _render_trigger_lines(outbound, inbound)),
    ]
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


def render_edges_jsonl(edges: Sequence[ProcEdge]) -> str:
    ordered = sorted(edges, key=_edge_sort_key)
    lines = []
    for e in ordered:
        payload = {
            "from_ref": e.from_ref,
            "to_ref": e.to_ref,
            "edge_type": e.edge_type,
            "line": e.line,
            "signature": e.signature,
            "resolved": e.resolved,
            "reason": e.reason,
            "confidence": e.confidence,
            "op": e.op,
            "cols": e.cols,
        }
        lines.append(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# INDEX.md
# --------------------------------------------------------------------------


def render_index_md(result: ProcGraphResult, coverage: CoverageReport, meta_params: Dict[str, Any]) -> str:
    root_ref = meta_params.get("root_ref")

    lines: List[str] = []
    lines.append("# Grafo de processo{}".format(": {}".format(root_ref) if root_ref else ""))
    lines.append("")

    lines.append("## Estatisticas")
    for key in sorted(result.stats):
        lines.append("- {}: {}".format(key, result.stats[key]))
    lines.append("")

    lines.append("## Nos")
    for node in sorted(result.nodes, key=lambda n: (n.owner, n.object_name, n.subprogram)):
        marker = " (leaf)" if node.is_leaf else ""
        lines.append("- {} [grao:{}]{}".format(node_ref(node), node.grain, marker))
    lines.append("")

    lines.extend(_coverage_lines(coverage))
    lines.extend(_cycles_lines(result))

    lines.append("## PONTOS CEGOS")
    blind_lines: List[str] = []
    if result.blind_spots:
        for spot in sorted(set(result.blind_spots)):
            blind_lines.append("- {}".format(spot))
    if result.truncated:
        blind_lines.append("### Truncamento")
        blind_lines.append("- motivo: {}".format(result.truncation_reason))
        for ref in sorted(set(result.not_expanded)):
            blind_lines.append("- nao expandido: {}".format(ref))
    if blind_lines:
        lines.extend(blind_lines)
    else:
        lines.append("- nenhum")

    return _finalize(lines)


# --------------------------------------------------------------------------
# meta.json
# --------------------------------------------------------------------------


def compute_chain_hash(nodes: Sequence[ProcNode]) -> str:
    """Mesma tecnica de `depgraph_render.compute_chain_hash` (SHA-256 sobre
    lista ordenada, sem relogio) adaptada aos campos de `ProcNode`
    (`owner`/`object_name`/`subprogram`/`grain`/`note` -- `ProcNode` nao tem
    `object_type`/`last_ddl_time` como `DepNode`)."""
    items = sorted(
        (n.owner, n.object_name, n.subprogram, n.grain, "" if n.note is None else n.note)
        for n in nodes
    )
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render_meta_json(result: ProcGraphResult, meta_params: Dict[str, Any]) -> str:
    payload = {
        "chain_hash": compute_chain_hash(result.nodes),
        "extractor_version": _PLSQLFLOW_VERSION,
        "params": meta_params,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


# --------------------------------------------------------------------------
# render_graph -- entrada publica
# --------------------------------------------------------------------------


def render_graph(
    result: ProcGraphResult, out_dir: Union[Path, str], meta_params: Optional[Dict[str, Any]] = None
) -> List[Path]:
    """Grava a arvore completa (ver docstring do modulo) em `out_dir`.
    `meta_params["capabilities"]` alimenta a secao COBERTURA (ver
    `capabilities_from_extractor`); demais chaves sao dados livres do
    chamador (ex.: `root_ref` rotula o cabecalho do INDEX.md, mesma
    convencao de `depgraph_render.render_graph`).

    ORDEM CRITICA: `build_coverage` roda ANTES de qualquer `mkdir`/escrita
    -- uma reconciliacao quebrada (`CoverageError`) impede que QUALQUER
    arquivo deste grafo seja gravado, mesmo parcialmente (regra dura do
    contrato: "o grafo nao e gravado como valido").

    Idempotencia byte a byte: mesma disciplina de `depgraph_render.
    render_graph` -- toda escrita usa `encoding="utf-8"`/`newline="\\n"`
    explicitos, e `nodes/*.md` antigos sao removidos antes de escrever os
    atuais (objeto que saiu do grafo entre duas geracoes nao deixa arquivo
    orfao)."""
    out_dir = Path(out_dir)
    meta_params = dict(meta_params or {})
    capabilities = meta_params.get("capabilities") or {}

    coverage = build_coverage(result, capabilities)

    out_dir.mkdir(parents=True, exist_ok=True)

    nodes_dir = out_dir / "nodes"
    if nodes_dir.exists():
        for old_file in nodes_dir.glob("*.md"):
            old_file.unlink()
    else:
        nodes_dir.mkdir(parents=True, exist_ok=True)

    outbound_by_ref: Dict[str, List[ProcEdge]] = {}
    inbound_by_ref: Dict[str, List[ProcEdge]] = {}
    for e in result.edges:
        outbound_by_ref.setdefault(e.from_ref, []).append(e)
        inbound_by_ref.setdefault(e.to_ref, []).append(e)

    written: List[Path] = []

    for node in result.nodes:
        ref = node_ref(node)
        path = nodes_dir / node_filename(node.owner, node.object_name, node.subprogram)
        text = render_node_md(node, ref, outbound_by_ref.get(ref, []), inbound_by_ref.get(ref, []))
        _write_text(path, text)
        written.append(path)

    edges_path = out_dir / "edges.jsonl"
    _write_text(edges_path, render_edges_jsonl(result.edges))
    written.append(edges_path)

    index_path = out_dir / "INDEX.md"
    _write_text(index_path, render_index_md(result, coverage, meta_params))
    written.append(index_path)

    meta_path = out_dir / "meta.json"
    _write_text(meta_path, render_meta_json(result, meta_params))
    written.append(meta_path)

    return sorted(written)
