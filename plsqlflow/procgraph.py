"""Motor de travessia de PROCESSO, grao SUBPROGRAMA (T-03, contrato
depgraph-granular).

Diferenca para os dois motores existentes (nenhum dos dois e alterado
aqui):
- `plsqlflow/graph.py` (congelado por golden test) tem no de subprograma,
  mas reconsidera TODAS as chamadas do OBJETO inteiro como candidatas de
  cada no expandido -- defeito declarado na propria docstring dele
  (linhas 9-20). E o defeito que este modulo existe para nao ter: aqui
  cada CALL/STMT so entra na expansao do subprograma exato que o
  compilador provou envolver (`plsqlflow/attribute.py`, T-01).
- `plsqlflow/depgraph.py` (`_DepGraphEngine`, tambem nao alterado aqui) tem
  grao OBJETO: um PACKAGE inteiro e um no so. Este modulo semeia a fila
  com SUBPROGRAMAS, nao objetos -- um package com 80 procedures das quais
  o processo alcanca 3 contribui 3 nos, nao 1.

Arquitetura (docs/plano-depgraph-granular.md, secao 4):

1. `ProcExtractor` e o Protocol que isola este motor de rede/banco -- mesmo
   padrao de `depgraph.DepExtractor`/`graph.Extractor`: no CLI real,
   implementado aplicando `extract.fetch_plscope_tree_batch`/
   `fetch_plscope_statements_batch` a uma conexao; nos testes, um fake
   alimentado por `tests/fixtures/procgraph_demo.json`.
2. Na primeira visita a um OBJETO (nao subprograma), `_load_object` puxa a
   arvore PL/Scope inteira dele (identifiers + statements, uma chamada
   cada) e roda `attribute.assign_context` uma unica vez -- cacheado em
   `_ProcGraphEngine._object_cache`, chaveado por (owner, object_name).
   Visitar outro subprograma do MESMO objeto nunca dispara nova leitura
   (prova: `tests/test_procgraph_bfs.py::test_cache_reads_object_once`).
3. Cada `Assignment` (T-01) cujo `enclosing` bate com o subprograma que
   esta sendo expandido vira aresta:
   - CALL resolvida por `attribute.resolve_call_target` -> aresta exata
     para `(owner, objeto, subprograma)` do alvo, que entra na fila.
   - CALL nao resolvida (T-01 nunca inventa destino) -> ver "Resolucao
     inter-package" abaixo.
   - STMT -> repassada ao seam de T-05 (`_expand_access`, ver secao
     "Seam de acesso" abaixo); T-03 so entrega o ponto de chamada certo
     (statement ja atribuido ao subprograma exato) e um stub que devolve
     lista vazia.

Resolucao inter-package (decisao de projeto NAO especificada letra-a-letra
no plano, registrada aqui): `attribute.resolve_call_target` resolve por um
indice CONSTRUIDO SO A PARTIR DOS OBJETOS JA LIDOS pela travessia (e assim
que T-01 foi desenhado -- modulo puro, sem banco, o indice e o que o
chamador the entrega). Isso resolve de graca chamada DENTRO do mesmo
objeto (ex.: MAIN chamando PROC_A no mesmo FLOW_DEMO, ja indexado no
momento em que FLOW_DEMO e carregado) mas cria um problema de ovo-e-galinha
para chamada ENTRE objetos: a primeira vez que PKG_A.P1 chama PKG_B.P2,
PKG_B ainda nao foi lido, entao a signature de P2 nao esta em nenhum
indice -- e sem ler PKG_B nao ha como descobrir que ele existe, mas sem
saber que ele existe nao ha razao para le-lo.

A saida (o que faz "a resolucao por signature atravessar objeto" ser
verdade na pratica, nao so em tese) e um metodo OPCIONAL do Protocol,
`resolve_owner(signature) -> Optional[Tuple[owner, object_name]]`: uma
consulta PONTUAL (equivalente a `SELECT owner, object_name FROM
all_identifiers WHERE signature = :sig AND usage IN ('DECLARATION',
'DEFINITION')`) que devolve SO o owner/objeto dono daquela signature, sem
trazer a arvore inteira dele. Quando uma CALL fica `UnresolvedCall`, o
motor consulta `resolve_owner`; se ela aponta para um objeto, esse objeto
e carregado (mesmo caminho de `_load_object`, com cache -- se outro
subprograma dele ja tiver sido processado antes, no-op) e a resolucao e
tentada de novo, agora com o indice global atualizado. Isso mantem a
garantia de "so entra no grafo o que o processo alcanca" (nunca
pre-carregamos um objeto por especulacao -- so quando uma CALL REAL,
comprovada pelo compilador via signature, aponta para ele) e evita
pre-varrer o schema inteiro. Quando `resolve_owner` nao existe no
extractor (Protocol OPCIONAL, mesmo padrao de `deps_direct_batch` em
`depgraph.DepExtractor`) ou devolve None, a CALL vira um no NAO-RESOLVIDO
declarado (nunca omitida -- ver `_ensure_unresolved_node`).

Seam de acesso (T-05, item 6 do contrato): `plsqlflow/procgraph_access.py`
ainda nao existe (e entrega de T-05). Import PROTEGIDO (try/except
ImportError no topo deste modulo) em vez de um parametro injetavel em
`build_proc_graph`: T-05 e um modulo IRMAO fixo do pacote, nao um plugin
escolhido em runtime por quem chama -- ninguem que chama `build_proc_graph`
hoje precisa saber que o seam existe, e no dia em que `procgraph_access.py`
for criado com uma funcao `expand_access(...)`, o import passa a resolver
sozinho, sem tocar em nenhum chamador. Ate la, `_expand_access` cai no
stub local (`_stub_expand_access`), que devolve sempre `[]`.

`__INIT__` do package (item 6 do contrato): o bloco de inicializacao de um
PACKAGE BODY/TYPE BODY roda na primeira chamada a QUALQUER subprograma
dele, entao faz parte do processo. Decisao de timing (nao especificada
letra a letra): o enqueue de `__INIT__` acontece no PROCESSAMENTO (nao no
enqueue) do primeiro subprograma daquele objeto a ser desenfileirado --
e o primeiro momento em que o motor SABE se o objeto e um BODY (so aí a
arvore PL/Scope foi lida) e portanto se `__INIT__` se aplica. Rastreado em
`_ProcGraphEngine._init_enqueued` (chaveado por objeto, marcado uma unica
vez), e o proprio `__INIT__` entra pela mesma `_enqueue_subprogram` que
qualquer outro subprograma -- sujeito ao mesmo visited-set.

TERMINACAO (requisito duro, docs/plano-depgraph-granular.md, secao
"Protecao contra recursao infinita"): a UNICA garantia que sustenta a
travessia terminar mesmo com ciclo (recursao direta, mutua no mesmo
package, ciclo atravessando N packages) e o visited-set global
(`_ProcGraphEngine.visited`, chaveado por (OWNER, OBJETO, SUBPROGRAMA)
maiusculo) marcado no ENQUEUE (`_enqueue_subprogram`), nunca no
processamento. Reencontrar um no ja visitado so gera a aresta (o ciclo
fica visivel no grafo) -- `_enqueue_subprogram` nao-opa sem re-enfileirar.
Como cada iteracao do laco de `run()` consome exatamente um item da fila e
todo item novo nasce SO de um no nunca antes visitado, a fila esvazia em
no maximo |nos alcancaveis| passos. PROIBIDO (e nao usado aqui) qualquer
cap de profundidade/timeout/contador como mecanismo de terminacao -- os
caps `max_objects`/`max_depth` sao opt-in de USUARIO (default None = sem
limite) e, quando acionados, so truncam a EXPANSAO de um no (o no ainda
existe, so nao ganha filhos), nunca mudam a logica de terminacao.

Determinismo: nenhuma lista de saida depende de ordem de iteracao de
set/dict -- `to_result()` ordena nos e arestas antes de devolver, mesmo
padrao de `depgraph.py::_DepGraphEngine.to_result`.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import (
    Any,
    Deque,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Union,
)

from .attribute import (
    Assignment,
    DefinitionEntry,
    IdentifierRow,
    ResolvedCall,
    StatementRow,
    UnresolvedCall,
    assign_context,
    extract_definitions,
    resolve_call_target,
)

# Import protegido do seam de T-05 (ver docstring do modulo, secao "Seam de
# acesso"). O modulo `procgraph_access` e entrega de T-05 -- ate ele
# existir (ou se, por qualquer motivo, nao expuser `expand_access`), o
# motor cai no stub local `_stub_expand_access`.
try:  # pragma: no cover - normal ate T-05 landar
    from . import procgraph_access as _procgraph_access
except ImportError:  # pragma: no cover - normal ate T-05 landar
    _procgraph_access = None  # type: ignore[assignment]


# Object types cujo BODY tem bloco de inicializacao executavel (roda na
# primeira chamada a qualquer subprograma do objeto) -- mesmo criterio
# documentado em attribute.py (`_SPEC_OBJECT_TYPES`/`_synthetic_root`), so
# que aqui a pergunta e "este object_type de BODY precisa de no __INIT__
# proprio na fila?", nao "qual sintetico usar para um no orfao dentro da
# arvore". Duplicado deliberadamente (poucas linhas, semantica levemente
# diferente) em vez de importar o simbolo privado `_synthetic_root` de
# attribute.py.
_INIT_CAPABLE_BODY_TYPES = {"PACKAGE BODY", "TYPE BODY"}

# Object types de spec (sem corpo executavel proprio) -- usados para achar
# a arvore de declaracoes publicas na semeadura de raiz de 2 partes.
_SPEC_TYPES = {"PACKAGE", "TYPE"}

# Object types de subprograma standalone (nao embrulhado em package/type) --
# a "arvore" dele e a propria DEFINITION de raiz (usage_context_id=0).
_STANDALONE_TYPES = {"PROCEDURE", "FUNCTION", "TRIGGER"}


def _stub_expand_access(**kwargs: Any) -> List["ProcEdge"]:
    """Stub local do seam de T-05 (ver docstring do modulo). T-03 so
    entrega o PONTO de chamada certo (statement ja atribuido ao
    subprograma exato) e este stub -- a implementacao real (READ/WRITE
    por coluna do compilador, estado de package, trigger como
    subprograma) e T-05. Devolve sempre lista vazia: nenhum statement gera
    aresta ate T-05 existir, mas o laco que chama isto (`_process_stmt`)
    ja esta no lugar certo para quando existir."""
    return []


def _expand_access(**kwargs: Any) -> List["ProcEdge"]:
    if _procgraph_access is not None and hasattr(_procgraph_access, "expand_access"):
        return _procgraph_access.expand_access(**kwargs)  # type: ignore[no-any-return]
    return _stub_expand_access(**kwargs)


# --------------------------------------------------------------------------
# Modelo de dados (espelha DepNode/DepEdge/DepGraphResult de depgraph.py)
# --------------------------------------------------------------------------


@dataclass
class ProcNode:
    """Um subprograma (ou no sintetico `__INIT__`/`__SPEC__`, ou no
    NAO-RESOLVIDO/externo) do grafo de processo.

    `grain` e "subprogram" para todo no normal deste motor; T-04 usa
    "object" para o fallback (objeto sem PL/Scope utilizavel expandido em
    grao objeto inteiro -- ver `plsqlflow.depgraph._DepGraphEngine`
    reusado la); este modulo so MARCA o ponto de decisao (ver
    `_process_subprogram`, ramo `not cache.has_identifiers`) sem
    implementar o fallback. `grain="unresolved"` e deste modulo: CALL cuja
    signature nao resolveu (ver docstring, secao "Resolucao
    inter-package") -- nunca omitida, sempre um no declarado com o motivo
    em `note`.

    `plscope_identifiers`/`plscope_statements` espelham os campos de
    mesmo nome em `depgraph.DepNode` (T-06): as duas capacidades
    reportadas SEPARADAS, porque um objeto so com IDENTIFIERS (sem
    STATEMENTS) ainda permite atribuir CALL corretamente -- so os
    acessos a tabela/SQL dinamico dele (via STMT) viram ponto cego, sem
    rebaixar o objeto inteiro para grao objeto (regra 7 do contrato)."""

    owner: str
    object_name: str
    subprogram: str
    grain: str = "subprogram"
    plscope_identifiers: bool = False
    plscope_statements: bool = False
    is_leaf: bool = False
    note: Optional[str] = None


@dataclass
class ProcEdge:
    """Uma aresta do grafo de processo. `from_ref`/`to_ref` sao
    `OWNER.OBJETO.SUBPROGRAMA` (mesmo formato do `ref` usado como chave em
    `ProcNode`). `resolved=False` marca aresta para um no NAO-RESOLVIDO
    (`reason` sempre preenchido nesse caso -- nunca aresta omitida por
    falta de resolucao, regra dura do contrato)."""

    from_ref: str
    to_ref: str
    edge_type: str  # "CALL" (T-03); STMT_* fica para o seam de T-05
    line: Optional[int]
    signature: Optional[str] = None
    resolved: bool = True
    reason: Optional[str] = None
    confidence: str = "exact"


@dataclass
class ProcGraphResult:
    """Espelha `depgraph.DepGraphResult` (mesmo contrato de
    truncamento/nao-omissao): `truncated` so e True quando
    `truncation_reason` existe e `not_expanded` nao esta vazio -- nunca
    corte silencioso. `blind_spots` (novo aqui, sem equivalente em
    `DepGraphResult`) enumera toda CALL que nao pode ser atribuida a
    nenhum destino conhecido (alvo fora do escopo visivel, ex.:
    SYS.STANDARD) -- distinto de `not_expanded`, que e sobre caps de
    usuario, nao sobre alvo desconhecido."""

    nodes: List[ProcNode]
    edges: List[ProcEdge]
    truncated: bool
    truncation_reason: Optional[str]
    not_expanded: List[str]
    blind_spots: List[str]
    stats: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProcRoot:
    """Raiz da travessia. `subprogram=None` (raiz de 2 partes,
    `owner.objeto`) semeia TODOS os subprogramas publicos da spec;
    `subprogram` preenchido (raiz de 3 partes, `owner.objeto.subprograma`)
    semeia so aquele subprograma exato."""

    owner: str
    object_name: str
    subprogram: Optional[str] = None


RootLike = Union[Tuple[str, str], Tuple[str, str, str], ProcRoot]


def _normalize_root(root: RootLike) -> Tuple[str, str, Optional[str]]:
    if isinstance(root, (tuple, list)):
        if len(root) == 2:
            return str(root[0]), str(root[1]), None
        if len(root) == 3:
            return str(root[0]), str(root[1]), str(root[2])
        raise ValueError(
            "root tupla deve ter 2 (owner, objeto) ou 3 (owner, objeto, subprograma) "
            "elementos, recebeu {!r}".format(root)
        )
    owner = getattr(root, "owner", None)
    object_name = getattr(root, "object_name", None)
    subprogram = getattr(root, "subprogram", None)
    if owner is None or object_name is None:
        raise TypeError(
            "root deve ser tupla (owner, objeto[, subprograma]) ou objeto com "
            "atributos .owner/.object_name[/.subprogram] (ex.: ProcRoot); "
            "recebeu {!r}".format(root)
        )
    return owner, object_name, subprogram


# --------------------------------------------------------------------------
# Protocol de extractor
# --------------------------------------------------------------------------


class ProcExtractor(Protocol):
    """Fonte de dados consumida pela BFS de subprogramas.

    Mesmo padrao de `depgraph.DepExtractor`/`graph.Extractor`: no CLI real
    e implementado aplicando `extract.fetch_plscope_tree_batch`/
    `fetch_plscope_statements_batch` a uma conexao (com `object_list` de
    um unico nome -- este motor pede objeto a objeto, na medida em que a
    travessia os descobre, nunca em lote adiantado: o proximo objeto so e
    conhecido depois que uma CALL real aponta pra ele); nos testes e um
    fake alimentado por `tests/fixtures/procgraph_demo.json`.
    `build_proc_graph` nunca toca rede/banco diretamente.

    As linhas devolvidas por `plscope_identifiers`/`plscope_statements`
    tem o mesmo formato de `extract.PlscopeTreeRow`/
    `extract.PlscopeStatementBatchRow` (duck-typed: qualquer objeto com os
    atributos usados aqui serve, sem precisar ser exatamente a dataclass
    de extract.py) -- podem conter linhas de MAIS de um object_type para o
    mesmo object_name (ex.: PACKAGE e PACKAGE BODY compartilham nome mas
    sao arvores separadas, mesma ressalva de `plscope_tree_batch.sql`); o
    motor agrupa por `row.object_type` antes de montar as arvores.

    `resolve_owner` e OPCIONAL (mesmo padrao de `deps_direct_batch` em
    `depgraph.DepExtractor`: presenca checada via `hasattr` em runtime) --
    ver docstring do modulo, secao "Resolucao inter-package", para o
    motivo de existir e a garantia que ele fecha."""

    def plscope_identifiers(self, owner: str, object_name: str) -> Sequence[Any]: ...

    def plscope_statements(self, owner: str, object_name: str) -> Sequence[Any]: ...

    def resolve_owner(self, signature: str) -> Optional[Tuple[str, str]]: ...


# --------------------------------------------------------------------------
# Cache por objeto
# --------------------------------------------------------------------------


@dataclass
class _ObjectCache:
    owner: str
    object_name: str
    body_type: Optional[str]
    spec_type: Optional[str]
    body_identifiers: List[IdentifierRow]
    body_statements: List[StatementRow]
    assignments_by_enclosing: Dict[str, List[Assignment]]
    definitions: List[DefinitionEntry]
    public_subprograms: List[str]
    has_identifiers: bool
    has_statements: bool


def _rows_to_identifiers(rows: Sequence[Any]) -> List[IdentifierRow]:
    return [
        IdentifierRow(
            usage_id=row.usage_id,
            usage_context_id=row.usage_context_id if row.usage_context_id is not None else 0,
            line=row.line,
            col=row.col,
            name=row.name,
            type=row.type,
            usage=row.usage,
            signature=row.signature,
        )
        for row in rows
    ]


def _rows_to_statements(rows: Sequence[Any]) -> List[StatementRow]:
    return [
        StatementRow(
            usage_id=row.usage_id,
            usage_context_id=row.usage_context_id if row.usage_context_id is not None else 0,
            line=row.line,
            stmt_type=row.stmt_type,
        )
        for row in rows
    ]


def _resolve_public_targets(
    spec_identifiers: Sequence[IdentifierRow],
    body_identifiers: Sequence[IdentifierRow],
) -> List[str]:
    """Nomes de subprograma PUBLICOS (declarados na SPEC, top-level) com
    sufixo de overload resolvido contra as DEFINITIONs do BODY -- usado
    para semear a fila quando a raiz e um objeto de 2 partes
    (`owner.objeto`, plano secao 4.2: "enfileira TODOS os subprogramas
    publicos da spec"). A SPEC diz o que e API publica; o BODY e quem tem
    a posicao real de overload (regra 3 de attribute.py, replicada aqui em
    miniatura -- `attribute._overload_positions` e privada do modulo e
    opera sobre TODAS as DEFINITIONs do objeto, nao so as top-level
    publicas; duplicar as poucas linhas custa menos que expor uma funcao
    nova so para este cruzamento spec/body). Determinismo: nomes e
    posicoes ordenados antes de devolver."""
    if not spec_identifiers:
        return []
    root_id = next((r.usage_id for r in spec_identifiers if r.usage_context_id == 0), None)
    if root_id is None:
        return []

    public_names: Set[str] = set()
    for row in spec_identifiers:
        if (
            row.usage_context_id == root_id
            and row.usage in ("DECLARATION", "DEFINITION")
            and row.type in ("PROCEDURE", "FUNCTION")
        ):
            public_names.add(row.name)
    if not public_names:
        return []

    body_root_id = next((r.usage_id for r in body_identifiers if r.usage_context_id == 0), None)
    if body_root_id is None:
        # Sem BODY para cruzar posicao de overload -- devolve nomes crus,
        # sem sufixo. Objeto sem BODY utilizavel e o ponto de decisao do
        # fallback de T-04 (mesmo criterio de `has_identifiers` em
        # `_process_subprogram`); aqui so evitamos quebrar a semeadura.
        return sorted(public_names)

    by_name: Dict[str, List[IdentifierRow]] = {}
    for row in body_identifiers:
        if (
            row.usage_context_id == body_root_id
            and row.usage == "DEFINITION"
            and row.type in ("PROCEDURE", "FUNCTION")
            and row.name in public_names
        ):
            by_name.setdefault(row.name, []).append(row)

    resolved: List[str] = []
    for name in sorted(by_name):
        rows = sorted(by_name[name], key=lambda r: (r.line, r.col, r.usage_id))
        if len(rows) == 1:
            resolved.append(name)
        else:
            for position in range(1, len(rows) + 1):
                resolved.append("{}#{}".format(name, position))
    return resolved


# --------------------------------------------------------------------------
# Motor
# --------------------------------------------------------------------------


class _ProcGraphEngine:
    def __init__(
        self,
        extractor: ProcExtractor,
        max_objects: Optional[int],
        max_depth: Optional[int],
    ) -> None:
        self.extractor = extractor
        self.max_objects = max_objects
        self.max_depth = max_depth

        # TERMINACAO: visited marcado no ENQUEUE (nunca no processamento) --
        # ver docstring do modulo. Chave = (OWNER, OBJETO, SUBPROGRAMA)
        # maiusculo.
        self.visited: Set[Tuple[str, str, str]] = set()
        self.queue: Deque[Tuple[str, str, str, int]] = deque()

        self.nodes: Dict[str, ProcNode] = {}
        self.edges: List[ProcEdge] = []
        self._edge_keys: Set[Tuple[str, str, str, Optional[int]]] = set()
        self.not_expanded: List[str] = []
        self.blind_spots: List[str] = []

        self.truncated = False
        self.truncation_reason: Optional[str] = None

        self._object_cache: Dict[Tuple[str, str], _ObjectCache] = {}
        self._definition_index: Dict[str, DefinitionEntry] = {}
        self._init_enqueued: Set[Tuple[str, str]] = set()

    # ---- utilitarios de baixo nivel (mesmo padrao de depgraph.py) ----

    def note_truncation(self, reason: str) -> None:
        self.truncated = True
        if self.truncation_reason is None:
            self.truncation_reason = reason

    def _add_edge(
        self,
        from_ref: str,
        to_ref: str,
        edge_type: str,
        line: Optional[int],
        **kwargs: Any,
    ) -> bool:
        edge_key = (from_ref, to_ref, edge_type, line)
        if edge_key in self._edge_keys:
            return False
        self._edge_keys.add(edge_key)
        self.edges.append(
            ProcEdge(from_ref=from_ref, to_ref=to_ref, edge_type=edge_type, line=line, **kwargs)
        )
        return True

    def _enqueue_subprogram(self, owner: str, object_name: str, subprogram: str, depth: int) -> None:
        key = (owner.upper(), object_name.upper(), subprogram)
        if key in self.visited:
            return
        self.visited.add(key)
        self.queue.append((owner, object_name, subprogram, depth))

    @staticmethod
    def _ref(owner: str, object_name: str, subprogram: str) -> str:
        return "{}.{}.{}".format(owner.upper(), object_name.upper(), subprogram)

    # ---- carga de objeto (cacheada, uma leitura por objeto na vida da BFS) ----

    def _load_object(self, owner: str, object_name: str) -> Optional[_ObjectCache]:
        key = (owner.upper(), object_name.upper())
        cached = self._object_cache.get(key)
        if cached is not None:
            return cached

        if self.max_objects is not None and len(self._object_cache) >= self.max_objects:
            # Cap opt-in de usuario (nunca mecanismo de terminacao -- ver
            # docstring do modulo). Nao cacheamos a falha: uma nova
            # tentativa de carregar o MESMO objeto so refaz esta checagem
            # barata, nunca uma nova leitura de rede/banco (o extractor
            # nunca chega a ser chamado).
            return None

        id_rows_raw = list(self.extractor.plscope_identifiers(owner, object_name))
        stmt_rows_raw = list(self.extractor.plscope_statements(owner, object_name))

        by_type_ids: Dict[str, List[Any]] = {}
        for row in id_rows_raw:
            obj_type = (getattr(row, "object_type", None) or "").upper()
            by_type_ids.setdefault(obj_type, []).append(row)

        by_type_stmts: Dict[str, List[Any]] = {}
        for row in stmt_rows_raw:
            obj_type = (getattr(row, "object_type", None) or "").upper()
            by_type_stmts.setdefault(obj_type, []).append(row)

        body_candidates = sorted(
            t for t in by_type_ids if t in _INIT_CAPABLE_BODY_TYPES or t in _STANDALONE_TYPES
        )
        body_type = body_candidates[0] if body_candidates else None
        spec_candidates = sorted(t for t in by_type_ids if t in _SPEC_TYPES)
        spec_type = spec_candidates[0] if spec_candidates else None

        body_ids_raw = by_type_ids.get(body_type, []) if body_type else []
        body_stmts_raw = by_type_stmts.get(body_type, []) if body_type else []
        body_identifiers = _rows_to_identifiers(body_ids_raw)
        body_statements = _rows_to_statements(body_stmts_raw)

        assignments = (
            assign_context(body_identifiers, body_statements, body_type) if body_type else []
        )
        assignments_by_enclosing: Dict[str, List[Assignment]] = {}
        for a in assignments:
            assignments_by_enclosing.setdefault(a.enclosing, []).append(a)

        definitions = (
            extract_definitions(body_identifiers, owner, object_name) if body_identifiers else []
        )
        for entry in definitions:
            # Signature e globalmente unica por construcao do PL/Scope --
            # sobrescrever e inofensivo (nunca deveria colidir com dado
            # real; ver build_definition_index em attribute.py, mesma
            # premissa).
            self._definition_index[entry.signature] = entry

        spec_ids_raw = by_type_ids.get(spec_type, []) if spec_type else []
        spec_identifiers = _rows_to_identifiers(spec_ids_raw)
        if spec_identifiers:
            public_subprograms = _resolve_public_targets(spec_identifiers, body_identifiers)
        elif body_type in _STANDALONE_TYPES and body_identifiers:
            # Objeto standalone (sem embrulho de package/type): a propria
            # raiz da arvore (usage_context_id=0) e o "subprograma publico"
            # unico -- nao ha overload possivel num objeto standalone.
            root_def = next(
                (r for r in body_identifiers if r.usage_context_id == 0 and r.usage == "DEFINITION"),
                None,
            )
            public_subprograms = [root_def.name] if root_def else []
        else:
            public_subprograms = []

        cache = _ObjectCache(
            owner=owner.upper(),
            object_name=object_name.upper(),
            body_type=body_type,
            spec_type=spec_type,
            body_identifiers=body_identifiers,
            body_statements=body_statements,
            assignments_by_enclosing=assignments_by_enclosing,
            definitions=definitions,
            public_subprograms=public_subprograms,
            has_identifiers=bool(body_identifiers),
            has_statements=bool(body_statements),
        )
        self._object_cache[key] = cache
        return cache

    # ---- resolucao de CALL, incluindo o salto inter-package ----

    def _synthetic_call_row(self, assignment: Assignment) -> IdentifierRow:
        # resolve_call_target (attribute.py) so le .signature/.name -- este
        # shim evita reconstruir/guardar um id_index completo so para
        # reobter a linha original (o Assignment ja carrega tudo que a
        # funcao precisa).
        return IdentifierRow(
            usage_id=assignment.usage_id,
            usage_context_id=0,
            line=assignment.line,
            col=0,
            name=assignment.target,
            type="",
            usage="CALL",
            signature=assignment.signature,
        )

    def _unresolved_ref(
        self, called_name: str, signature: Optional[str], owner_hint: Optional[Tuple[str, str]]
    ) -> str:
        if owner_hint is not None:
            return "{}.{}.__UNRESOLVED__".format(owner_hint[0].upper(), owner_hint[1].upper())
        return "__EXTERNAL__.{}.{}".format(called_name.upper(), signature or "NOSIG")

    def _ensure_unresolved_node(
        self,
        ref: str,
        called_name: str,
        owner_hint: Optional[Tuple[str, str]],
        reason: str,
    ) -> None:
        if ref in self.nodes:
            return
        if owner_hint is not None:
            owner, object_name = owner_hint[0].upper(), owner_hint[1].upper()
            subprogram = "__UNRESOLVED__"
        else:
            owner, object_name = "UNKNOWN", "UNKNOWN"
            subprogram = called_name
        self.nodes[ref] = ProcNode(
            owner=owner,
            object_name=object_name,
            subprogram=subprogram,
            grain="unresolved",
            is_leaf=True,
            note=reason,
        )

    def _process_call(self, from_ref: str, assignment: Assignment, depth: int) -> None:
        call_row = self._synthetic_call_row(assignment)
        resolved = resolve_call_target(call_row, self._definition_index)
        owner_hint: Optional[Tuple[str, str]] = None
        cap_blocked = False

        if isinstance(resolved, UnresolvedCall) and assignment.signature and hasattr(
            self.extractor, "resolve_owner"
        ):
            owner_hint = self.extractor.resolve_owner(assignment.signature)
            if owner_hint is not None:
                loaded = self._load_object(owner_hint[0], owner_hint[1])
                if loaded is None:
                    # Objeto CONHECIDO (resolve_owner achou) mas o cap de
                    # max_objects impede carrega-lo -- distinto do "alvo
                    # fora do escopo": aqui e truncamento de LIMITE DE
                    # USUARIO, entao entra em not_expanded item a item
                    # (contrato T-08), nao so em blind_spots.
                    cap_blocked = True
                    self.note_truncation(
                        "limite de max_objects={} atingido ao resolver CALL de {}".format(
                            self.max_objects, from_ref
                        )
                    )
                    self.not_expanded.append(
                        "{}.{}".format(owner_hint[0].upper(), owner_hint[1].upper())
                    )
                else:
                    resolved = resolve_call_target(call_row, self._definition_index)

        if isinstance(resolved, ResolvedCall):
            to_key_owner = resolved.owner.upper()
            to_key_object = resolved.object_name.upper()
            to_ref = self._ref(to_key_owner, to_key_object, resolved.subprogram)
            self._add_edge(
                from_ref,
                to_ref,
                "CALL",
                assignment.line,
                signature=resolved.signature,
                resolved=True,
            )
            self._enqueue_subprogram(to_key_owner, to_key_object, resolved.subprogram, depth + 1)
            return

        # UnresolvedCall definitivo: nunca omitida (regra dura do
        # contrato) -- vira aresta para um no NAO-RESOLVIDO declarado, com
        # o motivo. Ponto cego registrado a parte (`blind_spots`) para
        # quem monta a secao COBERTURA (T-08) poder contar -- exceto no
        # caso de cap (ja registrado em not_expanded acima, nao duplicamos
        # em blind_spots: nao e um alvo desconhecido, e um corte
        # declarado).
        assert isinstance(resolved, UnresolvedCall)
        if cap_blocked:
            reason = "objeto {}.{} conhecido mas nao carregado -- limite de max_objects atingido".format(
                owner_hint[0].upper(), owner_hint[1].upper()
            )
        elif owner_hint is not None:
            reason = "objeto {}.{} carregado mas a signature nao apareceu nas DEFINITIONs dele".format(
                owner_hint[0].upper(), owner_hint[1].upper()
            )
        elif assignment.signature is None:
            reason = "CALL sem signature (destino indeterminavel estaticamente)"
        else:
            reason = "alvo fora do escopo visivel (ex.: SYS.STANDARD) ou sem PL/Scope"

        unresolved_ref = self._unresolved_ref(assignment.target, assignment.signature, owner_hint)
        self._ensure_unresolved_node(unresolved_ref, assignment.target, owner_hint, reason)
        self._add_edge(
            from_ref,
            unresolved_ref,
            "CALL",
            assignment.line,
            signature=assignment.signature,
            resolved=False,
            reason=reason,
        )
        if not cap_blocked:
            self.blind_spots.append("{} -> {} ({})".format(from_ref, assignment.target, reason))

    def _process_stmt(self, owner: str, object_name: str, subprogram: str, cache: _ObjectCache, assignment: Assignment) -> None:
        # Seam de T-05 (ver docstring do modulo) -- statement ja atribuido
        # ao subprograma EXATO (nunca ao objeto inteiro, diferenca chave
        # para graph.py). Stub devolve [] ate T-05 existir.
        edges = _expand_access(
            owner=owner,
            object_name=object_name,
            subprogram=subprogram,
            statement=assignment,
            object_cache=cache,
        )
        for edge in edges or []:
            if isinstance(edge, ProcEdge):
                self._add_edge(
                    edge.from_ref,
                    edge.to_ref,
                    edge.edge_type,
                    edge.line,
                    signature=edge.signature,
                    resolved=edge.resolved,
                    reason=edge.reason,
                    confidence=edge.confidence,
                )

    # ---- processamento de um subprograma desenfileirado ----

    def _process_subprogram(self, owner: str, object_name: str, subprogram: str, depth: int) -> None:
        ref = self._ref(owner, object_name, subprogram)
        if ref in self.nodes:
            # Defensivo (mesmo padrao de _DepGraphEngine._process_object):
            # o visited-set do enqueue ja evita duplicata.
            return

        cache = self._load_object(owner, object_name)
        if cache is None:
            self.note_truncation(
                "limite de max_objects={} atingido antes de carregar {}.{}".format(
                    self.max_objects, owner.upper(), object_name.upper()
                )
            )
            self.not_expanded.append(ref)
            return

        node = ProcNode(
            owner=owner.upper(),
            object_name=object_name.upper(),
            subprogram=subprogram,
            plscope_identifiers=cache.has_identifiers,
            plscope_statements=cache.has_statements,
        )
        self.nodes[ref] = node

        if not cache.has_identifiers:
            # PONTO DE DECISAO para T-04 (regra 7 do contrato): objeto sem
            # identifiers utilizaveis torna a atribuicao por subprograma
            # impossivel -- este e exatamente o caso que o fallback de
            # grao objeto (T-04, reusando depgraph._DepGraphEngine com o
            # MESMO visited-set desta travessia) precisa tratar. T-03 nao
            # implementa o fallback: so marca o no e para aqui, sem
            # inventar dado nem quebrar a travessia (o resto do grafo
            # alcancado por OUTROS caminhos continua normalmente).
            node.grain = "object"
            node.is_leaf = True
            node.note = "sem PL/Scope identifiers -- fallback grao objeto (T-04, nao implementado aqui)"
            self.blind_spots.append("{} (sem PL/Scope identifiers)".format(ref))
            return

        # __INIT__ entra na fila junto com o PRIMEIRO subprograma
        # alcancado deste objeto (ver docstring do modulo, secao
        # "__INIT__ do package").
        obj_key = (owner.upper(), object_name.upper())
        if obj_key not in self._init_enqueued:
            self._init_enqueued.add(obj_key)
            if cache.body_type in _INIT_CAPABLE_BODY_TYPES and subprogram != "__INIT__":
                self._enqueue_subprogram(owner, object_name, "__INIT__", depth)

        if self.max_depth is not None and depth >= self.max_depth:
            self.note_truncation(
                "limite de max_depth={} atingido em {}".format(self.max_depth, ref)
            )
            self.not_expanded.append(ref)
            return

        assignments = sorted(
            cache.assignments_by_enclosing.get(subprogram, []), key=lambda a: a.usage_id
        )
        outbound = 0
        for assignment in assignments:
            if assignment.kind == "CALL":
                before = len(self.edges)
                self._process_call(ref, assignment, depth)
                if len(self.edges) > before:
                    outbound += 1
            else:  # STMT -- seam de T-05
                self._process_stmt(owner, object_name, subprogram, cache, assignment)

        node.is_leaf = outbound == 0

    def run(self) -> None:
        # FIFO simples: cada iteracao consome exatamente um item -- e a
        # invariante que a prova de terminacao (docstring do modulo)
        # depende. Sem batching por nivel (diferente de
        # `_DepGraphEngine.run`): o proximo objeto so e conhecido depois
        # que uma CALL real dentro do objeto CORRENTE aponta pra ele, entao
        # nao ha nivel inteiro para agrupar de antemao.
        while self.queue:
            owner, object_name, subprogram, depth = self.queue.popleft()
            self._process_subprogram(owner, object_name, subprogram, depth)

    # ---- semeadura ----

    def seed(self, root: RootLike) -> None:
        owner, object_name, subprogram = _normalize_root(root)
        if subprogram is not None:
            self._enqueue_subprogram(owner, object_name, subprogram, 0)
            return

        # Raiz de 2 partes: semeia toda a API publica da spec.
        cache = self._load_object(owner, object_name)
        if cache is None:
            self.note_truncation(
                "limite de max_objects={} atingido antes de semear {}.{}".format(
                    self.max_objects, owner.upper(), object_name.upper()
                )
            )
            self.not_expanded.append("{}.{}".format(owner.upper(), object_name.upper()))
            return
        for name in cache.public_subprograms:
            self._enqueue_subprogram(owner, object_name, name, 0)

    # ---- resultado ----

    def to_result(self) -> ProcGraphResult:
        stats = {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "objects_loaded": len(self._object_cache),
            "not_expanded": len(self.not_expanded),
            "blind_spots": len(self.blind_spots),
            "max_objects": self.max_objects,
            "max_depth": self.max_depth,
        }
        sorted_nodes = sorted(
            self.nodes.values(), key=lambda n: (n.owner, n.object_name, n.subprogram)
        )
        sorted_edges = sorted(
            self.edges,
            key=lambda e: (e.from_ref, e.to_ref, e.edge_type, e.line if e.line is not None else -1),
        )
        return ProcGraphResult(
            nodes=sorted_nodes,
            edges=sorted_edges,
            truncated=self.truncated,
            truncation_reason=self.truncation_reason,
            not_expanded=sorted(set(self.not_expanded)),
            blind_spots=sorted(set(self.blind_spots)),
            stats=stats,
        )


def build_proc_graph(
    extractor: ProcExtractor,
    root: RootLike,
    roots: Optional[Sequence[RootLike]] = None,
    max_objects: Optional[int] = None,
    max_depth: Optional[int] = None,
) -> ProcGraphResult:
    """Ponto de entrada publico do motor (T-03).

    `root`/`roots`: mesmo padrao aditivo de `depgraph.build_dep_graph`
    (`root` obrigatorio, `roots` opcional para raizes ADICIONAIS na MESMA
    fila/visited-set -- ver docstring la para a justificativa do desenho).
    Cada raiz pode ser de 2 partes (`(owner, objeto)` -- semeia a API
    publica inteira) ou 3 partes (`(owner, objeto, subprograma)` -- semeia
    so aquele subprograma).

    `max_objects`/`max_depth`: default `None` = SEM CAP (profundidade
    total, regra do plano secao 4 -- diferente de `depgraph.build_dep_graph`,
    que tem defaults numericos). Caps so entram como opt-in explicito do
    chamador e, quando acionados, truncam item a item em
    `ProcGraphResult.not_expanded` -- nunca silenciosamente (ver
    `_ProcGraphEngine._load_object`/`_process_subprogram`).

    Terminacao garantida mesmo com ciclo (recursao direta, mutua, ou
    atravessando N objetos) pelo visited-set marcado no enqueue -- ver
    docstring do modulo para a prova completa.
    """
    engine = _ProcGraphEngine(extractor, max_objects, max_depth)
    engine.seed(root)
    for extra_root in roots or ():
        engine.seed(extra_root)
    engine.run()
    return engine.to_result()
