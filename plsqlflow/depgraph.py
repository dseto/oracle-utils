"""Grafo de dependencias Oracle, com grao OBJETO.

Este modulo e o nucleo do subcomando `depgraph` do pacote `plsqlflow`:
percorre o fechamento transitivo de dependencias de um objeto raiz (busca
em largura sobre ALL_DEPENDENCIES, enriquecida com PL/Scope, triggers das
tabelas escritas e classificacao de SQL dinamico) e materializa o
resultado em `oracle-graph/<OWNER>.<RAIZ>/` para consumo por grep -- ver
docs/plano-oracle-dependency-graph.md.

Diferenca de grao para `plsqlflow/graph.py` (congelado por golden test do
contrato `plsqlflow-py`, NAO alterar): `graph.py` modela o grafo de
CHAMADAS entre SUBPROGRAMAS (procedure/function dentro de um pacote) para
uma unica cadeia de execucao a partir de uma entrada especifica.
`depgraph.py` modela o grafo de dependencias entre OBJETOS inteiros
(pacote, tabela, trigger, view, sinonimo, tipo -- um objeto = um no, com
status, situacao de PL/Scope e, quando for tabela, suas colunas) para o
fechamento transitivo completo alcancavel a partir de uma raiz. Os dois
modulos nao compartilham tipos nem sao intercambiaveis.

Fases (ver docs/plano-oracle-dependency-graph.md, secao 3):
- T-02 (`depgraph.py`, fases 0-1): `build_dep_graph` -- BFS sobre
  ALL_DEPENDENCIES que produz `DepNode`/`DepEdge` (CALL/SYNONYM_RESOLVES_TO).
- T-03 (`depgraph_enrich.py`, modulo irmao, NAO editado aqui): cruzamento
  statement<->tabela e SQL dinamico -- produz `DepEdge` READ/WRITE/
  DYNAMIC_SQL a partir de PL/Scope, consumidas externamente (fora deste
  modulo) e mescladas num `DepGraphResult` antes da fase 4.
- T-04 (`depgraph.py`, esta tarefa): `add_trigger_phase` -- fase 4 do
  plano. Para cada tabela que recebeu aresta WRITE, busca os triggers
  dela e os re-injeta na mesma BFS (motor `_DepGraphEngine`, compartilhado
  com `build_dep_graph` para nao duplicar a logica de classificacao/
  PL-Scope/self-loop/fronteira/sinonimo) para expandir as dependencias
  proprias de cada trigger. Tambem popula `DepNode.columns` (extensao
  alem do texto literal da fase 4 -- ver decisao registrada em
  `add_trigger_phase`), necessaria para a secao `## Colunas` do node .md
  que `depgraph_render.py` grava.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Protocol, Sequence, Set, Tuple, Union

from .extract import (
    DepsDirectRow,
    ObjectCatalogRow,
    PlscopeCheckRow,
    SynonymRow,
    TabColumnRow,
    TriggerRow,
)
from .resolve import resolve_synonym_chain

# Valores validos para DepEdge.edge_type (plano, secao 5). Documentado como
# tupla para uso por testes/validacoes futuras -- nao aplicado aqui como
# constraint, pois esta tarefa nao inclui logica.
EDGE_TYPES = (
    "CALL",
    "READ",
    "WRITE",
    "TRIGGER_FIRES",
    "SYNONYM_RESOLVES_TO",
    "DYNAMIC_SQL",
)

# Tipos de objeto que passam por all_plsql_object_settings (candidatos a
# PL/Scope). Objetos fora desse conjunto (TABLE, VIEW, SYNONYM, SEQUENCE...)
# nunca entram em needs_recompile -- a pergunta "tem PL/Scope?" nao se aplica
# a eles.
PLSQL_OBJECT_TYPES = {
    "PACKAGE",
    "PACKAGE BODY",
    "PROCEDURE",
    "FUNCTION",
    "TYPE",
    "TYPE BODY",
    "TRIGGER",
}

# Marcador de alvo indeterminado usado por `plsqlflow/depgraph_enrich.py`
# (T-03, modulo congelado -- nao importado aqui para nao criar dependencia
# circular: depgraph_enrich importa DepEdge deste modulo). Copia literal
# do valor de `depgraph_enrich.UNKNOWN_TARGET`; a fase 4 usa isso so para
# ignorar arestas WRITE sem alvo determinavel ao decidir quais tabelas
# consultar triggers.
_UNKNOWN_WRITE_TARGET = "?"


@dataclass
class DepNode:
    """Um objeto do grafo de dependencias (grao OBJETO).

    `plscope` indica se o objeto tem dados de PL/Scope disponiveis (ver
    plano, secao 4.1) -- quando False, as arestas CALL/READ/WRITE desse
    objeto vem apenas de ALL_DEPENDENCIES (sem numero de linha), e o
    objeto entra nos pontos cegos do INDEX.md.

    `source_first_line`/`source_last_line` sao None para objetos sem
    fonte PL/SQL (ex.: TABLE, TRIGGER sem corpo capturado como fonte
    separada) ou quando o fonte ainda nao foi lido.

    `is_leaf` (T-02): True quando a BFS deliberadamente nao expandiu o
    objeto -- fronteira de schema/prefixo (`stop_schemas`, `DBMS_`/`UTL_`),
    alvo de sinonimo terminando em db_link (folha opaca), ou objeto sem
    nenhuma dependencia de saida encontrada. `note` (T-02) e um marcador
    textual opcional (ex.: `"db_link:REMOTE_LINK"`); None quando nao se
    aplica.

    `last_ddl_time` (T-04): copiado de `ObjectCatalogRow.last_ddl_time` no
    momento da classificacao (`_DepGraphEngine.classify`); None para nos
    sem linha de catalogo (fronteira, db_link, sinonimo intermediario).
    Alimenta o `chain_hash` de `depgraph_render.py` (plano, secao 4.2).

    `trigger_status` (T-04): ENABLED|DISABLED, preenchido so para nos
    TRIGGER descobertos pela fase 4 (`add_trigger_phase`); None para
    qualquer outro tipo de no. Trigger desabilitado ainda entra no grafo
    -- so fica marcado aqui e em `note` (plano, secao 4.4).

    `columns` (T-04): lista de `TabColumnRow` para nos TABLE, preenchida
    por `add_trigger_phase`/`populate_table_columns` (decisao registrada
    la); None para nos que nao sao tabela ou cujas colunas nao foram
    buscadas.
    """

    owner: str
    object_name: str
    object_type: str
    status: str
    plscope: bool
    source_first_line: Optional[int] = None
    source_last_line: Optional[int] = None
    is_leaf: bool = False
    note: Optional[str] = None
    last_ddl_time: Optional[Any] = None
    trigger_status: Optional[str] = None
    columns: Optional[List[TabColumnRow]] = None


@dataclass
class DepEdge:
    """Uma aresta do grafo de dependencias.

    `from_ref`/`to_ref` sao referencias `OWNER.OBJECT_NAME` (mesmo
    formato do nome do arquivo em `nodes/`). A aresta e gravada nos dois
    nos (outbound na origem, inbound no destino) -- redundancia
    proposital para grep bidirecional sem join (plano, secao 5).

    `edge_type` deve ser um dos valores de `EDGE_TYPES`.
    `op` (ex.: 'INSERT'/'UPDATE'/'DELETE'/'MERGE') so se aplica a
    edge_type WRITE -- e (T-04) o evento (`triggering_event`, ex.:
    'INSERT') para edge_type TRIGGER_FIRES. `cols` e best-effort (plano,
    secao 4.7) -- ausencia nao e erro. `context` fica reservado para
    contexto estrutural (loop/branch/handler), nao-objetivo do MVP
    (plano, secao 4.6). `snippet_ref` aponta para o trecho de SQL
    dinamico embutido no node .md quando `dynamic` for True.
    """

    from_ref: str
    to_ref: str
    edge_type: str
    line: Optional[int]
    op: Optional[str] = None
    cols: Optional[List[str]] = None
    dynamic: bool = False
    confidence: str = "exact"
    context: Optional[str] = None
    snippet_ref: Optional[str] = None


# --------------------------------------------------------------------------
# T-02: BFS sobre ALL_DEPENDENCIES (fases 0-1 do plano).
# T-04: fase 4 (triggers re-injetados) reusa o mesmo motor (_DepGraphEngine).
# --------------------------------------------------------------------------


class DepExtractor(Protocol):
    """Fonte de dados consumida pela BFS de dependencias (`build_dep_graph`)
    e pela fase 4 (`add_trigger_phase`).

    Mesmo padrao do Protocol `Extractor` de `graph.py`: no CLI real e
    implementado aplicando parcialmente as funcoes de `extract.py` a uma
    conexao (ex.: `lambda owner, name: extract.fetch_deps_direct(conn,
    owner, name)`); nos testes e um fake alimentado por
    `tests/fixtures/depgraph_extract.json` e/ou fixtures sinteticas locais.
    `build_dep_graph`/`add_trigger_phase` nunca tocam rede/banco diretamente.

    `triggers`/`tab_columns` (T-04) foram acrescentados ao Protocol para a
    fase 4: `triggers(owner, table_names)` espelha
    `extract.fetch_triggers_for_tables` (mesmo padrao de `table_names`
    lista + `owner` unico usado por `report.Extractor.triggers`, ja
    consumido pelo CLI existente do modo flow); `tab_columns(owner,
    table_names)` espelha `extract.fetch_tab_columns` e alimenta
    `DepNode.columns` para a secao `## Colunas` do node .md.
    """

    def deps_direct(self, owner: str, name: str) -> List[DepsDirectRow]: ...

    def object_catalog(
        self, owner: str, object_list: Optional[str] = None
    ) -> List[ObjectCatalogRow]: ...

    def plscope_check(self, owner: str) -> List[PlscopeCheckRow]: ...

    def synonym(self, owner: str, name: str) -> List[SynonymRow]: ...

    def triggers(self, owner: str, table_names: Sequence[str]) -> List[TriggerRow]: ...

    def tab_columns(self, owner: str, table_names: Sequence[str]) -> List[TabColumnRow]: ...


@dataclass(frozen=True)
class DepRoot:
    """Raiz da BFS. `build_dep_graph` tambem aceita uma tupla simples
    `(owner, object_name)` -- ver `_normalize_root`."""

    owner: str
    object_name: str


RootLike = Union[Tuple[str, str], DepRoot]


@dataclass
class DepGraphResult:
    """Resultado da BFS (T-02) e/ou da fase 4 (T-04). `truncated`/
    `truncation_reason`/`not_expanded` sao o contrato explicito do
    criterio de aceite "avisa quando atinge o limite de tamanho em vez de
    truncar calado" -- quando `truncated` e True, `truncation_reason`
    nunca e None e `not_expanded` nunca esta vazio.

    `snippets` (T-04): mapa `snippet_ref -> texto do trecho de fonte`
    para as arestas DYNAMIC_SQL (`DepEdge.snippet_ref`). Extensao alem do
    texto literal da T-02/T-03 -- necessaria para `depgraph_render.py`
    montar a secao `## SQL Dinamico` do node .md a partir so de `result`
    (a assinatura de `render_graph` nao recebe extractor nem conexao;
    ver decisao registrada em `depgraph_render.py`). Vazio por padrao;
    quem monta o pipeline completo (T-05/cli.py, fora desta tarefa)
    preenche a partir de `DynFinding.snippet`/`snippet_ref`
    (`depgraph_enrich.dynamic_sql_findings`).
    """

    nodes: List[DepNode]
    edges: List[DepEdge]
    needs_recompile: List[str]
    truncated: bool
    truncation_reason: Optional[str]
    not_expanded: List[str]
    stats: dict
    snippets: Dict[str, str] = field(default_factory=dict)


def _normalize_root(root: RootLike) -> Tuple[str, str]:
    if isinstance(root, (tuple, list)):
        if len(root) != 2:
            raise ValueError(
                "root tupla deve ter exatamente 2 elementos (owner, object_name), recebeu {!r}".format(root)
            )
        return str(root[0]), str(root[1])
    owner = getattr(root, "owner", None)
    object_name = getattr(root, "object_name", None)
    if owner is None or object_name is None:
        raise TypeError(
            "root deve ser tupla (owner, object_name) ou objeto com atributos "
            ".owner/.object_name (ex.: DepRoot); recebeu {!r}".format(root)
        )
    return owner, object_name


class _DepGraphEngine:
    """Motor de BFS compartilhado por `build_dep_graph` (fases 0-1, T-02) e
    por `add_trigger_phase` (fase 4, T-04).

    Toda a logica de classificacao/PL-Scope/self-loop/fronteira/sinonimo
    do T-02 mora aqui, extraida de `build_dep_graph` sem mudanca de
    comportamento (os 18 testes de `tests/test_depgraph_bfs.py` cobrem
    isso). A fase 4 nao duplica essa logica: `add_trigger_phase` reidrata
    um motor a partir de um `DepGraphResult` ja existente (`from_result`),
    semeia a fila com as tabelas/triggers descobertos (`expand_triggers`)
    e deixa `run()` -- o mesmo laco de `build_dep_graph` -- expandir as
    dependencias proprias de cada trigger.
    """

    def __init__(
        self,
        extractor: DepExtractor,
        stop_schemas: Sequence[str],
        max_objects: int,
        max_depth: int,
    ) -> None:
        self.extractor = extractor
        self.stop_schemas_u = tuple(s.upper() for s in stop_schemas)
        self.max_objects = max_objects
        self.max_depth = max_depth

        self.nodes: Dict[Tuple[str, str], DepNode] = {}
        self.edges: List[DepEdge] = []
        self.edge_keys: Set[Tuple[str, str, str, Optional[int]]] = set()
        self.needs_recompile: List[str] = []
        self.not_expanded: List[str] = []
        self.visited: Set[Tuple[str, str]] = set()

        self.catalog_cache: Dict[str, Dict[str, List[ObjectCatalogRow]]] = {}
        self.plscope_cache: Dict[str, Dict[str, List[PlscopeCheckRow]]] = {}

        self.truncated = False
        self.truncation_reason: Optional[str] = None

        self.queue: Deque[Tuple[str, str, int, Optional[str]]] = deque()

        # Fase 4 (T-04): metadados do trigger (evento/status ENABLED|
        # DISABLED) a anotar no DepNode quando ele for criado
        # genericamente por run() -- ver _annotate_trigger.
        self._trigger_meta: Dict[Tuple[str, str], TriggerRow] = {}

        # T-04: repassado por from_result/to_result sem alteracao -- este
        # motor nao produz DYNAMIC_SQL (isso e T-03/depgraph_enrich.py),
        # so preserva o que ja veio no DepGraphResult de entrada.
        self.snippets: Dict[str, str] = {}

    @classmethod
    def from_result(
        cls,
        extractor: DepExtractor,
        result: DepGraphResult,
        stop_schemas: Sequence[str],
        max_objects: int,
        max_depth: int,
    ) -> "_DepGraphEngine":
        """Reidrata o estado do motor a partir de um `DepGraphResult` ja
        produzido (por `build_dep_graph`, possivelmente com arestas
        READ/WRITE/DYNAMIC_SQL ja mescladas por quem monta o pipeline a
        partir de `depgraph_enrich.py`, T-03) -- usado por
        `add_trigger_phase` para continuar a mesma BFS sem reprocessar os
        nos ja visitados nem perder truncamento/needs_recompile ja
        registrados."""
        engine = cls(extractor, stop_schemas, max_objects, max_depth)
        for node in result.nodes:
            key = (node.owner.upper(), node.object_name.upper())
            engine.nodes[key] = node
            engine.visited.add(key)
        for edge in result.edges:
            edge_key = (edge.from_ref, edge.to_ref, edge.edge_type, edge.line)
            if edge_key in engine.edge_keys:
                continue
            engine.edge_keys.add(edge_key)
            engine.edges.append(edge)
        engine.needs_recompile = list(result.needs_recompile)
        engine.not_expanded = list(result.not_expanded)
        engine.truncated = result.truncated
        engine.truncation_reason = result.truncation_reason
        engine.snippets = dict(result.snippets)
        return engine

    # ---- helpers portados de build_dep_graph (T-02), sem mudanca de logica ----

    def note_truncation(self, reason: str) -> None:
        self.truncated = True
        if self.truncation_reason is None:
            self.truncation_reason = reason

    def get_catalog(self, owner: str) -> Dict[str, List[ObjectCatalogRow]]:
        cache_key = owner.upper()
        if cache_key not in self.catalog_cache:
            rows = self.extractor.object_catalog(owner)
            by_name: Dict[str, List[ObjectCatalogRow]] = {}
            for row in rows:
                by_name.setdefault(row.object_name.upper(), []).append(row)
            self.catalog_cache[cache_key] = by_name
        return self.catalog_cache[cache_key]

    def get_plscope(self, owner: str) -> Dict[str, List[PlscopeCheckRow]]:
        cache_key = owner.upper()
        if cache_key not in self.plscope_cache:
            rows = self.extractor.plscope_check(owner)
            by_name: Dict[str, List[PlscopeCheckRow]] = {}
            for row in rows:
                by_name.setdefault(row.name.upper(), []).append(row)
            self.plscope_cache[cache_key] = by_name
        return self.plscope_cache[cache_key]

    def classify(
        self, owner: str, name: str, fallback_type: Optional[str]
    ) -> Tuple[str, str, Optional[Any]]:
        rows = self.get_catalog(owner).get(name.upper(), [])
        if not rows:
            return fallback_type or "UNKNOWN", "UNKNOWN", None
        # Ordem alfabetica do tipo e deterministica e, de quebra, prefere a
        # SPEC (`PACKAGE`, `TYPE`) sobre o BODY (`PACKAGE BODY`, `TYPE
        # BODY`) como rotulo do no agregado -- e a face publica do objeto.
        rows_sorted = sorted(rows, key=lambda r: r.object_type)
        object_type = rows_sorted[0].object_type
        statuses = {(r.status or "").upper() for r in rows}
        if "INVALID" in statuses:
            status = "INVALID"
        elif "VALID" in statuses:
            status = "VALID"
        else:
            status = rows_sorted[0].status
        # last_ddl_time (T-04): o mais recente entre as linhas agregadas
        # (SPEC+BODY) -- ordenacao lexicografica funciona pois o formato
        # devolvido por object_catalog.sql e "YYYY-MM-DD HH24:MI:SS".
        ddl_times = sorted(
            (str(r.last_ddl_time) for r in rows if r.last_ddl_time is not None)
        )
        last_ddl_time = ddl_times[-1] if ddl_times else None
        return object_type, status, last_ddl_time

    def has_plscope(self, owner: str, name: str, object_type: str) -> bool:
        if object_type not in PLSQL_OBJECT_TYPES:
            return False
        rows = self.get_plscope(owner).get(name.upper(), [])
        for row in rows:
            settings = (row.plscope_settings or "").upper()
            if "IDENTIFIERS:ALL" in settings:
                return True
        return False

    def add_edge(self, from_ref: str, to_ref: str, edge_type: str, line: Optional[int] = None, **kwargs) -> bool:
        # Chave inclui `line` (T-04 fix): duas arestas READ/WRITE/
        # DYNAMIC_SQL para o MESMO alvo mas em linhas diferentes sao fatos
        # distintos (dois INSERTs/EXECUTE IMMEDIATE separados) -- so CALL/
        # SYNONYM_RESOLVES_TO (line sempre None) dedupem como antes.
        edge_key = (from_ref, to_ref, edge_type, line)
        if edge_key in self.edge_keys:
            return False
        self.edge_keys.add(edge_key)
        self.edges.append(DepEdge(from_ref=from_ref, to_ref=to_ref, edge_type=edge_type, line=line, **kwargs))
        return True

    def enqueue(self, owner: str, name: str, depth: int, fallback_type: Optional[str]) -> None:
        enqueue_key = (owner.upper(), name.upper())
        if enqueue_key in self.visited:
            return
        self.visited.add(enqueue_key)
        self.queue.append((owner, name, depth, fallback_type))

    def _annotate_trigger(self, key: Tuple[str, str], node: DepNode) -> None:
        """T-04: se `key` corresponde a um trigger descoberto pela fase 4
        (`expand_triggers` populou `_trigger_meta`), anota status
        ENABLED/DISABLED no no recem-criado -- trigger desabilitado entra
        no grafo, marcado (plano, secao 4.4), nunca abortando a fase."""
        meta = self._trigger_meta.get(key)
        if meta is None:
            return
        trigger_status = (meta.status or "UNKNOWN").upper()
        node.trigger_status = trigger_status
        if trigger_status != "ENABLED":
            marker = "trigger_status:{}".format(trigger_status)
            node.note = marker if node.note is None else "{}; {}".format(node.note, marker)

    def run(self) -> None:
        """Processa a fila ate esvaziar -- mesmo laco de `build_dep_graph`
        (T-02), extraido para metodo reusavel. `add_trigger_phase` (T-04)
        semeia a fila via `expand_triggers` e chama este metodo de novo
        para expandir os triggers re-injetados (e qualquer dependencia
        nova que eles tragam)."""
        while self.queue:
            cur_owner, cur_name, depth, fallback_type = self.queue.popleft()
            key = (cur_owner.upper(), cur_name.upper())
            ref = "{}.{}".format(key[0], key[1])

            if key in self.nodes:
                # Defensivo: o visited-set em enqueue() ja evita duplicata,
                # mas a BFS nunca deve recriar/reprocessar um no existente.
                continue

            if len(self.nodes) >= self.max_objects:
                self.note_truncation("limite de max_objects={} atingido".format(self.max_objects))
                self.not_expanded.append(ref)
                continue

            is_frontier = key[0] in self.stop_schemas_u or key[1].startswith(("DBMS_", "UTL_"))

            if is_frontier:
                # Fronteira: entra como folha, mas nunca reconsultamos o
                # dicionario do schema de sistema (deps_direct/
                # object_catalog/plscope_check jamais sao chamados aqui).
                node = DepNode(
                    owner=key[0],
                    object_name=key[1],
                    object_type=fallback_type or "UNKNOWN",
                    status="UNKNOWN",
                    plscope=False,
                    is_leaf=True,
                )
                self._annotate_trigger(key, node)
                self.nodes[key] = node
                continue

            object_type, status, last_ddl_time = self.classify(cur_owner, cur_name, fallback_type)
            plscope = self.has_plscope(cur_owner, cur_name, object_type)
            node = DepNode(
                owner=key[0],
                object_name=key[1],
                object_type=object_type,
                status=status,
                plscope=plscope,
                is_leaf=False,
                last_ddl_time=last_ddl_time,
            )
            self._annotate_trigger(key, node)
            self.nodes[key] = node

            if object_type in PLSQL_OBJECT_TYPES and not plscope:
                self.needs_recompile.append(ref)

            if depth >= self.max_depth:
                self.note_truncation("limite de max_depth={} atingido em {}".format(self.max_depth, ref))
                self.not_expanded.append(ref)
                continue

            dep_rows = list(self.extractor.deps_direct(cur_owner, cur_name))
            dep_rows.sort(key=lambda r: (r.referenced_owner.upper(), r.referenced_name.upper()))

            outbound_count = 0
            for row in dep_rows:
                ref_owner_u, ref_name_u = row.referenced_owner.upper(), row.referenced_name.upper()
                if (ref_owner_u, ref_name_u) == key:
                    continue  # self-loop: BODY dependendo da propria SPEC (ou vice-versa)
                to_ref = "{}.{}".format(ref_owner_u, ref_name_u)
                if self.add_edge(ref, to_ref, "CALL", confidence="exact"):
                    outbound_count += 1
                self.enqueue(row.referenced_owner, row.referenced_name, depth + 1, row.referenced_type)

            if not dep_rows:
                syn_rows = self.extractor.synonym(cur_owner, cur_name)
                if syn_rows:
                    chain = resolve_synonym_chain(
                        lambda o, n: self.extractor.synonym(o, n), cur_owner, cur_name
                    )
                    prev_ref = ref
                    for hop in chain:
                        if hop.db_link:
                            link_owner = (hop.base_owner or "REMOTE").upper()
                            link_name = (hop.base_name or key[1]).upper()
                            link_ref = "{}.{}@{}".format(link_owner, link_name, hop.db_link)
                            link_key = ("__DBLINK__", link_ref)
                            if link_key not in self.nodes:
                                self.nodes[link_key] = DepNode(
                                    owner=link_owner,
                                    object_name=link_name,
                                    object_type="DATABASE LINK",
                                    status="UNKNOWN",
                                    plscope=False,
                                    is_leaf=True,
                                    note="db_link:{}".format(hop.db_link),
                                )
                            if self.add_edge(prev_ref, link_ref, "SYNONYM_RESOLVES_TO"):
                                outbound_count += 1
                            prev_ref = link_ref
                            break
                        if hop.base_owner and hop.base_name:
                            base_ref = "{}.{}".format(hop.base_owner.upper(), hop.base_name.upper())
                            if self.add_edge(prev_ref, base_ref, "SYNONYM_RESOLVES_TO"):
                                outbound_count += 1
                            self.enqueue(hop.base_owner, hop.base_name, depth + 1, None)
                            prev_ref = base_ref
                        else:
                            break

            if outbound_count == 0:
                node.is_leaf = True

    def expand_triggers(self) -> None:
        """Fase 4 (T-04, plano secao 4.4): para cada tabela que recebeu
        aresta WRITE (`edge_type == "WRITE"`, alvo determinavel), busca os
        triggers dela via `extractor.triggers` (agrupado por owner, mesmo
        padrao de bind `:table_list` da query real), cria uma aresta
        TRIGGER_FIRES da tabela para cada trigger (evento em `op`) e
        re-injeta tabela+trigger na fila do motor -- `run()` (chamado logo
        em seguida por `add_trigger_phase`) cria os nos genericamente
        (mesma classificacao/PL-Scope de qualquer outro no) e expande as
        dependencias proprias do trigger (trigger chama procedure / escreve
        em outra tabela).

        Anti-loop: reusa o mesmo visited-set de `enqueue` -- um trigger ja
        visitado (por exemplo, o mesmo trigger listado para duas tabelas)
        nao e re-enfileirado, so ganha uma aresta TRIGGER_FIRES adicional
        da outra tabela. Se o proprio trigger depender de volta da tabela
        que o disparou (comum: trigger le/escreve a propria tabela), o
        laco generico de `run()` so acrescenta uma aresta CALL extra
        (dedupicada por `add_edge`) -- a tabela ja esta em `visited`,
        entao `enqueue` no-opa e a BFS nao regride nem loopa."""
        write_targets = sorted(
            {
                e.to_ref
                for e in self.edges
                if e.edge_type == "WRITE" and e.to_ref and e.to_ref != _UNKNOWN_WRITE_TARGET
            }
        )
        by_owner: Dict[str, List[str]] = {}
        for ref in write_targets:
            owner, sep, name = ref.partition(".")
            if not sep:
                continue
            by_owner.setdefault(owner.upper(), []).append(name.upper())

        for owner in sorted(by_owner):
            table_names = sorted(set(by_owner[owner]))
            trigger_rows = sorted(
                self.extractor.triggers(owner, table_names),
                key=lambda r: (r.table_name.upper(), r.trigger_name.upper()),
            )
            for row in trigger_rows:
                table_key = (owner.upper(), row.table_name.upper())
                table_ref = "{}.{}".format(*table_key)
                trigger_key = (owner.upper(), row.trigger_name.upper())
                trigger_ref = "{}.{}".format(*trigger_key)

                # Garante que a tabela tambem vira no (caso a aresta WRITE
                # aponte para uma tabela que a BFS original nao alcancou
                # via ALL_DEPENDENCIES) -- no-op se ja visitada.
                self.enqueue(owner, row.table_name, 0, "TABLE")

                # Metadado registrado antes do enqueue do trigger: quando
                # run() criar o no do trigger (nesta chamada ou numa
                # seguinte), _annotate_trigger consulta este dicionario.
                self._trigger_meta.setdefault(trigger_key, row)
                self.enqueue(owner, row.trigger_name, 0, "TRIGGER")

                self.add_edge(
                    table_ref,
                    trigger_ref,
                    "TRIGGER_FIRES",
                    line=None,
                    op=(row.triggering_event or None),
                )

    def populate_table_columns(self) -> None:
        """Preenche `DepNode.columns` para todo no TABLE do grafo, via
        `extractor.tab_columns` (agrupado por owner). Decisao registrada
        na entrega do T-04: nao faz parte do texto literal da "fase 4"
        (triggers), mas e pre-requisito do `depgraph_render.py` (secao
        `## Colunas` do node .md, plano secao 5) -- sem isso o render nao
        teria de onde tirar colunas dos nos TABLE descobertos so pela fase
        4 (ex.: a tabela auditada por um trigger, alcancada via CALL
        generico dentro de `run()`, nunca via `deps_direct` da raiz)."""
        table_nodes = [n for n in self.nodes.values() if n.object_type == "TABLE"]
        if not table_nodes:
            return
        by_owner: Dict[str, List[DepNode]] = {}
        for n in table_nodes:
            by_owner.setdefault(n.owner.upper(), []).append(n)
        for owner in sorted(by_owner):
            nodes_for_owner = by_owner[owner]
            table_names = sorted({n.object_name.upper() for n in nodes_for_owner})
            cols = self.extractor.tab_columns(owner, table_names)
            by_table: Dict[str, List[TabColumnRow]] = {}
            for c in cols:
                by_table.setdefault(c.table_name.upper(), []).append(c)
            for n in nodes_for_owner:
                rows = by_table.get(n.object_name.upper())
                if rows:
                    n.columns = sorted(rows, key=lambda c: c.column_id)

    def to_result(self) -> DepGraphResult:
        stats = {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "needs_recompile": len(self.needs_recompile),
            "leaves": sum(1 for n in self.nodes.values() if n.is_leaf),
            "not_expanded": len(self.not_expanded),
            "max_objects": self.max_objects,
            "max_depth": self.max_depth,
        }

        sorted_nodes = sorted(self.nodes.values(), key=lambda n: (n.owner, n.object_name))
        sorted_edges = sorted(
            self.edges,
            key=lambda e: (e.from_ref, e.to_ref, e.edge_type, e.line if e.line is not None else -1),
        )

        return DepGraphResult(
            nodes=sorted_nodes,
            edges=sorted_edges,
            needs_recompile=sorted(set(self.needs_recompile)),
            truncated=self.truncated,
            truncation_reason=self.truncation_reason,
            not_expanded=sorted(set(self.not_expanded)),
            stats=stats,
            snippets=dict(self.snippets),
        )


def build_dep_graph(
    extractor: DepExtractor,
    root: RootLike,
    stop_schemas: Sequence[str] = ("SYS", "SYSTEM"),
    max_objects: int = 500,
    max_depth: int = 20,
) -> DepGraphResult:
    """Busca em largura sobre `ALL_DEPENDENCIES` (via `extractor.deps_direct`)
    a partir de `root`, visitando cada objeto uma unica vez.

    Regras (plano, secao 4.4; spec T-02):
    - visited-set chaveado por `(OWNER, NAME)` em maiusculas -- agrega
      PACKAGE + PACKAGE BODY (e SPEC/BODY de qualquer tipo) num no so, pois
      `deps_direct` ja devolve a uniao das duas por nao filtrar por tipo;
    - self-loop (aresta cujo destino e o proprio no de origem) e
      descartado;
    - arestas duplicadas (mesmo par `from/to/edge_type`) sao dedupicadas;
    - fronteira de parada: owner em `stop_schemas` OU nome comecando com
      `DBMS_`/`UTL_` -- o objeto entra no grafo como no folha
      (`is_leaf=True`) mas `deps_direct`/`plscope_check`/`object_catalog`
      NUNCA sao chamados para ele (nao reconsulta a fronteira);
    - sinonimo: quando `deps_direct` nao devolve nada para um no, tenta
      `extractor.synonym` + `resolve.resolve_synonym_chain`; cadeia
      terminando em db_link vira folha opaca (`is_leaf=True`, `note`
      marcado); cadeia resolvendo a objeto base gera aresta
      `SYNONYM_RESOLVES_TO` e o objeto base continua a BFS normalmente;
    - PL/Scope incremental: cada objeto de tipo PL/SQL (`PLSQL_OBJECT_TYPES`)
      e checado via `plscope_check`; sem PL/Scope, `plscope=False` mas o
      objeto NAO aborta a BFS -- so entra em `needs_recompile`;
    - limites `max_objects`/`max_depth`: ao serem atingidos, a BFS para de
      criar/expandir novos nos e registra cada objeto que ficou de fora em
      `not_expanded`, com `truncated=True` e `truncation_reason` explicando
      qual limite bateu primeiro -- nunca truncamento silencioso;
    - determinismo: as dependencias de cada no sao ordenadas por
      `(referenced_owner, referenced_name)` antes de entrar na fila, e as
      listas de saida (`nodes`, `edges`, `needs_recompile`, `not_expanded`)
      sao ordenadas de forma estavel -- duas chamadas com a mesma entrada
      produzem listas identicas.
    """
    owner0, name0 = _normalize_root(root)
    engine = _DepGraphEngine(extractor, stop_schemas, max_objects, max_depth)
    engine.enqueue(owner0, name0, 0, None)
    engine.run()
    return engine.to_result()


def add_trigger_phase(
    extractor: DepExtractor,
    result: DepGraphResult,
    stop_schemas: Sequence[str] = ("SYS", "SYSTEM"),
    max_objects: int = 500,
    max_depth: int = 20,
) -> DepGraphResult:
    """Fase 4 do plano (T-04, secao 4.4): triggers das tabelas escritas
    entram no grafo com suas proprias dependencias.

    Recebe um `DepGraphResult` ja existente -- tipicamente a saida de
    `build_dep_graph` com as arestas READ/WRITE/DYNAMIC_SQL de
    `depgraph_enrich.py` (T-03) ja mescladas por quem monta o pipeline
    (fora desta tarefa; `DepGraphResult.edges` e uma lista simples,
    mesclar e so concatenar+ordenar) -- e devolve um NOVO
    `DepGraphResult` estendido com:

    - um `DepNode` por trigger encontrado nas tabelas que receberam
      aresta WRITE, com `trigger_status` ENABLED|DISABLED (trigger
      desabilitado entra no grafo, marcado -- nunca abortando a fase);
    - uma aresta `TRIGGER_FIRES` da tabela para cada trigger dela, com o
      evento (`op`, ex.: 'INSERT') registrado;
    - a expansao das dependencias proprias de cada trigger (o trigger
      volta a fila da BFS -- mesmo motor/regras de `build_dep_graph`:
      dedup, self-loop, fronteira, sinonimo, PL-Scope incremental, caps);
    - `DepNode.columns` preenchido para todo no TABLE do grafo (ver
      `_DepGraphEngine.populate_table_columns` para a justificativa).

    `stop_schemas`/`max_objects`/`max_depth` devem normalmente repetir os
    mesmos valores passados a `build_dep_graph` para o `result` de
    entrada -- a fase 4 respeita os mesmos caps (plano, secao 4.4),
    contando a partir do numero de nos ja presentes em `result.nodes`.
    """
    engine = _DepGraphEngine.from_result(extractor, result, stop_schemas, max_objects, max_depth)
    engine.expand_triggers()
    engine.run()
    engine.populate_table_columns()
    return engine.to_result()
