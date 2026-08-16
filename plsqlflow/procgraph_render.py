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
"conserta" um contador -- quando uma das QUATRO somas nao fecha:

    objetos alcancados = grao subprograma + grao objeto (rebaixado/sem PL-SQL/fronteira) + nao resolvidas
    calls por objeto    = atribuidas a subprograma + atribuidas a __INIT__/__SPEC__ + nao atribuidas (arestas)
    statements lidos    = atribuidos a subprograma + atribuidos a __INIT__/__SPEC__ + sem dependencia por natureza (cada um com aresta ou ponto cego -- nunca so "[]")
    statements malformados = nenhuma aresta nao-CALL com from_ref fora do formato esperado

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
- "calls": classificadas pela FORMA do `from_ref` de cada aresta CALL
  (nunca pelo `to_ref` -- ver "IDENTIDADE DE NO PARA RENDER" abaixo para o
  motivo de `to_ref`/refs de no nao serem confiaveis para este fim).
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
- "statements" (DEFEITO 2, achado ao vivo: o rotulo antigo dizia
  "statements" mas somava ARESTAS -- um statement que nao gerava aresta
  NENHUMA nunca entrava na conta, a soma fechava e a COBERTURA declarava
  consistencia enquanto o SQL dinamico de RUN_DYNAMIC sumia sem rastro,
  DEFEITO 1). O denominador agora e HONESTO: `ProcGraphResult.
  statements_read`, uma lista de `(from_ref, line)` -- um par por STMT que
  `_ProcGraphEngine._process_stmt` de fato despachou ao seam de acesso
  (T-05), gravado ANTES de saber se produziu aresta. `_reconcile_statements_
  read` confere que TODO par tem pelo menos uma aresta nao-CALL com o
  MESMO `(from_ref, line)` -- resolvida (READ/WRITE/STATE_*/TRIGGER_FIRES)
  ou marcadora (DYNAMIC_SQL partial/opaque, que `_dynamic_sql_edges` SEMPRE
  emite, nunca omite). Par sem nenhuma aresta correspondente e a omissao
  silenciosa que este defeito existe para impedir -- levanta `CoverageError`
  imediatamente, nunca "fecha" trivialmente so porque a soma de zero e zero.
  So cobre grao SUBPROGRAMA (`statements_read` so existe onde PL/Scope foi
  lido por statement); o balde de grao OBJETO (fallback, sem PL/Scope para
  atribuir por statement) continua contado por ARESTA -- `_reconcile_edges`
  ainda roda sobre as arestas nao-CALL (agora so para validar refs
  malformadas + contar o balde "objeto"), e o rotulo impresso para essa
  fatia diz "arestas" explicitamente, nunca "statements" (a licao literal
  do defeito: rotulo que promete uma coisa e soma outra e o mesmo tipo de
  omissao que este contrato existe para impedir).
  `edge_type == "CALL"` alimenta a reconciliacao de calls; qualquer OUTRO
  edge_type (READ/WRITE/STATE_READ/STATE_WRITE/TRIGGER_FIRES/DYNAMIC_SQL/
  NO_DATA_DEP, e o que mais o merge do fallback trouxer --
  SYNONYM_RESOLVES_TO) alimenta a de statements -- fronteira natural entre
  "isto e uma chamada" e "isto e um efeito de um comando/estado", coerente
  com T-01 (CALL vs STMT).

============================================================================
SECAO 6-B -- CORRECAO DE DEFEITO BLOQUEANTE (relatorio de verificacao
independente sobre este contrato, 2026-08-16)
============================================================================

ANTES desta correcao, `procgraph_access._table_access_edges` devolvia `[]`
para QUALQUER `stmt_type` fora de SELECT/INSERT/UPDATE/DELETE/MERGE/
dinamico -- e como esta secao (`_reconcile_statements_read`) exigia que
TODO statement lido tivesse pelo menos uma aresta, um UNICO `COMMIT`/
`ROLLBACK`/`FETCH`/`CLOSE` dentro de QUALQUER subprograma da cadeia
derrubava `render_graph` inteiro (`CoverageError`, ZERO arquivos gravados)
-- nao um ponto cego declarado, uma RECUSA TOTAL de gerar qualquer mapa. O
alvo declarado do usuario ("processos gigantes, com centenas de sub
chamadas") e PL/SQL batch/transacional, cheio exatamente destes
statements -- na pratica o modo granular nunca completava para o caso de
uso real.

O `raise` em si NAO era o defeito (existe para pegar o Defeito 1 -- SQL
dinamico sumindo em silencio -- e essa garantia continua intacta). O
defeito era nao distinguir DOIS casos que `procgraph_access.py` agora
separa:

- tipo que, POR NATUREZA, nao tem dependencia de dados (controle
  transacional COMMIT/ROLLBACK/SAVEPOINT/SET TRANSACTION/LOCK TABLE;
  controle de cursor ESTATICO OPEN/FETCH/CLOSE, ver
  `procgraph_access._NO_DEPENDENCY_STMT_TYPES`) -- ganha aresta marcadora
  `NO_DATA_DEP`, balde PROPRIO nesta secao (`statements_no_dependency`),
  contado e visivel, NUNCA causa de `CoverageError`.
- tipo GENUINAMENTE desconhecido -- continua devolvendo `[]`, e esta
  secao continua levantando `CoverageError`, agora com o `stmt_type` na
  mensagem (`ProcGraphResult.statements_read` carrega uma tripla
  `(from_ref, line, stmt_type)`, nao mais um par).

DEFEITO NAO-BLOQUEANTE (mesmo relatorio): a linha de "arestas ... em grao
objeto (fallback, sem PL/Scope disponivel)" contava arestas TRIGGER_FIRES
junto com as de fallback de verdade. `from_ref` de TRIGGER_FIRES e SEMPRE
a tabela (ref de 2 partes) POR DESENHO
(`procgraph_access._discover_triggers`), com ou sem PL/Scope disponivel --
nao e sinal de fallback. `build_coverage` agora separa `trigger_fires_edges`
de `fallback_stmt_edges` ANTES de reconciliar; `edges_trigger_fires` e
`statements_edges_object` sao contados e rotulados em linhas distintas.

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
  gerado aqui: monta-lo exigiria a sintaxe `ALTER <TIPO> ... COMPILE
  PLSCOPE_SETTINGS=...` correta POR TIPO de objeto (PACKAGE precisa de
  duas linhas, TRIGGER de uma, etc. -- ver
  `depgraph_render._recompile_statements`), fora do escopo de prova desta
  tarefa (DEFEITO 4 acrescentou `ProcNode.object_type`, mas so para nos
  `grain="object"` -- nao universal como `DepNode.object_type`, e
  implementar o gerador de script continua fora do escopo desta
  correcao). Os refs de `result.needs_recompile` continuam listados na
  secao COBERTURA (dado visivel, nunca omitido); quem precisa do script
  pronto roda o modo objeto (`python -m plsqlflow depgraph OWNER.OBJETO`,
  sem `--granular`) sobre o mesmo ref -- o `recompile.sql` daquele modo
  cobre o mesmo objeto.
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
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from . import __version__ as _PLSQLFLOW_VERSION
from . import depgraph
from .depgraph_render import sanitize_component
from .procgraph import ProcEdge, ProcGraphResult, ProcNode
from .procgraph_cycles import find_cycles

_VALID_GRAINS = ("subprogram", "object", "unresolved")
_INIT_SPEC_SUBPROGRAMS = ("__INIT__", "__SPEC__")
_CALL_EDGE_TYPE = "CALL"
_TABLE_EDGE_TYPES = ("READ", "WRITE")
_STATE_EDGE_TYPES = ("STATE_READ", "STATE_WRITE")
_TRIGGER_EDGE_TYPE = "TRIGGER_FIRES"
# Correcao do DEFEITO BLOQUEANTE (relatorio de verificacao independente,
# 2026-08-16): edge_type sintetico emitido por
# `procgraph_access._no_dependency_edge` para statement cujo tipo NAO tem
# dependencia de dados por natureza (controle transacional COMMIT/
# ROLLBACK/SAVEPOINT/SET TRANSACTION/LOCK TABLE; controle de cursor
# ESTATICO OPEN/FETCH/CLOSE) -- balde PROPRIO da reconciliacao de
# statements, nunca misturado com READ/WRITE/STATE_*/TRIGGER_FIRES/
# DYNAMIC_SQL (que implicam dependencia de dado real) nem com o fallback
# de grao objeto.
_NO_DATA_DEP_EDGE_TYPE = "NO_DATA_DEP"

_CAPABILITY_NAMES = ("resolve_owner", "object_wrapped", "triggers", "source")

_CAPABILITY_LABELS = {
    "resolve_owner": "resolve_owner (resolucao de CALL inter-package por signature)",
    "object_wrapped": "object_wrapped (deteccao de objeto PL/SQL wrapped)",
    "triggers": "triggers (descoberta de trigger de tabela escrita)",
    "source": "source (texto-fonte para resolver/classificar SQL dinamico)",
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
    "source": (
        "SQL dinamico (EXECUTE IMMEDIATE/OPEN ... FOR/DBMS_SQL.PARSE) nunca resolve nem "
        "classifica 'partial' sem o texto-fonte -- toda ocorrencia cai em 'opaque' "
        "declarada (aresta marcadora + PONTOS CEGOS, nunca omitida), mas o mapa fica mais "
        "raso do que o processo real."
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
    # Statements LIDOS (denominador honesto, ver docstring do modulo,
    # secao 6 -- DEFEITO 2): so grao subprograma, cada um ja PROVADO
    # representado por `build_coverage` (senao teria levantado
    # `CoverageError` antes de chegar aqui). `statements_read_subprogram`/
    # `_init_spec` contam so statement com dependencia de dado REAL
    # (READ/WRITE/STATE_*/TRIGGER_FIRES/DYNAMIC_SQL) -- statement sem
    # dependencia por natureza entra em `statements_no_dependency`
    # (balde proprio, correcao do DEFEITO BLOQUEANTE, ver docstring do
    # modulo secao 6-B).
    statements_read_subprogram: int
    statements_read_init_spec: int
    statements_no_dependency: int
    statements_read_total: int
    # Arestas nao-CALL em grao objeto (fallback, sem PL/Scope para
    # atribuir por statement) -- rotulo deliberadamente "arestas", nunca
    # "statements" (a licao do defeito: nao contar aresta e chamar de
    # statement). NUNCA inclui TRIGGER_FIRES (ver `edges_trigger_fires`
    # abaixo -- correcao do DEFEITO NAO-BLOQUEANTE 1 do mesmo relatorio).
    statements_edges_object: int
    # Arestas TRIGGER_FIRES (tabela -> trigger) -- `from_ref` e SEMPRE a
    # tabela (ref de 2 partes) POR DESENHO (`_discover_triggers`,
    # procgraph_access.py), com ou sem PL/Scope disponivel. Contadas
    # SEPARADAS de `statements_edges_object`: antes desta correcao as
    # duas ficavam somadas sob o rotulo "fallback, sem PL/Scope" mesmo
    # quando o objeto tinha PL/Scope 100% disponivel (defeito
    # nao-bloqueante confirmado na saida real de
    # GESTAO.FLOW_DEMO.MAIN/INDEX.md: "= 1 arestas" atribuido a fallback
    # com fallback_objects: 0).
    edges_trigger_fires: int
    objects_subprogram: int
    objects_object: int
    # DEFEITO 4 (relatorio de verificacao independente, 2026-08-16): TRES
    # baldes que a versao anterior misturava sob um so rotulo ("Rebaixados
    # para grao objeto"), verdade sobre O QUE cada um conta:
    #
    # - `reasons`: objeto PL/SQL de verdade (object_type em
    #   `depgraph.PLSQL_OBJECT_TYPES`) rebaixado para grao objeto por
    #   falta de PL/Scope completo ou por ser *wrapped* -- o UNICO balde
    #   onde "rebaixado" e uma resposta correta, e o UNICO onde a
    #   pergunta "por que" se aplica. `note` sempre preenchido (correcao
    #   do "sem motivo registrado" em 26 de 27 objetos, ver
    #   `_ProcGraphEngine._object_grain_reason`/`_sync_fallback_results`).
    # - `no_plsql_objects`: objeto SEM PL/SQL nenhum (TABLE/VIEW/SEQUENCE/
    #   etc.) -- nunca foi candidato a grao subprograma, "rebaixado" nao
    #   se aplica (achado ao vivo: GESTAO_OO.TB_PROJETOS apareceu sob
    #   "Rebaixados", uma tabela nunca poderia ter sido).
    # - `frontier_objects`: fronteira de schema (`ProcNode.is_frontier`,
    #   owner em stop_schemas ou objeto com prefixo DBMS_/UTL_ -- ex.:
    #   SYS.STANDARD, PUBLIC.DBMS_LOB, PUBLIC.DBMS_ASSERT) -- limite
    #   DELIBERADO da travessia, nunca um rebaixamento; aparecia com
    #   `(leaf)` na lista de nos ao mesmo tempo que a linha "objetos
    #   alcancados" dizia "0 folhas de fronteira", contradicao que essa
    #   separacao resolve (o numero real agora aparece aqui).
    reasons: List[str] = field(default_factory=list)
    no_plsql_objects: List[str] = field(default_factory=list)
    frontier_objects: List[str] = field(default_factory=list)
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


def _reconcile_statements_read(
    statements_read: Sequence[Tuple[str, int, str]], edges: Sequence[ProcEdge]
) -> Tuple[int, int, int, List[str]]:
    """Denominador HONESTO da secao "statements" (DEFEITO 2, ver docstring
    do modulo secao 6): `statements_read` vem de `_ProcGraphEngine.
    _process_stmt` -- uma tripla `(from_ref, line, stmt_type)` por STMT de
    fato despachado ao seam de acesso, gravada ANTES de saber se produziu
    aresta. Cada tripla precisa bater com pelo menos uma aresta NAO-CALL
    no MESMO `(from_ref, line)` -- resolvida (READ/WRITE/STATE_*/
    TRIGGER_FIRES), marcadora (DYNAMIC_SQL partial/opaque, que
    `procgraph_access._dynamic_sql_edges` SEMPRE emite, nunca omite) ou
    "sem dependencia por natureza" (`NO_DATA_DEP`, correcao do DEFEITO
    BLOQUEANTE seguinte -- `procgraph_access._no_dependency_edge` SEMPRE
    emite para COMMIT/ROLLBACK/SAVEPOINT/SET TRANSACTION/LOCK TABLE/OPEN
    estatico/FETCH/CLOSE, nunca omite).

    Devolve `(count_subprogram, count_init_spec, count_no_dependency,
    missing)` -- um statement cuja UNICA aresta correspondente e
    `NO_DATA_DEP` entra so em `count_no_dependency` (balde proprio, nunca
    somado a `count_subprogram`/`count_init_spec`, que implicam
    dependencia de dado real). `missing` e a lista de statements SEM
    nenhuma aresta correspondente (a omissao silenciosa que o DEFEITO 2
    existe para impedir), cada entrada nomeando o `stmt_type` -- e assim
    que um tipo GENUINAMENTE desconhecido aparece na mensagem de
    `CoverageError` (correcao do DEFEITO BLOQUEANTE: o sinal alto so
    sobrevive para este caso, nao para COMMIT/ROLLBACK/FETCH/CLOSE, que
    agora tem aresta propria). `build_coverage` e quem decide levantar
    `CoverageError` quando `missing` nao esta vazio (esta funcao so
    reconcilia, nunca "conserta" o numero nem lanca)."""
    edge_types_by_key: Dict[Tuple[str, Optional[int]], Set[str]] = {}
    for e in edges:
        if e.edge_type == _CALL_EDGE_TYPE:
            continue
        edge_types_by_key.setdefault((e.from_ref, e.line), set()).add(e.edge_type)

    count_subprogram = 0
    count_init_spec = 0
    count_no_dependency = 0
    missing: List[str] = []
    for ref, line, stmt_type in statements_read:
        bucket = _classify_from_ref(ref)
        edge_types = edge_types_by_key.get((ref, line))
        if bucket is None or not edge_types:
            missing.append(
                "{} L{} [tipo={}]{}".format(
                    ref,
                    line,
                    stmt_type or "?",
                    "" if bucket is not None else " (ref malformado)",
                )
            )
            continue
        if edge_types <= {_NO_DATA_DEP_EDGE_TYPE}:
            # SO aresta(s) NO_DATA_DEP para este (ref, line) -- statement
            # sem dependencia de dados por natureza, balde proprio (nunca
            # misturado com "atribuido a subprograma/__INIT__/__SPEC__",
            # que implica dependencia de dado real).
            count_no_dependency += 1
        elif bucket == "init_spec":
            count_init_spec += 1
        else:
            count_subprogram += 1
    return count_subprogram, count_init_spec, count_no_dependency, missing


def build_coverage(
    result: ProcGraphResult, capabilities: Optional[Dict[str, Optional[bool]]] = None
) -> CoverageReport:
    """Reconciliacao de contadores da secao 6 do plano. Levanta
    `CoverageError` (nunca "conserta" o numero) quando qualquer uma das
    QUATRO somas nao fecha -- ver docstring do modulo para o desenho exato
    de cada balde. Chamada por `render_graph` ANTES de qualquer escrita em
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
    # DEFEITO NAO-BLOQUEANTE 1 (mesmo relatorio): TRIGGER_FIRES sai do
    # balde de fallback ANTES de reconciliar -- `from_ref` de TRIGGER_FIRES
    # e SEMPRE a tabela (ref de 2 partes) POR DESENHO
    # (`procgraph_access._discover_triggers`), com ou sem PL/Scope
    # disponivel; misturar as duas fazia o rotulo "fallback, sem PL/Scope"
    # mentir toda vez que um trigger disparava com PL/Scope 100%
    # disponivel (confirmado na saida real de
    # GESTAO.FLOW_DEMO.MAIN/INDEX.md).
    trigger_fires_edges = [e for e in stmt_edges if e.edge_type == _TRIGGER_EDGE_TYPE]
    fallback_stmt_edges = [e for e in stmt_edges if e.edge_type != _TRIGGER_EDGE_TYPE]
    # Reconcilia os dois grupos separadamente -- cada um continua validando
    # from_ref malformado (mesma checagem de antes); so o balde "objeto" de
    # `fallback_stmt_edges` alimenta `statements_edges_object` (fallback de
    # verdade, sem PL/Scope). "subprograma"/"__INIT__/__SPEC__" de nenhum
    # dos dois grupos alimenta o relatorio (ver `_reconcile_statements_read`
    # abaixo para o denominador honesto).
    stmt_edge_counts = _reconcile_edges(fallback_stmt_edges, "statements")
    # `_reconcile_edges` so serve aqui para validar from_ref malformado com
    # a MESMA regra dura do resto do modulo (nunca "consertar" um numero) --
    # `from_ref` de TRIGGER_FIRES e sempre a tabela (2 partes) por desenho,
    # entao os buckets em si nao importam, so a contagem total.
    _reconcile_edges(trigger_fires_edges, "statements (trigger_fires)")
    edges_trigger_fires = len(trigger_fires_edges)

    stmt_read_subprogram, stmt_read_init_spec, stmt_no_dependency, missing_statements = (
        _reconcile_statements_read(result.statements_read, edges)
    )
    if missing_statements:
        raise CoverageError(
            "COBERTURA nao fecha (statements): {} statement(s) lido(s) via PL/Scope sem "
            "nenhuma aresta correspondente (nem READ/WRITE/STATE_*/TRIGGER_FIRES/"
            "DYNAMIC_SQL/NO_DATA_DEP) -- denominador honesto vem de "
            "ProcGraphResult.statements_read; omissao silenciosa: {}".format(
                len(missing_statements), "; ".join(sorted(missing_statements))
            )
        )

    objects_subprogram = {(n.owner, n.object_name) for n in nodes if n.grain == "subprogram"}
    objects_object = {(n.owner, n.object_name) for n in nodes if n.grain == "object"}

    # DEFEITO 4: TRES baldes para grain="object" -- ver docstring de
    # `CoverageReport.reasons`/`no_plsql_objects`/`frontier_objects`.
    # `ProcNode.object_type is None` (ex.: `ProcGraphResult` construido a
    # mao em teste, sem passar por `_sync_fallback_results`) cai
    # conservadoramente em `reasons` -- mesmo comportamento de ANTES desta
    # correcao (nunca classifica errado por falta de dado, so por dado
    # presente e claro).
    reasons: List[str] = []
    no_plsql_objects: List[str] = []
    frontier_objects: List[str] = []
    for n in nodes:
        if n.grain != "object":
            continue
        label = "{}.{}".format(n.owner, n.object_name)
        if n.is_frontier:
            suffix = " [{}]".format(n.object_type) if n.object_type else ""
            frontier_objects.append("{}{}".format(label, suffix))
        elif n.object_type is not None and n.object_type not in depgraph.PLSQL_OBJECT_TYPES:
            no_plsql_objects.append("{}: {}".format(label, n.object_type))
        else:
            reasons.append("{}: {}".format(label, n.note or "sem motivo registrado"))
    reasons.sort()
    no_plsql_objects.sort()
    frontier_objects.sort()

    # DEFEITO 3: mesma separacao builtin/generico aplicada aqui -- um
    # UNKNOWN.UNKNOWN.<BUILTIN> nao precisa de investigacao humana, entao
    # nao compete por atencao na lista de alvos genuinamente nao
    # resolvidos (a secao "## Builtins SQL/PLSQL" do INDEX.md, alimentada
    # por `result.builtin_calls`, ja cobre esses casos separadamente).
    unresolved_targets = sorted(
        "{}.{}.{}: {}".format(n.owner, n.object_name, n.subprogram, n.note or "sem motivo registrado")
        for n in nodes
        if n.grain == "unresolved" and not n.is_builtin
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
        statements_read_subprogram=stmt_read_subprogram,
        statements_read_init_spec=stmt_read_init_spec,
        statements_no_dependency=stmt_no_dependency,
        statements_read_total=stmt_read_subprogram + stmt_read_init_spec + stmt_no_dependency,
        statements_edges_object=stmt_edge_counts["object"],
        edges_trigger_fires=edges_trigger_fires,
        objects_subprogram=len(objects_subprogram),
        objects_object=len(objects_object),
        reasons=reasons,
        no_plsql_objects=no_plsql_objects,
        frontier_objects=frontier_objects,
        unresolved_targets=unresolved_targets,
        needs_recompile=sorted(set(result.needs_recompile)),
        capabilities=capabilities,
    )


def capabilities_from_extractor(extractor: Any) -> Dict[str, bool]:
    """`{"resolve_owner": bool, "object_wrapped": bool, "triggers": bool,
    "source": bool}` via `hasattr` puro -- mesmo criterio de opcionalidade
    que `procgraph.py`/`procgraph_access.py` usam para checar estes
    metodos em tempo de execucao. Uso tipico: `meta_params["capabilities"]
    = capabilities_from_extractor(extractor)` antes de chamar
    `render_graph`."""
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
        # DEFEITO 4 (relatorio de verificacao independente, 2026-08-16):
        # o rotulo antigo chamava o terceiro termo de "folhas de
        # fronteira/nao resolvidas", conflando duas coisas diferentes --
        # `nodes_unresolved` e SO sobre CALL sem alvo determinavel, nunca
        # sobre fronteira de schema (SYS.STANDARD/DBMS_*/UTL_*, que fica
        # em `nodes_object` -- ver `## COBERTURA` abaixo, secao "Fronteira
        # de schema"). Contradicao achada ao vivo: um objeto de fronteira
        # aparecia `(leaf)` na lista de nos ao mesmo tempo que esta linha
        # dizia "0 folhas de fronteira". Rotulo agora descreve so o que o
        # numero de fato conta.
        "objetos alcancados = {} grao subprograma ({} objetos distintos) + {} grao "
        "objeto ({} objetos distintos, classificacao abaixo) + {} nao resolvidas "
        "(CALL sem alvo determinavel) = {} nos".format(
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
        "statements lidos (PL/Scope, denominador honesto) = {} atribuidos a subprograma + "
        "{} atribuidos a __INIT__/__SPEC__ + {} sem dependencia de dados por natureza "
        "(controle transacional/cursor) = {} statements -- cada um com pelo menos uma "
        "aresta correspondente (READ/WRITE/STATE_*/TRIGGER_FIRES/DYNAMIC_SQL/NO_DATA_DEP); "
        "statement lido sem nenhuma aresta correspondente levanta CoverageError (nunca "
        "fecha em silencio, nunca aborta a geracao inteira em silencio)".format(
            coverage.statements_read_subprogram,
            coverage.statements_read_init_spec,
            coverage.statements_no_dependency,
            coverage.statements_read_total,
        )
    )
    lines.append(
        "  statements sem dependencia de dados por natureza (controle transacional: "
        "COMMIT/ROLLBACK/SAVEPOINT/SET TRANSACTION/LOCK TABLE; cursor estatico: OPEN/"
        "FETCH/CLOSE) = {} -- contados e visiveis aqui, NUNCA invisiveis, NUNCA causa de "
        "CoverageError (ausencia de READ/WRITE e a resposta correta para estes tipos, "
        "nao uma lacuna)".format(coverage.statements_no_dependency)
    )
    lines.append(
        "arestas de acesso/estado/SQL dinamico em grao objeto (fallback, sem PL/Scope "
        "disponivel para atribuir por statement) = {} arestas".format(
            coverage.statements_edges_object
        )
    )
    lines.append(
        "arestas TRIGGER_FIRES (tabela -> trigger, from_ref de 2 partes POR DESENHO -- "
        "NAO e sinal de fallback nem de PL/Scope ausente) = {} arestas".format(
            coverage.edges_trigger_fires
        )
    )
    lines.append("")

    # DEFEITO 4: TRES secoes distintas em vez de uma so "Rebaixados" --
    # cada balde tem um rotulo que diz a verdade sobre o que conta (ver
    # docstring de `CoverageReport`).
    if coverage.reasons:
        lines.append(
            "### Rebaixados para grao objeto ({} objetos PL/SQL sem PL/Scope completo "
            "ou wrapped -- motivo individual por objeto)".format(len(coverage.reasons))
        )
        lines.extend("- {}".format(r) for r in coverage.reasons)
        lines.append("")

    if coverage.no_plsql_objects:
        lines.append(
            "### Sem PL/SQL ({} objetos -- TABLE/VIEW/SEQUENCE/etc.; NUNCA foram "
            "candidatos a grao subprograma, a pergunta 'por que rebaixado' nao se "
            "aplica)".format(len(coverage.no_plsql_objects))
        )
        lines.extend("- {}".format(r) for r in coverage.no_plsql_objects)
        lines.append("")

    if coverage.frontier_objects:
        lines.append(
            "### Fronteira de schema ({} objetos -- fora do escopo rastreado por "
            "desenho: owner em stop_schemas ou objeto com prefixo DBMS_/UTL_; NUNCA "
            "rebaixados, sao limite deliberado da travessia, nao uma deficiencia)".format(
                len(coverage.frontier_objects)
            )
        )
        lines.extend("- {}".format(r) for r in coverage.frontier_objects)
        lines.append("")

    if coverage.unresolved_targets:
        lines.append(
            "### Nao resolvidos ({} CALLs sem alvo determinavel -- alvo genuinamente "
            "fora do escopo visivel ou sem PL/Scope; builtin de SYS.STANDARD NAO entra "
            "aqui, ver secao Builtins abaixo)".format(len(coverage.unresolved_targets))
        )
        lines.extend("- {}".format(r) for r in coverage.unresolved_targets)
        lines.append("")

    if coverage.needs_recompile:
        lines.append("### Recompilaveis (needs_recompile)")
        lines.append(
            "- sem recompile.sql pronto neste modo (fora do escopo desta tarefa -- "
            "`ALTER <TIPO> ... COMPILE PLSCOPE_SETTINGS=...` tem sintaxe especifica por "
            "tipo de objeto, ex.: PACKAGE precisa de duas linhas, TRIGGER de uma); "
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


def _render_dynsql_lines(outbound: Sequence[ProcEdge]) -> List[str]:
    """Secao `## SQL Dinamico` do node .md -- equivalente granular do
    `_render_dynsql_lines` de `depgraph_render.py`.

    Existe porque sem ela o node .md MENTE por omissao. A aresta
    DYNAMIC_SQL de nivel `exact` (literal resolvido) nao e ponto cego,
    entao nao entra na secao PONTOS CEGOS do INDEX; e nao e READ/WRITE,
    entao nao entra em `## Tabelas acessadas`. Sem esta secao ela existiria
    APENAS em edges.jsonl, e quem lesse o arquivo do subprograma veria um
    no aparentemente sem efeito nenhum -- foi exatamente o que a primeira
    execucao ao vivo mostrou em GESTAO.FLOW_DEMO.RUN_DYNAMIC, que executa
    dois EXECUTE IMMEDIATE e aparecia mudo.

    O nivel (`confidence`: exact/partial/opaque) vai explicito na linha: e
    a diferenca entre "este alvo esta provado" e "este alvo e um palpite
    que alguem precisa conferir a mao antes de migrar".
    """
    entries = sorted(
        (e for e in outbound if e.edge_type == "DYNAMIC_SQL"),
        key=lambda e: (e.line if e.line is not None else -1, e.to_ref),
    )
    lines = []
    for e in entries:
        line_label = "L{}".format(e.line) if e.line is not None else "L?"
        lines.append("- {} [{}] -> {}".format(line_label, e.confidence, e.to_ref))
    return lines


def render_node_md(node: ProcNode, ref: str, outbound: Sequence[ProcEdge], inbound: Sequence[ProcEdge]) -> str:
    lines: List[str] = ["# {}".format(ref), _format_metadata_line(node), ""]

    sections: List[Tuple[str, List[str]]] = [
        ("## Chama (outbound)", _render_call_out_lines(outbound)),
        ("## Chamado por (inbound)", _render_call_in_lines(inbound)),
        ("## Tabelas acessadas", _render_table_lines(outbound, inbound)),
        ("## Estado de package", _render_state_lines(outbound)),
        ("## Triggers ativados", _render_trigger_lines(outbound, inbound)),
        ("## SQL Dinâmico", _render_dynsql_lines(outbound)),
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
        if node.is_builtin:
            # DEFEITO 3: no UNKNOWN.UNKNOWN.<BUILTIN> identificavel como
            # builtin diretamente na lista de nos, sem precisar cruzar com
            # a secao Builtins abaixo.
            marker += " (builtin)"
        lines.append("- {} [grao:{}]{}".format(node_ref(node), node.grain, marker))
    lines.append("")

    lines.extend(_coverage_lines(coverage))
    lines.extend(_cycles_lines(result))

    # DEFEITO 2 (relatorio de verificacao independente, 2026-08-16): a
    # lista aqui embaixo E a fonte da verdade -- `## Estatisticas` acima
    # usa `result.stats["blind_spots"]`, que `_ProcGraphEngine.to_result`
    # agora calcula do MESMO `sorted(set(...))` usado aqui (ver docstring
    # de `ProcGraphResult.builtin_calls`), entao os dois numeros SEMPRE
    # batem por construcao -- nao ha mais dois calculos independentes do
    # mesmo fato.
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
    lines.append("")

    # DEFEITO 3: builtin de SYS.STANDARD (NVL/ROUND/SUBSTR/SYSDATE/
    # TO_CHAR/CHR/TRIM/GREATEST/LEAST/SQLERRM/...) sai da secao PONTOS
    # CEGOS -- onde a esmagadora maioria das entradas afogava o SQL
    # dinamico de verdade (achado ao vivo contra GESTAO_OO: das 20
    # entradas listadas, quase todas eram builtin) -- e ganha secao
    # PROPRIA. Continua CONTADA e VISIVEL aqui (a regra anti-omissao vale
    # igual para builtin -- so para de competir por atencao com o que
    # exige acao humana).
    # DEFEITO 2, mesmo principio aplicado aqui: a contagem da linha-resumo
    # e a lista abaixo vem do MESMO `sorted(set(...))` -- nunca dois
    # calculos independentes do mesmo fato.
    builtin_calls = sorted(set(result.builtin_calls))
    lines.append("## Builtins SQL/PLSQL (nao expandidos)")
    lines.append(
        "chamadas a builtin SQL/PLSQL nao expandidas: {} (SYS.STANDARD -- semantica "
        "conhecida e documentada, NAO e ponto cego de migracao)".format(len(builtin_calls))
    )
    if builtin_calls:
        lines.extend("- {}".format(spot) for spot in builtin_calls)
    else:
        lines.append("- nenhuma")

    return _finalize(lines)


# --------------------------------------------------------------------------
# meta.json
# --------------------------------------------------------------------------


def compute_chain_hash(nodes: Sequence[ProcNode]) -> str:
    """Mesma tecnica de `depgraph_render.compute_chain_hash` (SHA-256 sobre
    lista ordenada, sem relogio) adaptada aos campos de `ProcNode`
    (`owner`/`object_name`/`subprogram`/`grain`/`note`). Nao inclui
    `object_type`/`is_frontier`/`is_builtin` (DEFEITO 4/3, acrescentados
    a `ProcNode` depois desta funcao existir) nem `last_ddl_time` (que
    `ProcNode`, ao contrario de `DepNode`, nunca carrega) -- alterar o
    conjunto de campos do hash e uma mudanca de contrato separada, fora
    do escopo desta correcao."""
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
