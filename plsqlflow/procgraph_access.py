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

4. SQL dinamico (correcao de DEFEITO 1, achado ao vivo contra o banco dev
   -- `GESTAO.FLOW_DEMO.RUN_DYNAMIC`, dois `EXECUTE IMMEDIATE`): um STMT
   cujo `target` (tipo do statement) e EXECUTE IMMEDIATE/OPEN ... FOR/
   DBMS_SQL.PARSE (`_is_dynamic_stmt_type`, duplicado localmente do
   equivalente privado `depgraph_enrich._looks_dynamic_stmt_type`, mesma
   razao de duplicacao ja documentada acima) NAO cai mais no "fora de
   escopo -> []" que a entrega 1 usa para SELECT/INSERT/UPDATE/DELETE/
   MERGE. Em vez disso, `_dynamic_sql_edges` REUSA (nunca reimplementa)
   `depgraph_enrich.dynamic_sql_findings`/`dynsql.resolve_dynamic_sql` --
   a mesma classificacao resolved/partial/opaque que o modo OBJETO ja usa
   -- e vira aresta `DYNAMIC_SQL` cujo `from_ref` e o SUBPROGRAMA exato
   (`GESTAO.FLOW_DEMO.RUN_DYNAMIC`), nunca o objeto inteiro (diferenca
   chave do modo objeto, onde `from_ref` e sempre `OWNER.OBJETO`).

   `dynamic_sql_findings` precisa do TEXTO-FONTE (`Sequence[FetchSourceRow]`)
   para montar o trecho e tentar resolver o literal -- capacidade que o
   seam de T-03 nunca precisou antes (READ/WRITE/estado/trigger vem tudo
   de PL/Scope, nunca do texto). `procgraph.ProcExtractor` ganhou um
   metodo OPCIONAL novo, `source(owner, object_name)` (mesmo padrao
   `hasattr` de `resolve_owner`/`object_wrapped`/`triggers`) -- quando
   ausente (ou devolve vazio), o SQL dinamico NUNCA desaparece: vira
   marcador `opaque` declarado (aresta + ponto cego, REGRA INEGOCIAVEL do
   contrato), so com o motivo "sem fonte" em vez de tentar resolver.
   `catalog_names` (nomes de objeto conhecidos, usados so pelo nivel
   "partial" para achar candidato no fragmento irresolvivel) vem de
   `dep_extractor.object_catalog` (Protocol de `depgraph.py`, JA usado
   pelo fallback de grao objeto -- reusado aqui so como CONSULTA, mesmo
   padrao de `extractor.triggers` na entrega 3); ausencia de
   `dep_extractor` so degrada "partial" para "opaque" com mais frequencia
   (nunca vira excecao, nunca omite).

5. Correcao de DEFEITO 1 (relatorio de verificacao independente sobre
   este mesmo contrato, contra schema REAL `GESTAO_OO`, banco 21c,
   2026-08-16): `_is_dynamic_stmt_type` supunha que um `OPEN c FOR
   v_sql;` (ref cursor DINAMICO) chegaria com `stmt_type` literal
   'OPEN FOR' -- premissa NUNCA confirmada contra banco (a docstring
   antiga ja dizia isso). Fato agora provado:
   `GESTAO_OO.PKG_DYNAMIC_EVALUATOR`, PACKAGE BODY linha 29, fonte
   literal `OPEN v_cursor FOR v_sql;` (v_sql montado por concatenacao
   com `p_filtro_extra`/`p_ordenacao`, parametros de entrada -- SQL
   dinamico do tipo mais perigoso) tem `ALL_STATEMENTS.TYPE` = `'OPEN'`
   PURO, identico ao tipo de um `OPEN c;` ESTATICO (cursor ja declarado
   com SELECT proprio). `ALL_STATEMENTS.TYPE` sozinho NUNCA vai
   distinguir os dois -- so o TEXTO-FONTE pode (presenca de `FOR` depois
   do `OPEN`, antes do `;` que fecha o statement). `_open_stmt_is_dynamic`
   faz essa desambiguacao, reusando a mesma capacidade OPCIONAL `source`
   que a entrega 4 (SQL dinamico) ja consome -- ver a funcao para a
   REGRA DE DESEMPATE inegociavel (indecidivel -> DINAMICO, nunca
   NO_DATA_DEP). `_table_access_edges` checa `stmt_type == "OPEN"` ANTES
   de `_is_dynamic_stmt_type`/`_NO_DEPENDENCY_STMT_TYPES` para rotear
   pelo caminho certo.

6. Correcao de DEFEITO BLOQUEANTE (relatorio de verificacao independente
   sobre este mesmo contrato, 2026-08-16): ANTES desta correcao, um STMT
   cujo tipo nao fosse SELECT/INSERT/UPDATE/DELETE/MERGE nem dinamico
   virava `[]` incondicional (ver o comentario antigo, ainda citado por
   `_NO_DEPENDENCY_STMT_TYPES` abaixo) -- e como `procgraph_render.
   build_coverage` exige que TODO statement lido tenha pelo menos uma
   aresta, um UNICO `COMMIT`/`ROLLBACK`/`FETCH`/`CLOSE` dentro de
   QUALQUER subprograma derrubava a geracao INTEIRA (`CoverageError`,
   ZERO arquivos gravados) -- nao um ponto cego declarado, uma RECUSA
   TOTAL. Exatamente o padrao mais comum de PL/SQL batch/transacional, o
   alvo declarado do usuario ("processos gigantes, com centenas de sub
   chamadas").

   A correcao distingue DOIS casos que o `[]` antigo tratava como um so:

   (a) tipo que, POR NATUREZA, nao tem dependencia de dados --
       `_NO_DEPENDENCY_STMT_TYPES` (controle transacional: COMMIT/
       ROLLBACK/SAVEPOINT/SET TRANSACTION/LOCK TABLE; controle de cursor
       ESTATICO: OPEN/FETCH/CLOSE). Ganha uma aresta marcadora
       `NO_DATA_DEP` (`_no_dependency_edge`) -- nao aponta para nenhum
       objeto real (nao ha nenhum: e exatamente o ponto), so prova para
       a reconciliacao de COBERTURA que o statement foi CONTADO e esta
       VISIVEL (balde proprio, rotulo honesto -- nunca "[]" silencioso,
       nunca `CoverageError`).
   (b) tipo GENUINAMENTE desconhecido (nunca catalogado nem aqui nem em
       `_is_dynamic_stmt_type`) continua devolvendo `[]`. O sinal alto do
       Defeito 1 sobrevive INTATO para este caso -- `procgraph_render.
       build_coverage` ainda levanta `CoverageError` (agora com o nome
       do tipo na mensagem, ver `ProcGraphResult.statements_read` em
       `procgraph.py`), so deixa de disparar para o caso NORMAL de
       PL/SQL transacional/cursor explicito.

   `OPEN` ESTATICO (fonte prova que fecha sem "FOR") entra na familia
   (a); `OPEN` DINAMICO (fonte com "FOR", OU indecidivel -- ver DEFEITO 1
   acima e `_open_stmt_is_dynamic`) cai no ramo de SQL dinamico ACIMA --
   `stmt_type == "OPEN"` e tratado ANTES desta lista, com desambiguacao
   por texto-fonte, precisamente para que esta entrega NUNCA engula o
   caso dinamico (a versao anterior deste modulo confiava so no literal
   `stmt_type`, premissa que o DEFEITO 1 provou errada).

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

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .depgraph_enrich import UNKNOWN_TARGET, dynamic_sql_findings

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


def _is_dynamic_stmt_type(stmt_type: str) -> bool:
    """EXECUTE IMMEDIATE exato, qualquer variante de DBMS_SQL.PARSE/
    .OPEN_CURSOR, ou um `stmt_type` que JA carregue "FOR" no proprio texto
    (ex.: hipotetico "OPEN FOR" literal, nunca confirmado em Oracle real --
    ver DEFEITO 1 abaixo).

    NAO decide o caso normal de `stmt_type == "OPEN"` puro -- esse e
    tratado ANTES desta funcao, em `_table_access_edges`, via
    `_open_stmt_is_dynamic` (desambiguacao por texto-fonte). O branch
    `startswith("OPEN") and upper != "OPEN"` abaixo so sobra como rede de
    seguranca defensiva para um `stmt_type` que JA venha com sufixo
    (nunca observado contra banco real, mas inofensivo manter).

    HISTORICO DA PREMISSA CORRIGIDA (DEFEITO 1, relatorio de verificacao
    independente sobre este mesmo contrato, contra schema REAL
    `GESTAO_OO`, banco Oracle 21c, 2026-08-15/16): a versao anterior desta
    funcao supunha que um `OPEN c FOR v_sql;` DINAMICO chegaria com
    `stmt_type` literal "OPEN FOR" (premissa copiada de
    `report.py::DYNSQL_STMT_TYPES`, e a docstring antiga ja avisava:
    "NAO foi confirmado contra banco... conferir contra um schema real
    antes de tratar esta premissa como fato"). Agora conferida contra
    banco real: `GESTAO_OO.PKG_DYNAMIC_EVALUATOR`, PACKAGE BODY linha 29,
    `OPEN v_cursor FOR v_sql;` (SQL dinamico de verdade, `v_sql` montado
    por concatenacao com parametro de entrada) tem `ALL_STATEMENTS.TYPE`
    = `'OPEN'` PURO -- SEM "FOR" nenhum, identico ao tipo de um
    `OPEN c;` ESTATICO. A premissa estava ERRADA: `ALL_STATEMENTS.TYPE`
    sozinho NUNCA distingue os dois casos de OPEN; so o texto-fonte pode
    (ver `_open_stmt_is_dynamic`)."""
    upper = (stmt_type or "").upper().strip()
    if upper == "EXECUTE IMMEDIATE":
        return True
    if upper.startswith("OPEN") and upper != "OPEN":
        # Rede de seguranca defensiva -- ver docstring acima. `stmt_type`
        # puro "OPEN" NUNCA cai aqui (tratado antes, por texto-fonte).
        return True
    if "DBMS_SQL" in upper:
        return True
    return False


# Janela de linhas (a partir da linha do proprio statement, inclusive)
# usada por `_open_stmt_is_dynamic` para procurar o fechamento (";") de um
# `OPEN ...;` -- statement PL/SQL raramente quebra em mais de 2-3 linhas,
# mas a janela e generosa (o custo de olhar linhas a mais e desprezivel,
# e uma janela curta demais aumentaria falsos "indecidivel", que a regra
# de desempate ja resolve para DINAMICO mesmo assim -- a janela so afeta
# quantas vezes conseguimos provar ESTATICO com certeza).
_OPEN_SOURCE_WINDOW = 6

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_\$#]*")


def _open_stmt_is_dynamic(
    owner: str, object_name: str, line: Optional[int], extractor: Optional[Any]
) -> bool:
    """Desambigua `OPEN c;` ESTATICO (cursor ja declarado com SELECT
    proprio) de `OPEN c FOR v_sql;` DINAMICO (ref cursor aberto com SQL
    montado em runtime) usando o TEXTO-FONTE da linha do statement --
    `ALL_STATEMENTS.TYPE` sozinho NUNCA distingue os dois (DEFEITO 1, ver
    `_is_dynamic_stmt_type` para o fato confirmado contra banco real).

    REGRA DE DESEMPATE, INEGOCIAVEL (mandato explicito do relatorio de
    verificacao independente): quando NAO for possivel decidir --
    `extractor` sem a capacidade OPCIONAL `source`, `line` ausente, fonte
    que nao cobre a linha, ou o statement nao fecha (";") dentro da
    janela de busca (`_OPEN_SOURCE_WINDOW`) -- devolve True (DINAMICO).

    A falha e ASSIMETRICA, e essa assimetria e o motivo da regra: um
    ponto cego A MAIS (um `OPEN` estatico classificado como
    `DYNAMIC_SQL` opaque, aparecendo em PONTOS CEGOS por engano) e ruido
    que uma pessoa descarta em segundos ao ler o texto do cursor. Um
    ponto cego A MENOS (um `OPEN ... FOR` dinamico classificado como
    `NO_DATA_DEP`, desaparecendo de PONTOS CEGOS) e SQL dinamico inteiro
    ficando invisivel num mapa que sera usado para reescrever o processo
    -- exatamente o DEFEITO 1. Entre as duas falhas possiveis, so a
    primeira e aceitavel; por isso o default do "nao sei" e sempre
    DINAMICO, nunca ESTATICO. So devolve False quando o fonte PROVA,
    dentro da janela, que o statement fecha (";") sem nenhum "FOR" entre
    "OPEN" e o fechamento."""
    if extractor is None or not hasattr(extractor, "source") or not line:
        return True
    try:
        source = list(extractor.source(owner, object_name) or [])
    except Exception:
        # Fonte e best-effort (mesma filosofia de `_discover_triggers`/
        # `_dynamic_sql_edges`): falha na consulta nunca decide por
        # ESTATICO -- regra de desempate manda DINAMICO.
        return True
    if not source:
        return True

    by_line: Dict[int, str] = {row.line: (row.text or "") for row in source}
    window_parts: List[str] = []
    closed = False
    for offset in range(_OPEN_SOURCE_WINDOW):
        text = by_line.get(line + offset)
        if text is None:
            break
        window_parts.append(text)
        if ";" in text:
            closed = True
            break

    if not window_parts or not closed:
        # Linha ausente na fonte, ou statement nao fecha dentro da janela
        # -- indecidivel, regra de desempate manda DINAMICO.
        return True

    before_semicolon = "".join(window_parts).split(";", 1)[0]
    tokens = _WORD_RE.findall(before_semicolon.upper())
    return "FOR" in tokens


# --------------------------------------------------------------------------
# DEFEITO BLOQUEANTE (correcao): statement sem dependencia de dados por
# natureza -- ver secao 6 da docstring do modulo.
# --------------------------------------------------------------------------

# Duas familias, cada uma com o PORQUE registrado:
#
# 1. CONTROLE TRANSACIONAL (COMMIT/ROLLBACK/SAVEPOINT/SET TRANSACTION/
#    LOCK TABLE): o efeito e sobre o ESTADO DA TRANSACAO (ou um lock de
#    tabela inteira, sem alvo de coluna), nunca sobre dado de uma tabela/
#    coluna especifica -- nao ha READ nem WRITE para atribuir, por
#    construcao da propria linguagem (nenhum destes statements referencia
#    dado, so o controla).
#
# 2. CONTROLE DE CURSOR ESTATICO (OPEN/FETCH/CLOSE -- `stmt_type`
#    literalmente "OPEN" para os dois casos, estatico e dinamico; ver
#    DEFEITO 1 e `_open_stmt_is_dynamic` para a desambiguacao por
#    texto-fonte que decide QUANDO um "OPEN" chega aqui -- so quando a
#    fonte PROVA que fecha sem "FOR"; indecidivel ou dinamico nunca
#    chegam neste balde): o SELECT do cursor ja foi registrado como
#    STATEMENT proprio na DECLARACAO dele (`CURSOR c IS SELECT ...`) --
#    esse SELECT sim gera READ normalmente, pela mesma
#    `_table_access_edges` (entrega 1 acima). OPEN/FETCH/CLOSE so movem
#    o ponteiro de um cursor JA aberto -- reafirmar a mesma tabela como
#    dependencia de novo duplicaria a informacao, nunca completaria.
_NO_DEPENDENCY_STMT_TYPES = (
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "SET TRANSACTION",
    "LOCK TABLE",
    "OPEN",
    "FETCH",
    "CLOSE",
)

# edge_type sintetico desta correcao -- nunca aparece em
# `_TABLE_EDGE_TYPES`/`_STATE_EDGE_TYPES`/`_TRIGGER_EDGE_TYPE` (secoes do
# node .md em procgraph_render.py), entao nao some com nada que ja existe;
# so entra na reconciliacao de COBERTURA como balde proprio.
_NO_DATA_DEP_EDGE_TYPE = "NO_DATA_DEP"
# marcador sintetico fixo -- nunca um objeto real (nao ha nenhum para um
# COMMIT apontar; mesmo espirito de `UNKNOWN_TARGET`, mas semanticamente
# distinto: UNKNOWN_TARGET diz "alvo desconhecido", este diz "nao ha alvo
# nenhum, por natureza do statement").
_NO_DATA_DEP_TARGET = "__NO_DATA_DEP__"


def _no_dependency_edge(
    owner: str,
    object_name: str,
    subprogram: str,
    statement: Any,
    stmt_type: str,
    proc_edge_cls: Any,
) -> Any:
    """Aresta marcadora para um STMT cujo tipo esta em
    `_NO_DEPENDENCY_STMT_TYPES` -- prova para `procgraph_render.
    build_coverage` que o statement foi CONTADO e esta VISIVEL, mesmo sem
    nenhum READ/WRITE real (nao ha dado nenhum para apontar, por
    natureza do statement -- ver secao 6 da docstring do modulo). `op`
    carrega o `stmt_type` exato (COMMIT/ROLLBACK/FETCH/etc.), mesma
    convencao de `WRITE.op`, para quem ler `edges.jsonl`/o node .md saber
    qual statement gerou a aresta."""
    return proc_edge_cls(
        from_ref=_ref(owner, object_name, subprogram),
        to_ref=_NO_DATA_DEP_TARGET,
        edge_type=_NO_DATA_DEP_EDGE_TYPE,
        line=getattr(statement, "line", None),
        op=stmt_type,
    )


def _catalog_names_for(owner: str, dep_extractor: Optional[Any]) -> List[str]:
    """Nomes de objeto conhecidos do owner, usados so pelo nivel "partial"
    de `dynamic_sql_findings` (candidato precisa bater com o catalogo).
    `dep_extractor` e o Protocol `depgraph.DepExtractor` (o mesmo que o
    fallback de grao objeto ja usa) -- CONSULTA pontual, nao motor;
    ausencia (extractor None ou sem `object_catalog`) so degrada mais
    achados para "opaque" em vez de "partial", nunca lanca nem omite."""
    if dep_extractor is None or not hasattr(dep_extractor, "object_catalog"):
        return []
    try:
        rows = dep_extractor.object_catalog(owner) or []
    except Exception:
        # Best-effort (mesma filosofia de `_discover_triggers` abaixo):
        # falha na consulta de catalogo nunca derruba a travessia, so
        # reduz a chance de um achado "partial" (cai para "opaque").
        return []
    return [row.object_name for row in rows if getattr(row, "object_name", None)]


def _dynamic_sql_edges(
    owner: str,
    object_name: str,
    subprogram: str,
    statement: Any,
    object_cache: Any,
    extractor: Optional[Any],
    dep_extractor: Optional[Any],
    proc_edge_cls: Any,
) -> List[Any]:
    """DEFEITO 1 (achado ao vivo, banco dev): `GESTAO.FLOW_DEMO.
    RUN_DYNAMIC` tem dois `EXECUTE IMMEDIATE` que sumiam sem rastro --
    `_table_access_edges` devolvia `[]` para qualquer statement que nao
    fosse SELECT/INSERT/UPDATE/DELETE/MERGE. REUSA (nunca reimplementa) a
    classificacao resolved/partial/opaque de `depgraph_enrich.
    dynamic_sql_findings`/`dynsql.resolve_dynamic_sql` -- a mesma que o
    modo objeto ja usa -- so trocando o DONO da aresta: `from_ref` e o
    SUBPROGRAMA exato (`_ref`), nunca o objeto inteiro.

    REGRA INEGOCIAVEL (ver docstring do modulo): esta funcao SEMPRE
    devolve pelo menos uma aresta DYNAMIC_SQL para a linha -- resolvida
    (`confidence="exact"`), parcial (`confidence="partial"`, uma por
    candidato) ou, na falta de capacidade/dado suficiente para tentar
    resolver, um marcador `confidence="opaque"` com
    `to_ref=UNKNOWN_TARGET`. Nunca devolve `[]` para uma linha que o
    proprio PL/Scope ja marcou como statement dinamico -- e exatamente
    essa omissao que o Defeito 1 provou em producao."""
    from_ref = _ref(owner, object_name, subprogram)
    line = getattr(statement, "line", None)
    opaque = [
        proc_edge_cls(
            from_ref=from_ref,
            to_ref=UNKNOWN_TARGET,
            edge_type="DYNAMIC_SQL",
            line=line,
            confidence="opaque",
        )
    ]

    if extractor is None or not hasattr(extractor, "source"):
        # Capacidade OPCIONAL ausente no extractor (secao COBERTURA de
        # procgraph_render.py declara isso "em alto e bom som", nao so em
        # log) -- sem texto-fonte nao ha como tentar resolver NEM montar o
        # trecho da entrega parcial; o achado ainda tem que aparecer,
        # entao vira opaque declarado, nunca some.
        return opaque

    try:
        source = list(extractor.source(owner, object_name) or [])
    except Exception:
        # Fonte e best-effort (mesma filosofia de `_discover_triggers`):
        # falha na consulta nunca derruba a travessia inteira -- so esta
        # linha cai para opaque.
        source = []

    if not source:
        return opaque

    statements = list(getattr(object_cache, "body_statements", None) or [])
    catalog_names = _catalog_names_for(owner, dep_extractor)
    findings = dynamic_sql_findings(statements, source, catalog_names, owner, object_name)

    matches = [f for f in findings if f.line == line]
    if not matches:
        # Defensivo: `dynamic_sql_findings` classifica toda linha que
        # `detect_dynamic_lines` (PL/Scope + fonte) acha; se a linha deste
        # STMT (que o proprio PL/Scope ja marcou dinamico) nao aparecer
        # entre os achados, nao inventamos resolucao -- ainda declara
        # opaque em vez de sumir.
        return opaque

    edges: List[Any] = []
    for finding in matches:
        for dep_edge in finding.edges:
            edges.append(
                proc_edge_cls(
                    from_ref=from_ref,
                    to_ref=dep_edge.to_ref,
                    edge_type="DYNAMIC_SQL",
                    line=dep_edge.line,
                    confidence=dep_edge.confidence,
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
    dep_extractor: Optional[Any],
    proc_edge_cls: Any,
) -> List[Any]:
    stmt_type = (getattr(statement, "target", None) or "").upper()

    if stmt_type == "OPEN":
        # DEFEITO 1 (correcao): `stmt_type` puro "OPEN" nao distingue
        # cursor ESTATICO de DINAMICO (ver `_open_stmt_is_dynamic`) --
        # decidido por texto-fonte ANTES de qualquer outra classificacao,
        # regra de desempate embutida na propria funcao (indecidivel ->
        # DINAMICO).
        if _open_stmt_is_dynamic(owner, object_name, getattr(statement, "line", None), extractor):
            return _dynamic_sql_edges(
                owner, object_name, subprogram, statement, object_cache, extractor, dep_extractor, proc_edge_cls
            )
        return [_no_dependency_edge(owner, object_name, subprogram, statement, stmt_type, proc_edge_cls)]

    if _is_dynamic_stmt_type(stmt_type):
        return _dynamic_sql_edges(
            owner, object_name, subprogram, statement, object_cache, extractor, dep_extractor, proc_edge_cls
        )

    if stmt_type in _NO_DEPENDENCY_STMT_TYPES:
        # Correcao do DEFEITO BLOQUEANTE (secao 6 da docstring do modulo):
        # tipo SEM dependencia de dados POR NATUREZA (controle
        # transacional ou cursor ESTATICO) -- aresta marcadora
        # `NO_DATA_DEP`, nunca `[]`. Distinto do ramo abaixo: aqui o tipo
        # E CONHECIDO e a ausencia de READ/WRITE e a resposta CORRETA,
        # nao uma lacuna.
        return [_no_dependency_edge(owner, object_name, subprogram, statement, stmt_type, proc_edge_cls)]

    if stmt_type not in _READ_STMT_TYPES and stmt_type not in _WRITE_STMT_TYPES:
        # Tipo GENUINAMENTE desconhecido -- nem SELECT/INSERT/UPDATE/
        # DELETE/MERGE, nem dinamico (`_is_dynamic_stmt_type`), nem um
        # dos tipos catalogados como "sem dependencia por natureza"
        # (`_NO_DEPENDENCY_STMT_TYPES`) logo acima. Devolve `[]`
        # deliberadamente: a reconciliacao de COBERTURA (procgraph_render.
        # py, defeito 2) e quem PROVA que nenhum statement lido fica sem
        # representacao -- um tipo nunca visto antes precisa quebrar ALTO
        # (CoverageError, com o nome do tipo na mensagem) em vez de deixar
        # o gap passar batido, mesma filosofia que pegou o Defeito 1. Esta
        # e a UNICA categoria que ainda propaga ate CoverageError.
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
    dep_extractor: Optional[Any] = None,
    **_ignored: Any,
) -> List[Any]:
    """Seam consumido por `plsqlflow/procgraph.py::_ProcGraphEngine.
    _process_stmt`. Ver docstring do modulo para as duas formas de
    chamada (por statement vs por subprograma) e as quatro entregas.

    `dep_extractor` (entrega 4, SQL dinamico): Protocol `depgraph.
    DepExtractor`, repassado pelo motor SO para `_catalog_names_for` (nivel
    "partial" de `dynamic_sql_findings`) -- `None` e valido (degrada para
    mais "opaque", nunca lanca).

    `**_ignored` absorve kwargs futuros que `procgraph.py` venha a
    acrescentar sem quebrar esta assinatura -- mesma tolerancia que um
    Protocol estrutural ja da para metodo, aplicada aqui a funcao solta."""
    from .procgraph import ProcEdge  # import tardio -- ver docstring do modulo

    if statement is None:
        edges = _state_edges(owner, object_name, subprogram, object_cache, ProcEdge)
    elif getattr(statement, "kind", None) == "STMT":
        edges = _table_access_edges(
            owner, object_name, subprogram, statement, object_cache, extractor, dep_extractor, ProcEdge
        )
    else:
        # CALL-kind Assignment nunca deveria chegar aqui (procgraph.py so
        # invoca o seam para STMT ou para a varredura statement=None) --
        # defensivo, nunca lanca, so nao produz nada.
        edges = []

    edges.sort(key=lambda e: (e.from_ref, e.to_ref, e.edge_type, e.line if e.line is not None else -1))
    return edges
