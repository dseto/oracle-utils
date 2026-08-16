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

FALLBACK DE GRAO OBJETO (T-04, contrato depgraph-granular): quando a
cadeia alcanca um objeto PL/SQL sem PL/Scope `IDENTIFIERS` utilizavel (ou
*wrapped*), a atribuicao por subprograma e IMPOSSIVEL -- mas a travessia
nunca para nem omite (docs/plano-depgraph-granular.md, secao 4,
"Fallback de completude"). Este motor reusa `depgraph._DepGraphEngine`
(fronteira/sinonimo/db_link/caps/catalogo, NAO duplicados aqui) para
expandir esse objeto em grao OBJETO, e tudo que ele alcanca continua a
travessia atraves dele.

Dois pontos de entrada da fallback (`_fallback_reason` decide SE cabe,
`_fallback_object_grain` executa):
- `_process_call`: uma CALL que so resolve ate o OWNER/OBJETO (via
  `resolve_owner`, ver secao "Resolucao inter-package" acima) mas cujo
  objeto, uma vez carregado, nao tem identifiers utilizaveis. ANTES desta
  tarefa isso virava um no `__UNRESOLVED__` sem saida -- exatamente o
  buraco que o plano descreve (o objeto seguinte da cadeia, alcancavel so
  via ALL_DEPENDENCIES a partir dali, sumia). Este e o caminho que a
  cadeia A(fino)->B(sem PL/Scope)->C(fino) do teste percorre: a CALL de A
  para B nunca resolve por signature (B nunca contribuiu nenhuma
  DEFINITION para o indice, exatamente porque nao tem identifiers) --
  so o `resolve_owner` pontual descobre que o alvo E B.
- `_process_subprogram`: o objeto foi enfileirado DIRETO como alvo de
  subprograma (raiz de 3 partes nomeando um subprograma de um objeto sem
  PL/Scope -- unico jeito de chegar aqui sem passar por uma CALL
  resolvida, ja que `ResolvedCall` exige DEFINITION indexada, que exige
  identifiers). O no de 3 partes nunca chega a ser criado: identidade de
  no em fallback e sempre de 2 partes (plano, secao "Identidade de no").

TRADUCAO DE VISITED-SET (requisito duro do T-04, decisao de projeto
registrada aqui): os dois motores chaveiam identidade de formas
DIFERENTES -- o fino por (OWNER, OBJETO, SUBPROGRAMA), o de objeto
(`_DepGraphEngine`) por (OWNER, OBJETO). Duas travessias em um so
visited-set fisico nao funcionam (chaves de tamanhos diferentes), entao a
integracao e uma TRADUCAO nos dois sentidos, cada um com seu proprio
mecanismo:

1. "fino ja tocou -> fallback nao retoca": `_ProcGraphEngine` mantem
   `_subprogram_grain_objects: Set[(OWNER, OBJETO)]` -- a PROJECAO do
   visited-set fino (que e por subprograma) para o espaco por objeto do
   `_DepGraphEngine`. Um objeto entra ali assim que o motor fino
   CONFIRMA que ele tem identifiers utilizaveis -- no ENQUEUE de uma
   `ResolvedCall` (`_process_call`) e no auto-enqueue de `__INIT__`, ALEM
   do ponto de confirmacao normal em `_process_subprogram` (rede de
   seguranca para raiz direta). Marcar no ENQUEUE (nao so quando o
   subprograma e de fato desenfileirado) fecha uma janela de corrida real
   da BFS FIFO: um alvo resolvido entra no FIM da fila e so e processado
   depois de itens que ja estao nela -- se um desses itens anteriores
   disparar fallback ANTES do alvo ser desenfileirado, o fallback precisa
   already saber que aquele objeto e territorio fino, mesmo sem o no
   ainda existir (prova:
   `test_fine_visited_object_not_reexpanded_by_fallback`, que controla a
   ordem da fila de proposito para exercitar exatamente essa janela).
   `_get_fallback_engine` sincroniza este set para dentro de
   `_DepGraphEngine.visited` ANTES de cada enqueue/run (nao so na
   criacao): o motor fino pode reclamar objetos novos entre um gatilho de
   fallback e o proximo. `_DepGraphEngine.enqueue`/`_process_object` ja
   no-opam sozinhos para qualquer chave presente em `visited` -- nenhuma
   mudanca de comportamento la, so um visited-set pre-populado de fora
   (mesmo mecanismo que `from_result` ja usa para reidratar estado).
2. "fallback ja tocou -> fino nao retoca": todo objeto que o
   `_DepGraphEngine` reusado processa vira um `ProcNode` de 2 partes
   (`OWNER.OBJETO`) com `grain="object"` em `self.nodes`
   (`_sync_fallback_results`). `_fallback_ref_if_covered` checa essa
   chave ANTES de `_load_object` em `_process_call` E em
   `_process_subprogram` -- se o objeto ja esta coberto, so uma aresta e
   adicionada (`resolved=True`, sem CALL/STMT novos), nunca uma nova
   chamada a `plscope_identifiers`/`plscope_statements` (prova:
   `test_fallback_expanded_object_not_reprocessed_by_fine_engine`, que
   conta chamadas no fake).

Consequencia aceita (nao testada letra a letra, registrada aqui): quando
uma aresta do `_DepGraphEngine` aponta para um objeto que e territorio
fino (caso 1 acima), o alvo fica no ref de 2 partes (`OWNER.OBJETO`) em
vez do ref de 3 partes de um subprograma especifico -- o motor de objeto
GENUINAMENTE nao sabe qual subprograma esta sendo alcancado (so
ALL_DEPENDENCIES, sem signature), entao apontar para um subprograma
especifico seria inventar precisao que o dado nao sustenta. A aresta
ainda fecha o ciclo/mostra a dependencia (ref de 2 partes E um prefixo
legivel do objeto), so nao aponta para o no exato -- coerente com "grao
objeto declarado onde o compilador nao prova" (plano, secao 4).

Deteccao de *wrapped* (motivo distinto de "sem identifiers" exigido pelo
T-04): metodo OPCIONAL `object_wrapped(owner, object_name) -> bool` no
Protocol `ProcExtractor` (mesmo padrao `hasattr` de `resolve_owner`) --
quando ausente, todo objeto sem identifiers cai no motivo generico "sem
PL/Scope identifiers". A wiring real (consulta que decide *wrapped* --
provavelmente inspecionar o texto de ALL_SOURCE em busca do marcador de
`DBMS_DDL.WRAP`) fica para quem monta o extractor real do CLI (fora do
escopo desta tarefa, que so cobre `plsqlflow/procgraph.py`); os testes
deste modulo alimentam um fake que implementa o metodo diretamente.

`dep_extractor` ausente (`None`, o default) mas fallback necessario: a
regra "nunca omite em silencio" vale mesmo sem o extractor de objeto
configurado -- em vez de lancar excecao (que mataria a travessia
INTEIRA por um unico objeto problematico) ou fingir cobertura, o no de
fallback e criado mesmo assim (`grain="object"`, `is_leaf=True`) com o
motivo mais a ressalva de que o fallback nao pode expandir, e o gap
entra em `not_expanded`/`blind_spots`/`truncated` -- declarado, nunca
silencioso.
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

from . import depgraph
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

# T-04: `_DepGraphEngine.__init__` exige max_objects/max_depth como `int`
# (o motor de objeto original sempre teve cap default numerico -- ver
# depgraph.build_dep_graph). O motor fino usa `Optional[int]` (`None` =
# sem cap, regra do modo granular). Quando o usuario nao passou cap
# proprio para o motor fino, o fallback tambem roda sem cap PRATICO --
# este sentinel e "grande o suficiente para nunca bater" sem precisar
# ensinar `_DepGraphEngine` a entender `None`.
_NO_CAP_SENTINEL = 2**31 - 1


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
    "object" para o fallback (objeto sem PL/Scope utilizavel, ou
    *wrapped*, expandido em grao objeto inteiro via
    `plsqlflow.depgraph._DepGraphEngine` reusado -- ver
    `_ProcGraphEngine._fallback_object_grain`/`_sync_fallback_results` e a
    secao "FALLBACK DE GRAO OBJETO" da docstring do modulo). No de
    fallback usa identidade de 2 partes (`OWNER.OBJETO`, plano secao
    "Identidade de no") -- `subprogram=""` (nunca `None`: o campo do
    dataclass e `str`) e o sentinel para "este no nao tem subprograma,
    e o objeto inteiro". `grain="unresolved"` e deste modulo: CALL cuja
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
    falta de resolucao, regra dura do contrato).

    `op`/`cols` (T-05, seam de acesso): mesma semantica de
    `depgraph.DepEdge.op`/`.cols` -- `op` e o tipo do statement (INSERT/
    UPDATE/DELETE/MERGE) para edge_type=WRITE, ou o evento (ex.:
    'INSERT') para edge_type=TRIGGER_FIRES; `cols` e a lista de colunas
    do COMPILADOR (PL/Scope, nunca regex), best-effort (None quando o
    compilador nao entregou identificador COLUMN nenhum -- nunca lista
    inventada). Para READ/WRITE, `to_ref` NAO e um subprograma (e uma ref
    de tabela `OWNER.TABELA`, 2 partes); para TRIGGER_FIRES, `to_ref`
    aponta para o trigger COMO subprograma (3 partes, mesmo formato de
    `_ref`) -- e o que permite ao motor enfileira-lo em vez de trata-lo
    como folha (ver `_ProcGraphEngine._process_stmt`)."""

    from_ref: str
    to_ref: str
    edge_type: str  # "CALL" (T-03); READ/WRITE/STATE_*/TRIGGER_FIRES (T-05)
    line: Optional[int]
    signature: Optional[str] = None
    resolved: bool = True
    reason: Optional[str] = None
    confidence: str = "exact"
    op: Optional[str] = None
    cols: Optional[List[str]] = None


@dataclass
class ProcGraphResult:
    """Espelha `depgraph.DepGraphResult` (mesmo contrato de
    truncamento/nao-omissao): `truncated` so e True quando
    `truncation_reason` existe e `not_expanded` nao esta vazio -- nunca
    corte silencioso. `blind_spots` (novo aqui, sem equivalente em
    `DepGraphResult`) enumera toda CALL que nao pode ser atribuida a
    nenhum destino conhecido (alvo fora do escopo visivel, ex.:
    SYS.STANDARD) -- distinto de `not_expanded`, que e sobre caps de
    usuario, nao sobre alvo desconhecido.

    `needs_recompile` (T-04): refs de objeto (`OWNER.OBJETO`, 2 partes)
    que caem no fallback de grao objeto e sao recompilaveis para ganhar
    PL/Scope completo -- espelha `depgraph.DepGraphResult.needs_recompile`
    e vem DIRETO de la (`_DepGraphEngine` reusado ja calcula isso sozinho
    em `_process_object`, nao reimplementado aqui): todo objeto de tipo
    PL/SQL sem `IDENTIFIERS:ALL`+`STATEMENTS:ALL` completos entra aqui,
    nao so o objeto raiz do gatilho de fallback mas qualquer outro
    alcancado pelo mesmo caminho."""

    nodes: List[ProcNode]
    edges: List[ProcEdge]
    truncated: bool
    truncation_reason: Optional[str]
    not_expanded: List[str]
    blind_spots: List[str]
    needs_recompile: List[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    # Denominador honesto da reconciliacao de COBERTURA (correcao do
    # DEFEITO 2, procgraph_render.build_coverage): um par (ref, line) por
    # STMT de fato despachado a `_process_stmt` (nunca por aresta -- a
    # soma antiga contava arestas com o rotulo "statements" e fechava
    # mesmo quando um statement nao gerava aresta nenhuma, exatamente o
    # buraco que deixou o SQL dinamico de RUN_DYNAMIC sumir sem erro).
    # Populado por `_ProcGraphEngine.to_result()`; default vazio preserva
    # compatibilidade com `ProcGraphResult(...)` construido a mao nos
    # testes existentes (mesmo padrao de `needs_recompile`/`stats` acima).
    statements_read: List[Tuple[str, int]] = field(default_factory=list)


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
    motivo de existir e a garantia que ele fecha.

    `object_wrapped` (T-04) tambem e OPCIONAL (mesmo padrao `hasattr`):
    devolve True quando o objeto e PL/SQL *wrapped* (fonte embaralhada
    por `DBMS_DDL.WRAP`, PL/Scope nao consegue introspectar) -- motivo
    distinto de "sem PL/Scope identifiers" no fallback de grao objeto
    (ver docstring do modulo, secao "FALLBACK DE GRAO OBJETO"). Quando o
    extractor nao implementa, todo objeto sem identifiers cai no motivo
    generico.

    `triggers` (T-05, seam de acesso) tambem e OPCIONAL (mesmo padrao
    `hasattr`): espelha `depgraph.DepExtractor.triggers` -- descobre os
    triggers de uma tabela escrita (mesmo caminho de
    `depgraph._DepGraphEngine.expand_triggers`, reusado so como CONSULTA
    aqui, nao como motor); usado por `procgraph_access.expand_access` via
    o `extractor` repassado em `_process_stmt`. Quando ausente, tabela
    escrita nunca gera TRIGGER_FIRES (gap declarado, nao erro -- entrega 3
    de T-05 fica inerte sem esta capacidade no extractor real do CLI).

    `source` (correcao de DEFEITO 1, contrato depgraph-granular) tambem e
    OPCIONAL (mesmo padrao `hasattr`): espelha `depgraph.DepExtractor.
    source` -- texto-fonte de ALL_SOURCE, usado por `procgraph_access.
    expand_access` (via `_dynamic_sql_edges`) para tentar resolver/
    classificar SQL dinamico (EXECUTE IMMEDIATE/OPEN ... FOR/DBMS_SQL.
    PARSE) com `depgraph_enrich.dynamic_sql_findings`, reusado sem
    alteracao. Quando ausente, SQL dinamico NUNCA desaparece do grafo --
    vira marcador `opaque` declarado (aresta + ponto cego), so com o
    motivo "sem fonte" em vez de tentar resolver."""

    def plscope_identifiers(self, owner: str, object_name: str) -> Sequence[Any]: ...

    def plscope_statements(self, owner: str, object_name: str) -> Sequence[Any]: ...

    def resolve_owner(self, signature: str) -> Optional[Tuple[str, str]]: ...

    def object_wrapped(self, owner: str, object_name: str) -> bool: ...

    def triggers(self, owner: str, table_names: Sequence[str]) -> Sequence[Any]: ...

    def source(self, owner: str, object_name: str) -> Sequence[Any]: ...


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
        dep_extractor: Optional["depgraph.DepExtractor"] = None,
        stop_schemas: Sequence[str] = ("SYS", "SYSTEM"),
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
        self.needs_recompile: List[str] = []
        # Correcao do DEFEITO 2 (COBERTURA, procgraph_render.py): um par
        # (ref, line) por STMT de fato despachado a `_process_stmt` --
        # denominador honesto para `build_coverage` provar que todo
        # statement lido via PL/Scope tem pelo menos uma aresta
        # correspondente (READ/WRITE/STATE_*/TRIGGER_FIRES/DYNAMIC_SQL).
        self._statements_seen: List[Tuple[str, int]] = []

        self.truncated = False
        self.truncation_reason: Optional[str] = None

        self._object_cache: Dict[Tuple[str, str], _ObjectCache] = {}
        self._definition_index: Dict[str, DefinitionEntry] = {}
        self._init_enqueued: Set[Tuple[str, str]] = set()

        # T-04: fallback de grao objeto (ver docstring do modulo, secao
        # "FALLBACK DE GRAO OBJETO" e "TRADUCAO DE VISITED-SET").
        # `dep_extractor` alimenta o `_DepGraphEngine` reusado -- None
        # (default) e valido para qualquer travessia que nunca precisar
        # do fallback (ex.: toda a suite de test_procgraph_bfs.py); so
        # importa quando um objeto sem PL/Scope e de fato alcancado (ver
        # `_fallback_object_grain`).
        self.dep_extractor = dep_extractor
        self.stop_schemas = tuple(stop_schemas)
        self._fallback_engine: Optional["depgraph._DepGraphEngine"] = None
        # Projecao do visited-set fino (OWNER, OBJETO, SUBPROGRAMA) para o
        # espaco por objeto (OWNER, OBJETO) do `_DepGraphEngine` -- ver
        # docstring do modulo, item 1 de "TRADUCAO DE VISITED-SET".
        self._subprogram_grain_objects: Set[Tuple[str, str]] = set()
        # Motivo (T-04) do gatilho de fallback, por objeto -- so para o no
        # RAIZ do fallback (objetos alcancados transitivamente por
        # ALL_DEPENDENCIES a partir dali carregam so o `note` que o
        # proprio `_DepGraphEngine` ja calcula, ex.: fronteira/db_link).
        self._fallback_root_reasons: Dict[Tuple[str, str], str] = {}

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

    # ---- T-04: fallback de grao objeto (ver docstring do modulo) ----

    def _claim_subprogram_grain(self, owner: str, object_name: str) -> None:
        """Marca (owner, objeto) como territorio do motor fino -- item 1
        de "TRADUCAO DE VISITED-SET" na docstring do modulo. Chamado assim
        que o motor fino CONFIRMA identifiers utilizaveis para o objeto
        (ResolvedCall em `_process_call`, auto-enqueue de `__INIT__`, e o
        proprio sucesso de `_process_subprogram`) -- nunca antes disso,
        senao um objeto que ainda vai cair em fallback (raiz de 3 partes
        apontando direto para um objeto sem PL/Scope) ficaria marcado
        fino por engano e o proprio fallback dele seria bloqueado."""
        self._subprogram_grain_objects.add((owner.upper(), object_name.upper()))

    def _fallback_ref_if_covered(self, owner: str, object_name: str) -> Optional[str]:
        """Item 2 de "TRADUCAO DE VISITED-SET": objeto ja expandido em
        grao objeto (por QUALQUER gatilho anterior, direto ou transitivo)
        aparece em `self.nodes` sob o ref de 2 partes. Checar isto ANTES
        de `_load_object` evita a releitura via
        `plscope_identifiers`/`plscope_statements` do motor fino."""
        obj_ref = "{}.{}".format(owner.upper(), object_name.upper())
        node = self.nodes.get(obj_ref)
        if node is not None and node.grain == "object":
            return obj_ref
        return None

    def _fallback_reason(
        self, owner: str, object_name: str, cache: "_ObjectCache"
    ) -> Optional[str]:
        """Motivo LEGIVEL de rebaixamento para grao objeto, ou None se o
        objeto continua em grao subprograma. Ordem de checagem: *wrapped*
        primeiro (mais especifico), depois ausencia de identifiers --
        NUNCA ausencia de statements sozinha (regra 7 do contrato: objeto
        PARCIAL, identifiers sim/statements nao, continua fino -- ver
        `_process_subprogram`, que so chama isto depois de `_load_object`
        ja ter separado as duas capacidades)."""
        if hasattr(self.extractor, "object_wrapped") and self.extractor.object_wrapped(
            owner, object_name
        ):
            return (
                "objeto wrapped -- PL/Scope nao consegue introspectar bytecode wrapped, "
                "atribuicao por subprograma impossivel"
            )
        if not cache.has_identifiers:
            return "sem PL/Scope identifiers -- atribuicao por subprograma impossivel"
        return None

    def _get_fallback_engine(self) -> "depgraph._DepGraphEngine":
        if self._fallback_engine is None:
            no_cap_objects = self.max_objects if self.max_objects is not None else _NO_CAP_SENTINEL
            no_cap_depth = self.max_depth if self.max_depth is not None else _NO_CAP_SENTINEL
            self._fallback_engine = depgraph._DepGraphEngine(
                self.dep_extractor, self.stop_schemas, no_cap_objects, no_cap_depth
            )
        # Sincroniza ANTES de cada uso (nao so na criacao): o motor fino
        # pode reclamar objetos novos entre um gatilho de fallback e o
        # proximo. `enqueue`/`_process_object` do `_DepGraphEngine` ja
        # no-opam sozinhos para qualquer chave presente em `visited` --
        # nenhuma mudanca de comportamento la, so um visited-set
        # pre-populado de fora (mesmo mecanismo que `from_result` usa).
        self._fallback_engine.visited.update(self._subprogram_grain_objects)
        return self._fallback_engine

    def _fallback_object_grain(self, owner: str, object_name: str, reason: str) -> str:
        """Executa o fallback de grao objeto para (owner, object_name):
        expande via `_DepGraphEngine` reusado (fronteira/sinonimo/
        db_link/caps/catalogo, nao duplicados aqui) e devolve o ref de 2
        partes do no resultante. Idempotente -- objeto ja expandido nesta
        travessia (por outro gatilho) so devolve o ref existente."""
        obj_ref = "{}.{}".format(owner.upper(), object_name.upper())
        key = (owner.upper(), object_name.upper())

        existing = self.nodes.get(obj_ref)
        if existing is not None and existing.grain == "object":
            return obj_ref

        if self.dep_extractor is None:
            # Regra dura do contrato: nunca omitir em silencio. Sem
            # dep_extractor configurado nao ha como consultar
            # ALL_DEPENDENCIES para expandir o objeto -- declara o gap
            # (truncamento + ponto cego) em vez de fingir cobertura ou de
            # derrubar a travessia inteira com uma excecao.
            self.nodes[obj_ref] = ProcNode(
                owner=key[0],
                object_name=key[1],
                subprogram="",
                grain="object",
                is_leaf=True,
                note="{} -- fallback grao objeto indisponivel: nenhum dep_extractor configurado".format(
                    reason
                ),
            )
            self.blind_spots.append("{} ({}, sem dep_extractor)".format(obj_ref, reason))
            self.not_expanded.append(obj_ref)
            self.note_truncation(
                "fallback grao objeto exigido para {} mas nenhum dep_extractor foi configurado".format(
                    obj_ref
                )
            )
            return obj_ref

        self._fallback_root_reasons.setdefault(key, reason)
        engine = self._get_fallback_engine()
        engine.enqueue(owner, object_name, 0, None)
        engine.run()
        self._sync_fallback_results()
        return obj_ref

    def _sync_fallback_results(self) -> None:
        """Espelha o estado atual do `_DepGraphEngine` reusado para dentro
        de `self.nodes`/`self.edges`/`self.needs_recompile`. Roda inteiro
        (nao so o delta) a cada chamada -- barato (subgrafos de fallback
        sao tipicamente pequenos) e mantem tudo deterministico/idempotente
        sem precisar rastrear o que ja foi sincronizado antes."""
        engine = self._fallback_engine
        if engine is None:
            return

        for key, dep_node in engine.nodes.items():
            obj_ref = "{}.{}".format(key[0], key[1])
            root_reason = self._fallback_root_reasons.get(key)
            if root_reason is not None and dep_node.note:
                note = "{}; {}".format(root_reason, dep_node.note)
            else:
                note = root_reason or dep_node.note
            proc_node = self.nodes.get(obj_ref)
            if proc_node is None:
                self.nodes[obj_ref] = ProcNode(
                    owner=dep_node.owner,
                    object_name=dep_node.object_name,
                    subprogram="",
                    grain="object",
                    plscope_identifiers=dep_node.plscope_identifiers,
                    plscope_statements=dep_node.plscope_statements,
                    is_leaf=dep_node.is_leaf,
                    note=note,
                )
            else:
                proc_node.is_leaf = dep_node.is_leaf
                proc_node.plscope_identifiers = dep_node.plscope_identifiers
                proc_node.plscope_statements = dep_node.plscope_statements
                proc_node.note = note

        for dep_edge in engine.edges:
            # ProcEdge nao modela todos os campos de DepEdge (op/cols/
            # dynamic/context/snippet_ref sao especificos de grao objeto
            # com STMT, fora do escopo desta tarefa) -- so a conectividade
            # (from/to/tipo/linha/confidence) atravessa o merge.
            self._add_edge(
                dep_edge.from_ref,
                dep_edge.to_ref,
                dep_edge.edge_type,
                dep_edge.line,
                resolved=True,
                confidence=dep_edge.confidence,
            )

        for ref in engine.needs_recompile:
            if ref not in self.needs_recompile:
                self.needs_recompile.append(ref)

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
            if root_def is None:
                # TRIGGER (fato provado contra o banco dev, T-05, contrato
                # depgraph-granular: GESTAO.TRG_FLOW_DEMO_LOG): a raiz
                # (usage_context_id=0) e uma DECLARATION, com a DEFINITION
                # correspondente como filho DIRETO dela (usage_context_id
                # == usage_id da DECLARATION) -- diferente de PROCEDURE/
                # FUNCTION standalone, cuja raiz ja E a propria DEFINITION.
                # Sem este fallback, todo TRIGGER ficava com
                # public_subprograms=[] e nunca virava no de subprograma
                # nenhum (entrega 3 de T-05 -- "trigger entra como
                # subprograma" -- ficava impossivel de alcancar).
                root_decl = next(
                    (r for r in body_identifiers if r.usage_context_id == 0 and r.usage == "DECLARATION"),
                    None,
                )
                if root_decl is not None:
                    root_def = next(
                        (
                            r
                            for r in body_identifiers
                            if r.usage_context_id == root_decl.usage_id and r.usage == "DEFINITION"
                        ),
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
                # T-04: objeto ja coberto pelo fallback (grao objeto) por
                # um gatilho anterior -- nao retoca (item 2 de "TRADUCAO
                # DE VISITED-SET" na docstring do modulo). So aresta.
                covered_ref = self._fallback_ref_if_covered(owner_hint[0], owner_hint[1])
                if covered_ref is not None:
                    self._add_edge(
                        from_ref,
                        covered_ref,
                        "CALL",
                        assignment.line,
                        signature=assignment.signature,
                        resolved=True,
                    )
                    return

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
                    fallback_reason = self._fallback_reason(owner_hint[0], owner_hint[1], loaded)
                    if fallback_reason is not None:
                        # T-04: e exatamente aqui que a cadeia sumia antes
                        # desta tarefa -- o objeto carregado nao contribui
                        # nenhuma DEFINITION (sem identifiers utilizaveis),
                        # entao a signature NUNCA vai resolver por mais
                        # tentativas que fizermos. Em vez de um
                        # `__UNRESOLVED__` sem saida, o destino inteiro
                        # cai no fallback de grao objeto -- e tudo que ELE
                        # alcanca continua a travessia (docstring do
                        # modulo, secao "FALLBACK DE GRAO OBJETO").
                        obj_ref = self._fallback_object_grain(
                            owner_hint[0], owner_hint[1], fallback_reason
                        )
                        self._add_edge(
                            from_ref,
                            obj_ref,
                            "CALL",
                            assignment.line,
                            signature=assignment.signature,
                            resolved=True,
                        )
                        return
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
            # T-04: ResolvedCall so acontece quando o alvo ja contribuiu
            # DEFINITION (identifiers presentes) -- reclama o objeto para
            # o territorio fino AGORA (no enqueue), nao so quando ele for
            # de fato desenfileirado (ver `_claim_subprogram_grain`).
            self._claim_subprogram_grain(to_key_owner, to_key_object)
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

    def _process_stmt(
        self,
        owner: str,
        object_name: str,
        subprogram: str,
        cache: _ObjectCache,
        assignment: Optional[Assignment],
        depth: int,
    ) -> None:
        # Seam de T-05 (ver docstring do modulo) -- statement ja atribuido
        # ao subprograma EXATO (nunca ao objeto inteiro, diferenca chave
        # para graph.py). `assignment=None` e a varredura POR SUBPROGRAMA
        # (T-05, entrega 2 -- estado de package, ver chamada em
        # `_process_subprogram`): acesso a variavel/constante/cursor de
        # package acontece via identificador ASSIGNMENT/REFERENCE solto,
        # que NUNCA vira um STATEMENT do PL/Scope (`V_X := 5;` nao entra
        # em ALL_STATEMENTS), entao um subprograma que so mexe em estado
        # (sem SQL nenhum) precisa do seam disparado mesmo sem nenhum
        # Assignment kind=STMT -- senao seu STATE_WRITE nunca apareceria.
        # `extractor` repassado (T-05, entrega 3): expand_access usa
        # `extractor.triggers` (Protocol OPCIONAL, mesmo padrao hasattr de
        # `resolve_owner`/`object_wrapped`) para achar os triggers de uma
        # tabela escrita -- descoberta reusa o caminho de
        # `depgraph._DepGraphEngine.expand_triggers`, mas o destino aqui e
        # a FILA deste motor fino (enqueue de TRIGGER_FIRES abaixo), nao
        # uma folha. `dep_extractor` repassado (entrega 4, SQL dinamico):
        # `expand_access` usa `dep_extractor.object_catalog` (mesmo
        # Protocol que o fallback de T-04 ja consome) so para achar
        # candidato do nivel "partial" -- `None` degrada para "opaque" com
        # mais frequencia, nunca lanca.
        edges = _expand_access(
            owner=owner,
            object_name=object_name,
            subprogram=subprogram,
            statement=assignment,
            object_cache=cache,
            extractor=self.extractor,
            dep_extractor=self.dep_extractor,
        )

        if assignment is not None:
            # Correcao do DEFEITO 2 (COBERTURA): registra o STMT como
            # "lido" ANTES de saber se ele produziu aresta -- e o
            # denominador honesto que `build_coverage` usa depois para
            # provar (ou levantar CoverageError) que cada statement esta
            # representado. `assignment is None` e a varredura de estado
            # (entrega 2, statement=None) -- nunca um STMT de verdade,
            # nunca entra aqui.
            self._statements_seen.append(
                (self._ref(owner, object_name, subprogram), assignment.line)
            )

        for edge in edges or []:
            if not isinstance(edge, ProcEdge):
                continue
            self._add_edge(
                edge.from_ref,
                edge.to_ref,
                edge.edge_type,
                edge.line,
                signature=edge.signature,
                resolved=edge.resolved,
                reason=edge.reason,
                confidence=edge.confidence,
                op=edge.op,
                cols=edge.cols,
            )
            if edge.edge_type == "TRIGGER_FIRES":
                # T-05, entrega 3: trigger de tabela escrita entra na
                # travessia COMO SUBPROGRAMA -- `to_ref` e uma ref de 3
                # partes (OWNER.TRIGGER.TRIGGER, mesmo formato de `_ref`)
                # que `expand_access` ja resolveu; o visited-set de
                # `_enqueue_subprogram` garante que um trigger ja
                # alcancado (ex.: ciclo T1->TRG1->T2->TRG2->T1) nunca
                # reentra na fila -- so a aresta (o ciclo) fica visivel.
                parts = edge.to_ref.split(".")
                if len(parts) == 3:
                    self._enqueue_subprogram(parts[0], parts[1], parts[2], depth + 1)
            elif edge.edge_type == "DYNAMIC_SQL" and edge.confidence in ("partial", "opaque"):
                # Correcao do DEFEITO 1: SQL dinamico que nao resolveu
                # 100% literal e ponto cego, nunca so uma aresta muda --
                # mesmo formato de texto que o modo objeto ja usa
                # (`depgraph_render._blind_spot_lines`, secao "SQL
                # dinamico nao resolvido") para nao-regressao visual entre
                # os dois modos.
                line_label = "L{}".format(edge.line) if edge.line is not None else "L?"
                self.blind_spots.append(
                    "{} {} [{}] -> {}".format(edge.from_ref, line_label, edge.confidence, edge.to_ref)
                )

    # ---- processamento de um subprograma desenfileirado ----

    def _process_subprogram(self, owner: str, object_name: str, subprogram: str, depth: int) -> None:
        ref = self._ref(owner, object_name, subprogram)
        if ref in self.nodes:
            # Defensivo (mesmo padrao de _DepGraphEngine._process_object):
            # o visited-set do enqueue ja evita duplicata.
            return

        # T-04: objeto ja coberto pelo fallback -- nao retoca (mesma
        # invariante de _process_call, item 2 de "TRADUCAO DE
        # VISITED-SET"). So acontece na pratica quando um ResolvedCall
        # aponta para um objeto que TAMBEM acaba marcado fallback (ex.:
        # wrapped com identifiers residuais, ver _fallback_reason) --
        # rede de seguranca, nao o caminho principal.
        if self._fallback_ref_if_covered(owner, object_name) is not None:
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

        fallback_reason = self._fallback_reason(owner, object_name, cache)
        if fallback_reason is not None:
            # PONTO DE DECISAO do T-04 (regra 7 do contrato): objeto
            # alcancado DIRETO (raiz de 3 partes nomeando um subprograma
            # de um objeto sem PL/Scope utilizavel -- unico jeito deste
            # ramo disparar sem passar pelo caminho equivalente em
            # `_process_call`, ja que ResolvedCall exige DEFINITION
            # indexada, que exige identifiers) cai no MESMO fallback de
            # grao objeto. O no de 3 partes (`ref`) NUNCA e criado aqui:
            # identidade de fallback e sempre de 2 partes (plano, secao
            # "Identidade de no") -- nenhuma aresta de entrada preexistia
            # apontando para o ref de 3 partes neste cenario (ver
            # docstring do modulo para a prova completa).
            self._fallback_object_grain(owner, object_name, fallback_reason)
            return

        # Identifiers confirmados -- reclama o objeto para o territorio
        # fino (rede de seguranca; ResolvedCall/__INIT__ ja reclamam mais
        # cedo, no enqueue -- ver `_claim_subprogram_grain`).
        self._claim_subprogram_grain(owner, object_name)

        node = ProcNode(
            owner=owner.upper(),
            object_name=object_name.upper(),
            subprogram=subprogram,
            plscope_identifiers=cache.has_identifiers,
            plscope_statements=cache.has_statements,
        )
        self.nodes[ref] = node

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

        # T-05, entrega 2: varredura de estado POR SUBPROGRAMA, uma vez,
        # ANTES/independente do laco de assignments abaixo -- ver docstring
        # de `_process_stmt` para o motivo (acesso a estado nao e um STMT,
        # entao um subprograma so-de-estado nunca dispararia o seam se a
        # chamada fosse so por statement).
        self._process_stmt(owner, object_name, subprogram, cache, None, depth)

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
                self._process_stmt(owner, object_name, subprogram, cache, assignment, depth)

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
            "needs_recompile": len(self.needs_recompile),
            "fallback_objects": sum(1 for n in self.nodes.values() if n.grain == "object"),
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
            needs_recompile=sorted(set(self.needs_recompile)),
            stats=stats,
            statements_read=sorted(self._statements_seen),
        )


def build_proc_graph(
    extractor: ProcExtractor,
    root: RootLike,
    roots: Optional[Sequence[RootLike]] = None,
    max_objects: Optional[int] = None,
    max_depth: Optional[int] = None,
    dep_extractor: Optional["depgraph.DepExtractor"] = None,
    stop_schemas: Sequence[str] = ("SYS", "SYSTEM"),
) -> ProcGraphResult:
    """Ponto de entrada publico do motor (T-03; `dep_extractor`/
    `stop_schemas` sao acrescimo do T-04).

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

    `dep_extractor` (T-04): fonte de dados do fallback de grao objeto
    (`plsqlflow.depgraph.DepExtractor`, o MESMO Protocol que
    `depgraph.build_dep_graph` consome) -- so e usado quando a travessia
    de fato alcanca um objeto sem PL/Scope utilizavel ou wrapped (ver
    docstring do modulo, secao "FALLBACK DE GRAO OBJETO"). `None`
    (default) e valido para qualquer alvo que nunca precisar do fallback;
    quando o fallback e necessario e nenhum `dep_extractor` foi passado,
    o gap fica declarado (`not_expanded`/`blind_spots`/`truncated`),
    nunca omitido em silencio nem derruba a travessia inteira.
    `stop_schemas` e repassado direto ao `_DepGraphEngine` reusado (mesmo
    default de `depgraph.build_dep_graph`).

    Terminacao garantida mesmo com ciclo (recursao direta, mutua, ou
    atravessando N objetos -- inclusive ciclo que atravessa o fallback)
    pelo visited-set marcado no enqueue -- ver docstring do modulo para a
    prova completa.
    """
    engine = _ProcGraphEngine(
        extractor, max_objects, max_depth, dep_extractor=dep_extractor, stop_schemas=stop_schemas
    )
    engine.seed(root)
    for extra_root in roots or ():
        engine.seed(extra_root)
    engine.run()
    return engine.to_result()
