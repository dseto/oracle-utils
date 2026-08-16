"""Deteccao de ciclos (T-07, contrato depgraph-granular) sobre o grafo de
subprogramas produzido por `plsqlflow/procgraph.py` (T-03).

Modulo PURO: nenhuma funcao aqui toca rede/banco nem importa nada alem do
modelo de dados de `procgraph.py` (que este modulo CONSOME, nunca edita --
outra tarefa mexe em `procgraph.py` em paralelo). A entrada e uma sequencia
de arestas (`ProcEdge` ou qualquer objeto com os mesmos tres atributos --
duck-typed de proposito, mesmo padrao de `FakeExtractor` em
tests/test_procgraph_bfs.py); a saida e uma lista de grupos deterministica.

POR QUE ISTO EXISTE (ver docs/plano-depgraph-granular.md, secao 4, "Protecao
contra recursao infinita"): o motor de T-03 ja TERMINA sob ciclo -- o
visited-set global garante isso, e o ciclo fica visivel no grafo como uma
aresta comum (reencontro de no ja visitado). Isso basta para terminacao mas
NAO basta para migracao: um conjunto de subprogramas que se chamam
mutuamente (direta ou indiretamente) e acoplamento que decide onde da e onde
nao da para cortar a fronteira de um servico. Este modulo faz esse
acoplamento aparecer NOMEADO, como um grupo (SCC), em vez de precisar ser
deduzido seguindo setas no grafo bruto.

ALGORITMO: Tarjan iterativo (pilha explicita, nao recursao Python). O alvo
do contrato sao processos gigantes com centenas de subchamadas atravessando
dezenas de packages -- uma implementacao recursiva de Tarjan estouraria o
limite de recursao do Python (`sys.getrecursionlimit()`, default 1000) num
grafo profundo o bastante para ser realista neste dominio. A versao aqui
simula a pilha de chamadas com uma lista de frames (`[no, indice_do_proximo_
vizinho_a_visitar]`), mesma tecnica classica de "converter recursao em
pilha explicita": cada iteracao do laco externo processa um frame do topo,
empurra um frame filho quando encontra um vizinho nao visitado (equivalente
a uma chamada recursiva) e so faz pop + propaga lowlink para o pai quando
todos os vizinhos do no corrente ja foram examinados (equivalente a retornar
da chamada). Prova de que isso nao estoura pilha: `test_deep_chain_of_2000_
nodes_does_not_overflow_recursion` em tests/test_procgraph_cycles.py.

QUAIS ARESTAS FORMAM CICLO (decisao de projeto deste modulo, exposta via
parametro `edge_types`): por default so `CALL` e `TRIGGER` contam.
- `CALL` obviamente conta -- e a propria definicao de ciclo de chamada.
- `TRIGGER` (nome ANTECIPADO para quando T-05 landar -- ver docstring de
  procgraph.py, secao "Seam de acesso", e plano secao 4: "tabela escrita ->
  triggers dela -> corpo do trigger entra na fila COMO SUBPROGRAMA") conta
  porque um ciclo de trigger (T1 grava T2 via TRG1, TRG2 grava T1 de volta)
  E uma sequencia real de execucao que acontece em runtime, tao real quanto
  uma CALL direta -- so que disparada implicitamente pelo commit/DML em vez
  de uma chamada explicita no codigo. Para quem migra, esse ciclo importa
  tanto quanto (ou mais que) um ciclo de CALL: nao da pra extrair T1 e T2
  para servicos diferentes sem quebrar a dependencia circular via trigger.
  Se T-05 landar com um `edge_type` diferente de "TRIGGER" para essa aresta,
  quem consome esta funcao (T-08) deve passar `edge_types=` explicitamente
  ate este default ser atualizado.
- `READ`/`WRITE` (acesso a tabela) e `STATE_READ`/`STATE_WRITE` (estado de
  package, T-05) ficam FORA do default deliberadamente: dois subprogramas
  que leem/escrevem a MESMA tabela ou a MESMA variavel de estado de package
  nao "se chamam" um ao outro nessas arestas -- nao ha ordem de execucao
  imposta por elas (A e B podem escrever a mesma tabela em qualquer ordem,
  em processos totalmente distintos, sem nunca se invocarem). Tratar
  READ/WRITE como aresta de ciclo de chamada geraria falso-positivo grosso
  (qualquer par de subprogramas que toque a mesma tabela viraria "ciclo").
  Acoplamento por dado compartilhado e informacao real e importante para
  migracao, mas e uma pergunta DIFERENTE ("quem compartilha estado/tabela")
  da que este modulo responde ("quem esta preso num ciclo de execucao").
  `edge_types` e parametro justamente para quem quiser fazer essa segunda
  pergunta poder reusar o mesmo motor de SCC com outro recorte de arestas.

CLASSIFICACAO (`CycleGroup.kind`): a distincao minima pedida pelo contrato e
ciclo DENTRO de um objeto (recursao direta ou mutua no mesmo package/type)
vs ciclo que ATRAVESSA objetos (acoplamento inter-package, o caso grave para
fronteira de servico). Um SCC de 1 membro so entra no resultado quando tem
aresta para si mesmo (self-loop, ver `_is_cycle`) -- SCC de 1 membro sem
self-loop nao e ciclo nenhum, e a maioria dos nos de qualquer grafo aciclico
cai exatamente nesse caso (cada no vira seu proprio SCC trivial). Identidade
de objeto usada para a classificacao e derivada do proprio `ref`
(`OWNER.OBJETO.SUBPROGRAMA[...]`, mesmo formato de `ProcNode`/`ProcEdge` --
ver "Identidade de no" no plano): os dois primeiros segmentos separados por
"." sao owner/objeto, sobra (terceiro segmento em diante, pode ter mais
pontos para subprograma aninhado) e o subprograma. Nao precisamos da lista
de `ProcNode` para isso -- os proprios refs das arestas ja carregam a
identidade, entao a API deste modulo pede so `edges`, nao `nodes`.

DETERMINISMO: nenhuma saida depende de ordem de iteracao de set/dict.
Vertices e listas de adjacencia sao ordenados ANTES de rodar Tarjan (a
ordem de visita de Tarjan afeta em QUAL SCC um no cai quando ha ambiguidade
de raiz, entao a entrada precisa ser ordenada, nao so a saida); os grupos e
os membros de cada grupo sao ordenados DEPOIS. Mesma entrada, em qualquer
ordem de arestas, sempre produz a mesma lista de grupos -- provado por
`test_determinism_...` em tests/test_procgraph_cycles.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Sequence, Set, Tuple

# Duck type minimo que este modulo consome de cada aresta -- qualquer objeto
# com estes 3 atributos serve (ProcEdge real, SimpleNamespace de teste, ou
# qualquer outra coisa). Nao importamos `procgraph.ProcEdge` para nao criar
# um acoplamento de tipo que este modulo nao precisa -- so os 3 campos
# importam aqui; os demais campos de ProcEdge (signature/resolved/reason/
# confidence) sao irrelevantes para deteccao de SCC.
EdgeLike = Any


# Nome CANONICO do disparo de trigger no repo: `depgraph.EDGE_TYPES` (que
# `procgraph_access`/T-05 reusa) chama isso de "TRIGGER_FIRES". "TRIGGER" fica
# como sinonimo aceito por robustez -- os dois entram no conjunto de proposito.
# Motivo de aceitar os dois em vez de so o canonico: um tipo de aresta de
# trigger que NAO esteja neste conjunto nao gera falso positivo, gera um ciclo
# de trigger que simplesmente NAO e reportado -- omissao silenciosa, que e o
# modo de falha que este contrato inteiro existe para impedir. Aceitar um nome
# a mais e barato; perder um ciclo real de execucao nao e.
_TRIGGER_EDGE_TYPE_NAMES = ("TRIGGER_FIRES", "TRIGGER")

# Default de quais tipos de aresta formam ciclo de EXECUCAO -- ver
# docstring do modulo, secao "QUAIS ARESTAS FORMAM CICLO", para a
# justificativa completa de cada tipo incluido/excluido.
DEFAULT_CYCLE_EDGE_TYPES: FrozenSet[str] = frozenset({"CALL"}) | frozenset(
    _TRIGGER_EDGE_TYPE_NAMES
)

# Subconjunto de `edge_types` usado so para marcar `CycleGroup.via_trigger`
# (bonus do contrato: "se conseguir identificar tambem ciclo que passa por
# trigger, melhor"). Separado de DEFAULT_CYCLE_EDGE_TYPES porque a pergunta
# "este grupo conta como ciclo" e diferente de "este grupo passa por
# trigger" -- um chamador pode customizar `edge_types` sem precisar
# recustomizar `trigger_edge_types` junto (e vice-versa).
DEFAULT_TRIGGER_EDGE_TYPES: FrozenSet[str] = frozenset(_TRIGGER_EDGE_TYPE_NAMES)


@dataclass(frozen=True)
class CycleGroup:
    """Um SCC (componente fortemente conexo) do grafo de chamada que
    caracteriza um ciclo real -- ver `_is_cycle` para o criterio exato (>1
    membro, OU 1 membro com aresta para si mesmo).

    `members`: refs (`OWNER.OBJETO.SUBPROGRAMA`) ordenados estavelmente
    (ordem alfabetica) -- nunca a ordem de descoberta do Tarjan, que nao e
    deterministica entre execucoes com arestas embaralhadas.
    `objects`: conjunto ORDENADO de (OWNER, OBJETO) distintos tocados pelo
    grupo -- e o que decide `kind`; exposto separado porque quem renderiza
    o mapa (T-08) precisa listar os objetos envolvidos sem reparsear
    `members`.
    `kind`: "direct_recursion" (1 membro, self-loop -- P chama P), "same_
    object" (2+ membros, todos no mesmo (owner, objeto) -- recursao mutua
    dentro de um package/type), ou "cross_object" (2+ membros tocando 2+
    objetos distintos -- o caso grave para fronteira de servico).
    `via_trigger`: True quando alguma aresta INTERNA ao grupo tem
    `edge_type` em `trigger_edge_types` (ver `find_cycles`) -- ciclo que
    passa por um disparo de trigger, nao so por CALL direta.
    `internal_edges`: arestas (from_ref, to_ref, edge_type) cujos DOIS
    extremos estao no grupo -- e o que efetivamente fecha o ciclo (arestas
    que saem do grupo para fora, ou entram de fora, nao aparecem aqui).
    Ordenadas estavelmente.
    """

    members: Tuple[str, ...]
    kind: str
    objects: Tuple[Tuple[str, str], ...]
    via_trigger: bool
    internal_edges: Tuple[Tuple[str, str, str], ...]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass
class CycleDetectionResult:
    """Resultado publico de `find_cycles`. `groups` ja vem ordenado
    estavelmente (ver docstring do modulo, secao DETERMINISMO). `stats` e
    um resumo barato para quem so precisa de contagem (ex.: T-08 montando a
    secao `## Ciclos` do INDEX) sem iterar `groups` de novo."""

    groups: List[CycleGroup]
    stats: Dict[str, Any] = field(default_factory=dict)


def _split_owner_object(ref: str) -> Tuple[str, str]:
    """`OWNER.OBJETO.SUBPROGRAMA[...]` -> (`OWNER`, `OBJETO`). So os dois
    primeiros segmentos importam para identidade de objeto (ver "Identidade
    de no" no plano) -- o resto (a partir do terceiro "." em diante) e o
    subprograma, que pode ele mesmo conter pontos para aninhamento
    (`EXTERNA.INTERNA`) e nao interessa aqui. Ref sem 2 segmentos (nunca
    deveria acontecer dado o contrato de `ProcEdge.from_ref`/`to_ref`, mas
    defensivo contra fixture malformada em teste) cai no proprio ref
    inteiro como "objeto", owner vazio -- nao quebra, so classifica
    "same_object" com uma unica entrada esquisita."""
    parts = ref.split(".", 2)
    if len(parts) < 2:
        return "", ref
    return parts[0], parts[1]


def _tarjan_scc(vertices: Sequence[str], adjacency: Dict[str, List[str]]) -> List[List[str]]:
    """Tarjan ITERATIVO (pilha explicita) -- ver docstring do modulo,
    secao ALGORITMO, para a tecnica e a justificativa de nao usar recursao
    Python. `vertices` e `adjacency[*]` DEVEM vir ja ordenados por quem
    chama (determinismo depende da ORDEM DE VISITA, nao so da ordenacao da
    saida -- reordenar so o resultado depois nao bastaria: Tarjan pode
    agrupar nos em SCCs "raiz" diferentes dependendo da ordem de descoberta
    quando o grafo tem mais de um componente, embora o CONJUNTO de SCCs em
    si seja sempre o mesmo independente de ordem -- ordenamos a entrada de
    qualquer forma para deixar tambem a ORDEM DE DESCOBERTA reprodutivel,
    nao so o resultado final).

    Devolve os componentes na ordem bruta em que o Tarjan os fecha (nao
    ordenado ainda -- `find_cycles` ordena o resultado final)."""
    index_counter = 0
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    on_stack: Set[str] = set()
    tarjan_stack: List[str] = []
    sccs: List[List[str]] = []

    for start in vertices:
        if start in indices:
            continue

        # Frame = [no, indice do proximo vizinho a examinar]. Uma lista (nao
        # tupla) porque o indice e mutado in-place enquanto o frame fica no
        # topo -- equivalente a variavel local de uma chamada recursiva que
        # "lembra onde parou" quando retomada apos a chamada filha retornar.
        work: List[List[Any]] = [[start, 0]]
        while work:
            node, pi = work[-1]
            if pi == 0:
                indices[node] = index_counter
                lowlink[node] = index_counter
                index_counter += 1
                tarjan_stack.append(node)
                on_stack.add(node)

            neighbors = adjacency.get(node, ())
            pushed_child = False
            while pi < len(neighbors):
                w = neighbors[pi]
                pi += 1
                if w not in indices:
                    # Equivalente a "strongconnect(w)" recursivo: guarda por
                    # onde o frame corrente parou e empurra o frame filho.
                    work[-1][1] = pi
                    work.append([w, 0])
                    pushed_child = True
                    break
                if w in on_stack and indices[w] < lowlink[node]:
                    lowlink[node] = indices[w]

            if pushed_child:
                continue

            # Todos os vizinhos de `node` ja foram examinados -- equivalente
            # a "retornar" da chamada recursiva. Propaga lowlink para o pai
            # (se houver) e, se `node` for raiz do proprio SCC, desempilha o
            # componente inteiro.
            work.pop()
            if work:
                parent = work[-1][0]
                if lowlink[node] < lowlink[parent]:
                    lowlink[parent] = lowlink[node]

            if lowlink[node] == indices[node]:
                component: List[str] = []
                while True:
                    w = tarjan_stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == node:
                        break
                sccs.append(component)

    return sccs


def _is_cycle(component: List[str], self_loops: Set[str]) -> bool:
    """Um SCC e um ciclo quando tem mais de 1 membro, OU quando tem
    exatamente 1 membro com aresta para si mesmo (recursao direta: P chama
    P). SCC de 1 membro SEM self-loop e o caso comum de todo no que nao
    participa de ciclo nenhum (cada no aciclico vira seu proprio SCC
    trivial) -- NAO entra no resultado, regra explicita do contrato."""
    if len(component) > 1:
        return True
    return component[0] in self_loops


def find_cycles(
    edges: Sequence[EdgeLike],
    *,
    edge_types: FrozenSet[str] = DEFAULT_CYCLE_EDGE_TYPES,
    trigger_edge_types: FrozenSet[str] = DEFAULT_TRIGGER_EDGE_TYPES,
) -> CycleDetectionResult:
    """Funcao PURA de deteccao de ciclos (T-07). Le `edges` (qualquer
    sequencia de objetos com `.from_ref`/`.to_ref`/`.edge_type`, tipicamente
    `ProcGraphResult.edges`), filtra pelos tipos em `edge_types` (ver
    docstring do modulo, secao "QUAIS ARESTAS FORMAM CICLO", para o default
    e a justificativa) e devolve todo SCC que e um ciclo de verdade (ver
    `_is_cycle`), classificado e ordenado deterministicamente.

    Nao recebe `nodes`: a identidade de objeto usada para classificar
    `kind` vem do proprio formato do ref (`OWNER.OBJETO.SUBPROGRAMA`,
    mesmo contrato de `ProcNode`/`ProcEdge`) -- ver `_split_owner_object`.
    Um no isolado (sem nenhuma aresta dos tipos considerados) nunca aparece
    em nenhum grupo -- SCC trivial sem self-loop, filtrado por `_is_cycle`.
    """
    relevant = [e for e in edges if e.edge_type in edge_types]

    # Vertices e adjacencia ordenados ANTES de rodar Tarjan -- determinismo
    # da ORDEM DE VISITA, nao so do resultado (ver docstring de
    # `_tarjan_scc`). Multi-arestas (mesmo par, mesmo tipo, linhas
    # diferentes) colapsam numa unica aresta de adjacencia -- Tarjan so
    # precisa de alcancabilidade, a lista completa de arestas internas
    # (com duplicatas legitimas) e reconstruida separadamente abaixo, so
    # para os grupos que sobrevivem.
    vertex_set: Set[str] = set()
    adjacency_sets: Dict[str, Set[str]] = {}
    self_loops: Set[str] = set()
    for e in relevant:
        vertex_set.add(e.from_ref)
        vertex_set.add(e.to_ref)
        adjacency_sets.setdefault(e.from_ref, set()).add(e.to_ref)
        if e.from_ref == e.to_ref:
            self_loops.add(e.from_ref)

    vertices = sorted(vertex_set)
    adjacency = {node: sorted(targets) for node, targets in adjacency_sets.items()}

    components = _tarjan_scc(vertices, adjacency)

    groups: List[CycleGroup] = []
    for component in components:
        if not _is_cycle(component, self_loops):
            continue
        member_set = set(component)
        members = tuple(sorted(component))

        objects = tuple(sorted({_split_owner_object(ref) for ref in members}))
        kind = (
            "direct_recursion"
            if len(members) == 1
            else ("same_object" if len(objects) == 1 else "cross_object")
        )

        internal = sorted(
            {
                (e.from_ref, e.to_ref, e.edge_type)
                for e in relevant
                if e.from_ref in member_set and e.to_ref in member_set
            }
        )
        via_trigger = any(edge_type in trigger_edge_types for (_, _, edge_type) in internal)

        groups.append(
            CycleGroup(
                members=members,
                kind=kind,
                objects=objects,
                via_trigger=via_trigger,
                internal_edges=tuple(internal),
            )
        )

    # Ordenacao FINAL, estavel: por membros (tupla ja ordenada dentro de
    # cada grupo) -- determina uma ordem TOTAL entre grupos disjuntos (seus
    # conjuntos de membros nunca se sobrepoem, entao comparar as tuplas
    # ordenadas nunca empata de verdade a menos que os grupos sejam
    # identicos).
    groups.sort(key=lambda g: g.members)

    stats = {
        "groups": len(groups),
        "direct_recursion": sum(1 for g in groups if g.kind == "direct_recursion"),
        "same_object": sum(1 for g in groups if g.kind == "same_object"),
        "cross_object": sum(1 for g in groups if g.kind == "cross_object"),
        "via_trigger": sum(1 for g in groups if g.via_trigger),
        "edges_considered": len(relevant),
        "edge_types": sorted(edge_types),
    }
    return CycleDetectionResult(groups=groups, stats=stats)
