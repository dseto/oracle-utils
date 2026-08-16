"""Seam de acesso (T-05, contrato depgraph-granular): implementa
`expand_access(**kwargs)`, o ponto que `plsqlflow/procgraph.py` ja chama
(import protegido, stub ate hoje devolvendo `[]` -- ver a docstring do
modulo la, secao "Seam de acesso"). Tres entregas do contrato:

1. READ/WRITE por subprograma com COLUNAS DO COMPILADOR. Hoje o modo
   nao-granular tira colunas de escrita de regex best-effort
   (`depgraph_enrich._extract_write_cols`, NAO tocado aqui -- continua
   servindo o caminho antigo). PL/Scope da isso de graca e exato:
   identificadores type=COLUMN tem `usage_context_id` apontando para o
   STATEMENT (fato provado contra o banco dev, GESTAO.FLOW_DEMO: `MSG
   COLUMN REFERENCE` com contexto=6=o usage_id do INSERT; confirmado de
   novo em GESTAO.TRG_FLOW_DEMO_LOG: `LOG_ID`/`ACAO` com contexto=3=o
   INSERT de dentro do trigger). SELECT vira READ; INSERT/UPDATE/DELETE/
   MERGE vira WRITE com `op`=tipo do statement. A tabela alvo vem do
   mesmo padrao (identificador type=TABLE/VIEW filho direto do
   statement).

2. Estado de package: variavel/constante/cursor cuja DECLARATION esta
   diretamente sob a raiz do PACKAGE/TYPE (nao dentro de nenhuma
   PROCEDURE/FUNCTION) e ESTADO COMPARTILHADO. `usage=ASSIGNMENT` vira
   `STATE_WRITE`; `usage=REFERENCE` vira `STATE_READ`; `usage=DECLARATION`
   sozinha nunca gera aresta (so declara, nao acessa). Arestas sempre
   `subprograma -> OWNER.OBJETO.__STATE__` (no sintetico, mesma
   convencao de direcao usada por CALL/READ/WRITE: sempre parte de quem
   AGE). Fato-chave que distingue estado de variavel local (provado
   contra o banco, plano secao 2 item 4): variavel LOCAL tem DECLARATION
   com `usage_context_id` apontando para a DEFINITION do subprograma que
   a declara (ex.: `V_SQL` dentro de `RUN_DYNAMIC`); variavel de package
   aponta direto para o no do PACKAGE.

3. Trigger de tabela escrita entra na travessia COMO SUBPROGRAMA. Um
   WRITE cuja tabela tem trigger(s) -- descoberta via `extractor.triggers`
   (Protocol OPCIONAL, mesmo caminho de dados de
   `depgraph._DepGraphEngine.expand_triggers`, so reusado aqui como
   CONSULTA, nao como motor) -- gera uma aresta TRIGGER_FIRES cujo
   `to_ref` e a ref de SUBPROGRAMA do trigger (3 partes, mesmo formato
   `OWNER.OBJETO.SUBPROGRAMA` usado em toda parte deste motor);
   `procgraph.py` (`_process_stmt`, editado nesta tarefa -- ver relatorio)
   reconhece esse edge_type e enfileira o trigger no motor fino, para que
   o corpo dele tambem seja expandido (nao vira folha).

Duas FORMAS de chamada, ambas vindo do mesmo seam (`kwargs["statement"]`
distingue -- ver `procgraph.py::_process_stmt`, que agora chama duas
vezes por subprograma):

- POR STATEMENT (`statement` e um `attribute.Assignment` kind=STMT):
  entregas 1 e 3.
- POR SUBPROGRAMA (`statement=None`, uma vez, antes do laco de
  statements): entrega 2. Necessario porque acesso a variavel de package
  acontece via identificador ASSIGNMENT/REFERENCE solto -- nunca vira um
  STATEMENT do PL/Scope (`V_X := 5;` nao existe em ALL_STATEMENTS) --
  entao um subprograma que so mexe em estado (sem SQL nenhum) nunca
  dispararia o seam se a chamada fosse so por statement.

Import tardio de `plsqlflow.procgraph.ProcEdge`: `procgraph.py` importa
ESTE modulo no TOPO do arquivo (import protegido, antes de `ProcEdge` ser
definido na mesma leitura de arquivo) -- um `from .procgraph import
ProcEdge` no topo deste modulo criaria import circular (procgraph.py
carregando procgraph_access.py carregando procgraph.py, que Python ve
como "parcialmente inicializado" e falha ao resolver `ProcEdge`). A
importacao acontece DENTRO de `expand_access`, quando os dois modulos ja
estao totalmente carregados (mesma tecnica padrao de Python para quebrar
ciclo de import).

Reimplementacoes deliberadamente locais (nao importadas de
`plsqlflow/attribute.py`): o walk de contexto (`_enclosing_or_synthetic`)
e a logica de overload (`_overload_positions_local`/`_suffixed_local`)
duplicam poucas linhas de `attribute.py` em vez de importar simbolos
privados (`_enclosing_chain`, `_overload_positions`, `_suffixed_name`,
`_synthetic_root`) -- mesmo padrao ja adotado em `procgraph.py`
(`_resolve_public_targets`, ver comentario la: "duplicar as poucas linhas
custa menos que expor uma funcao nova"). `attribute.assign_context` so
atribui CALL/STMT (regra 1-4 de T-01); acesso a variavel solta
(ASSIGNMENT/REFERENCE) nunca passa por ela, entao nao ha funcao publica
para reusar aqui de qualquer forma.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .depgraph_enrich import UNKNOWN_TARGET

# Tipos de `statement`/`object_cache`/edges retornados sao deliberadamente
# `Any` neste modulo (duck-typed, mesmo padrao ja usado por
# `plsqlflow.procgraph.ProcExtractor`): `Assignment`/`IdentifierRow`
# (attribute.py) e `ProcEdge` (procgraph.py) nao sao importados no topo
# para nao criar import circular com procgraph.py (ver docstring do
# modulo, secao sobre import tardio) nem para type-check-only (o ganho de
# precisao de tipo nao paga a complexidade extra de um bloco
# TYPE_CHECKING so para anotacoes).

# --------------------------------------------------------------------------
# Helpers de arvore (identico em espirito a attribute.py, duplicado -- ver
# docstring do modulo)
# --------------------------------------------------------------------------

_ENCLOSING_TYPES = ("PROCEDURE", "FUNCTION")
_STATE_VALUE_TYPES = ("VARIABLE", "CONSTANT", "CURSOR")
_READ_STMT_TYPES = ("SELECT",)
_WRITE_STMT_TYPES = ("INSERT", "UPDATE", "DELETE", "MERGE")
_WRITE_COLS_STMT_TYPES = ("INSERT", "UPDATE", "MERGE")


def _ref(owner: str, object_name: str, subprogram: str) -> str:
    # Mesmo formato de `procgraph._ProcGraphEngine._ref` (subprogram NAO
    # e uppercased -- carrega sufixo de overload/caminho aninhado ja
    # formatado por quem monta o Assignment).
    return "{}.{}.{}".format(owner.upper(), object_name.upper(), subprogram)


def _qualify(owner: str, name: str) -> str:
    if "." in name:
        return name.upper()
    return "{}.{}".format(owner.upper(), name.upper())


def _dedupe_preserve_order(names: Sequence[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for name in names:
        upper = name.upper()
        if upper in seen:
            continue
        seen.add(upper)
        out.append(name)
    return out


def _by_id_and_parent(identifiers: Sequence[Any]) -> Tuple[Dict[int, Any], Dict[int, List[Any]]]:
    by_id: Dict[int, Any] = {}
    by_parent: Dict[int, List[Any]] = {}
    for row in identifiers:
        by_id[row.usage_id] = row
        by_parent.setdefault(row.usage_context_id, []).append(row)
    return by_id, by_parent


def _overload_positions_local(identifiers: Sequence[Any]) -> Dict[str, List[int]]:
    by_name: Dict[str, List[Any]] = {}
    for row in identifiers:
        if row.usage == "DEFINITION" and (row.type or "") in _ENCLOSING_TYPES:
            by_name.setdefault(row.name, []).append(row)
    positions: Dict[str, List[int]] = {}
    for name, rows in by_name.items():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda r: (r.line, r.col, r.usage_id))
        positions[name] = [r.usage_id for r in ordered]
    return positions


def _suffixed_local(row: Any, positions: Dict[str, List[int]]) -> str:
    pos = positions.get(row.name)
    if not pos:
        return row.name
    n = pos.index(row.usage_id) + 1
    return "{}#{}".format(row.name, n)


def _enclosing_or_synthetic(
    context_id: int,
    by_id: Dict[int, Any],
    positions: Dict[str, List[int]],
    object_type: str,
) -> str:
    """Mesmo criterio de `attribute._enclosing_path`/`_synthetic_root`
    (regras 1-4 de T-01), reimplementado em miniatura -- ver docstring do
    modulo. `seen` protege contra ciclo malformado nos dados de entrada,
    mesma guarda de `attribute._enclosing_chain`."""
    chain: List[Any] = []
    current_id = context_id
    seen: set = set()
    while current_id != 0 and current_id not in seen:
        seen.add(current_id)
        node = by_id.get(current_id)
        if node is None:
            break
        if node.usage == "DEFINITION" and (node.type or "") in _ENCLOSING_TYPES:
            chain.append(node)
        current_id = node.usage_context_id
    chain.reverse()
    if chain:
        return ".".join(_suffixed_local(n, positions) for n in chain)
    if (object_type or "") in ("PACKAGE", "TYPE"):
        return "__SPEC__"
    return "__INIT__"


# --------------------------------------------------------------------------
# Entrega 2: estado de package (varredura POR SUBPROGRAMA, statement=None)
# --------------------------------------------------------------------------


def _state_edges(
    owner: str,
    object_name: str,
    subprogram: str,
    object_cache: Any,
    proc_edge_cls: Any,
) -> List[Any]:
    identifiers = list(getattr(object_cache, "body_identifiers", None) or [])
    if not identifiers:
        return []

    by_id, by_parent = _by_id_and_parent(identifiers)
    positions = _overload_positions_local(identifiers)
    root_id = next((r.usage_id for r in identifiers if r.usage_context_id == 0), None)
    if root_id is None:
        return []

    # DECLARATION de VARIABLE/CONSTANT/CURSOR por nome -- best-effort
    # (nomes duplicados no mesmo objeto sao raros; o primeiro achado
    # vence, nunca inventamos declaracao para um nome sem nenhuma).
    declarations_by_name: Dict[str, Any] = {}
    for row in identifiers:
        if row.usage == "DECLARATION" and (row.type or "") in _STATE_VALUE_TYPES:
            declarations_by_name.setdefault(row.name, row)

    object_type = getattr(object_cache, "body_type", None) or ""
    state_ref = "{}.{}.__STATE__".format(owner.upper(), object_name.upper())
    from_ref = _ref(owner, object_name, subprogram)

    edges: List[Any] = []
    for row in identifiers:
        if row.usage not in ("ASSIGNMENT", "REFERENCE"):
            continue
        if (row.type or "") not in _STATE_VALUE_TYPES:
            continue
        decl = declarations_by_name.get(row.name)
        if decl is None or decl.usage_context_id != root_id:
            # Sem DECLARATION direta sob a raiz do PACKAGE/TYPE -- LOCAL
            # (declarada dentro de um subprograma) ou nome sem declaracao
            # conhecida neste objeto. Nunca vira estado (regra dura: teste
            # negativo do contrato -- variavel local jamais compartilhada).
            continue
        enclosing = _enclosing_or_synthetic(row.usage_context_id, by_id, positions, object_type)
        if enclosing != subprogram:
            # Varredura e por subprograma -- so emite quando a ocorrencia
            # pertence ao subprograma CORRENTE (outros subprogramas do
            # mesmo objeto ganham a aresta na propria varredura deles).
            continue
        edge_type = "STATE_WRITE" if row.usage == "ASSIGNMENT" else "STATE_READ"
        edges.append(
            proc_edge_cls(from_ref=from_ref, to_ref=state_ref, edge_type=edge_type, line=row.line)
        )

    edges.sort(key=lambda e: (e.line if e.line is not None else -1, e.edge_type, e.to_ref))
    return edges


# --------------------------------------------------------------------------
# Entregas 1 e 3: READ/WRITE por statement + descoberta de trigger
# --------------------------------------------------------------------------


def _statement_children(statement: Any, object_cache: Any) -> List[Any]:
    identifiers = list(getattr(object_cache, "body_identifiers", None) or [])
    if not identifiers:
        return []
    _by_id, by_parent = _by_id_and_parent(identifiers)
    children = by_parent.get(statement.usage_id, [])
    # Ordem de origem (line, col) -- NAO usage_id: PL/Scope nao garante
    # usage_id crescente na ordem textual das colunas (fato provado contra
    # o banco dev, GESTAO.TRG_FLOW_DEMO_LOG: LOG_ID usage_id=6/col=32 vem
    # ANTES de ACAO usage_id=5/col=40 no texto "log_id, acao", apesar de
    # usage_id(ACAO) < usage_id(LOG_ID)). Ordenar por (line, col) reproduz
    # a ordem em que o compilador viu o texto.
    return sorted(children, key=lambda r: (r.line, r.col, r.usage_id))


def _discover_triggers(
    owner: str, table_name: str, extractor: Optional[Any], proc_edge_cls: Any
) -> List[Any]:
    if extractor is None or not hasattr(extractor, "triggers"):
        return []
    table_owner = owner.upper()
    table_name_u = table_name.upper()
    try:
        rows = list(extractor.triggers(table_owner, [table_name_u]) or [])
    except Exception:
        # Descoberta de trigger e best-effort (entrega 3): falha na
        # consulta nunca derruba a travessia inteira -- so fica sem
        # TRIGGER_FIRES para esta tabela (gap declarado por omissao, nao
        # excecao fatal -- mesma filosofia do resto do contrato).
        return []

    table_ref = _qualify(table_owner, table_name_u)
    edges: List[Any] = []
    for row in sorted(rows, key=lambda r: (getattr(r, "trigger_name", "") or "").upper()):
        trigger_name = getattr(row, "trigger_name", None)
        if not trigger_name:
            continue
        trig_owner = (getattr(row, "table_owner", None) or table_owner) or table_owner
        trigger_ref = _ref(trig_owner, trigger_name, trigger_name)
        edges.append(
            proc_edge_cls(
                from_ref=table_ref,
                to_ref=trigger_ref,
                edge_type="TRIGGER_FIRES",
                line=None,
                op=getattr(row, "triggering_event", None),
            )
        )
    return edges


def _table_access_edges(
    owner: str,
    object_name: str,
    subprogram: str,
    statement: Any,
    object_cache: Any,
    extractor: Optional[Any],
    proc_edge_cls: Any,
) -> List[Any]:
    stmt_type = (getattr(statement, "target", None) or "").upper()
    if stmt_type not in _READ_STMT_TYPES and stmt_type not in _WRITE_STMT_TYPES:
        # SQL dinamico (EXECUTE IMMEDIATE/OPEN FOR) e qualquer outro tipo
        # de statement ficam fora do escopo de T-05 (classificacao de SQL
        # dinamico em grao granular nao e uma das 3 entregas deste
        # contrato) -- mesma fronteira que `depgraph_enrich.
        # table_edges_from_statements` ja usa no modo nao-granular.
        return []

    children = _statement_children(statement, object_cache)
    targets = _dedupe_preserve_order(
        [row.name for row in children if (row.type or "").upper() in ("TABLE", "VIEW")]
    )
    cols = _dedupe_preserve_order(
        [row.name for row in children if (row.type or "").upper() == "COLUMN"]
    )

    from_ref = _ref(owner, object_name, subprogram)
    line = getattr(statement, "line", None)
    edges: List[Any] = []

    if stmt_type in _READ_STMT_TYPES:
        read_cols = cols or None
        if not targets:
            edges.append(
                proc_edge_cls(from_ref=from_ref, to_ref=UNKNOWN_TARGET, edge_type="READ", line=line, cols=read_cols)
            )
        else:
            for target in targets:
                edges.append(
                    proc_edge_cls(
                        from_ref=from_ref,
                        to_ref=_qualify(owner, target),
                        edge_type="READ",
                        line=line,
                        cols=read_cols,
                    )
                )
        return edges

    # WRITE (INSERT/UPDATE/DELETE/MERGE)
    to_ref = _qualify(owner, targets[0]) if targets else UNKNOWN_TARGET
    write_cols = cols if (stmt_type in _WRITE_COLS_STMT_TYPES and cols) else None
    edges.append(
        proc_edge_cls(from_ref=from_ref, to_ref=to_ref, edge_type="WRITE", line=line, op=stmt_type, cols=write_cols)
    )

    if targets:
        # Entrega 3: so tenta descobrir trigger quando a tabela alvo e
        # conhecida (nunca dispara para UNKNOWN_TARGET).
        edges.extend(_discover_triggers(owner, targets[0], extractor, proc_edge_cls))

    return edges


# --------------------------------------------------------------------------
# Ponto de entrada do seam
# --------------------------------------------------------------------------


def expand_access(
    *,
    owner: str,
    object_name: str,
    subprogram: str,
    statement: Optional[Any] = None,
    object_cache: Any = None,
    extractor: Optional[Any] = None,
    **_ignored: Any,
) -> List[Any]:
    """Seam consumido por `plsqlflow/procgraph.py::_ProcGraphEngine.
    _process_stmt`. Ver docstring do modulo para as duas formas de
    chamada (por statement vs por subprograma) e as tres entregas.

    `**_ignored` absorve kwargs futuros que `procgraph.py` venha a
    acrescentar sem quebrar esta assinatura -- mesma tolerancia que um
    Protocol estrutural ja da para metodo, aplicada aqui a funcao solta."""
    from .procgraph import ProcEdge  # import tardio -- ver docstring do modulo

    if statement is None:
        edges = _state_edges(owner, object_name, subprogram, object_cache, ProcEdge)
    elif getattr(statement, "kind", None) == "STMT":
        edges = _table_access_edges(
            owner, object_name, subprogram, statement, object_cache, extractor, ProcEdge
        )
    else:
        # CALL-kind Assignment nunca deveria chegar aqui (procgraph.py so
        # invoca o seam para STMT ou para a varredura statement=None) --
        # defensivo, nunca lanca, so nao produz nada.
        edges = []

    edges.sort(key=lambda e: (e.from_ref, e.to_ref, e.edge_type, e.line if e.line is not None else -1))
    return edges
