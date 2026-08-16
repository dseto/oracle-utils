"""Atribuicao de CALL/statement ao subprograma exato que os executa (T-01).

Modulo PURO: nenhuma linha aqui abre conexao, importa driver de banco ou toca
rede -- so estruturas de dados de entrada (linhas de ALL_IDENTIFIERS/
ALL_STATEMENTS ja lidas por quem chama) e funcoes deterministicas sobre elas.
Por isso `IdentifierRow`/`StatementRow` sao dataclasses PROPRIAS deste modulo,
e nao um reaproveitamento de `plsqlflow.extract.PlscopeTreeRow`/
`PlscopeStatementBatchRow` (que ja existem com o mesmo formato, contrato
T-02): importar `extract.py` arrastaria `db.py` -> `oracledb`, uma dependencia
de banco que este modulo se propoe a nao ter. Os campos foram desenhados para
espelhar 1:1 os de `PlscopeTreeRow`/`PlscopeStatementBatchRow` (mesmos nomes,
sem `object_name`/`object_type`, que aqui sao parametros do objeto inteiro em
vez de coluna por linha) -- a conversao no motor da BFS (T-03) e so
reempacotar, nunca remapear.

Base tecnica (docs/plano-depgraph-granular.md, secao 2): ALL_IDENTIFIERS e
ALL_STATEMENTS de UM objeto formam UMA arvore unica de contexto, ligada por
usage_id/usage_context_id (o usage_id do PAI). Os usage_id que faltam em
ALL_IDENTIFIERS sao exatamente os de ALL_STATEMENTS -- os dois conjuntos sao
folhas/nos da mesma arvore, nunca dois espacos independentes.

Duas operacoes:

1. `assign_context`: para cada linha CALL e para cada statement, sobe a
   arvore por usage_context_id ate achar o(s) DEFINITION de PROCEDURE/
   FUNCTION mais proximo(s) (regra 1). Subprograma aninhado vira caminho
   completo `OUTER.INNER` (regra 2, nunca fundido no pai). Sobrecarga (mesmo
   nome com >1 DEFINITION no objeto) recebe sufixo `#<n>` deterministico,
   n = posicao da signature ordenada por linha de declaracao (regra 3). O
   que nao acha nenhum subprograma envolvente pousa num no sintetico --
   nunca e descartado (regra 4).

2. `extract_definitions` + `build_definition_index` + `resolve_call_target`:
   a segunda metade da regra 5. Cada CALL carrega a `signature` da
   DECLARATION/DEFINITION que o COMPILADOR resolveu (nao um nome livre) --
   por isso o join por signature entrega o destino exato mesmo entre objetos
   diferentes e mesmo com sobrecarga (duas DEFINITIONs do mesmo nome tem
   signatures distintas). Quando a signature nao esta no indice (objeto alvo
   ainda nao lido pela travessia, ou sem PL/Scope), o resultado vem marcado
   como NAO RESOLVIDO com a signature original preservada -- nunca inventa
   destino, nunca silencia (quem decide o que fazer com isso, ex.: enfileirar
   o objeto ou registrar ponto cego, e o motor da BFS em T-03, nao este
   modulo).

Criterio __INIT__ vs __SPEC__ (regra 4, escolha documentada aqui porque o
plano so da o exemplo, nao a regra formal): o object_type do OBJETO DE
ORIGEM (a raiz da arvore, usage_context_id=0) decide.
- PACKAGE/TYPE (a spec): nao tem bloco executavel nenhum fora de
  subprogramas -- qualquer CALL/statement sem subprograma envolvente ai so
  pode ser algo preso a uma DECLARATION de nivel de pacote (ex.: valor
  default de uma constante). Pousa em `__SPEC__`.
- PACKAGE BODY/TYPE BODY: tem um bloco de inicializacao executavel
  (BEGIN...END do corpo do pacote, roda na primeira chamada a qualquer
  subprograma dele). CALL/statement sem subprograma envolvente ai e, por
  definicao, esse bloco. Pousa em `__INIT__`.
- Objeto standalone (PROCEDURE/FUNCTION/TRIGGER): normalmente nunca cai
  neste fallback -- o proprio no raiz (usage_context_id=0) JA e uma
  DEFINITION de PROCEDURE/FUNCTION, entao o walk o inclui e acha o
  subprograma envolvente antes de chegar aqui (ver `_enclosing_chain`, que
  processa o no de contexto 0 antes de parar). Se mesmo assim acontecer
  (dado malformado/objeto de tipo inesperado), tratamos como `__INIT__` por
  seguranca: mais provavel ser codigo executavel avulso do que declaracao de
  spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Union

# object_type de origem que nao tem bloco executavel fora de subprogramas --
# ver criterio documentado no docstring do modulo.
_SPEC_OBJECT_TYPES = {"PACKAGE", "TYPE"}

# Tipos de identificador que contam como "subprograma" para efeito de walk
# (regra 1). Deliberadamente so PROCEDURE/FUNCTION -- um TYPE method tambem
# aparece como FUNCTION/PROCEDURE em ALL_IDENTIFIERS, entao ja esta coberto.
_SUBPROGRAM_TYPES = ("PROCEDURE", "FUNCTION")


@dataclass(frozen=True)
class IdentifierRow:
    """Uma linha de ALL_IDENTIFIERS de UM objeto (mesmo formato de
    `plsqlflow.extract.PlscopeTreeRow`, sem object_name/object_type)."""

    usage_id: int
    usage_context_id: int
    line: int
    col: int
    name: str
    type: str
    usage: str
    signature: Optional[str] = None


@dataclass(frozen=True)
class StatementRow:
    """Uma linha de ALL_STATEMENTS de UM objeto, ja unida a arvore de
    contexto (usage_id/usage_context_id -- regra do T-02, sem eles o
    statement fica fora da arvore e nao da pra atribuir)."""

    usage_id: int
    usage_context_id: int
    line: int
    stmt_type: str


@dataclass(frozen=True)
class Assignment:
    """Resultado da atribuicao de uma CALL ou de um statement ao
    subprograma que o envolve."""

    usage_id: int
    line: int
    kind: str  # "CALL" ou "STMT"
    target: str  # nome chamado (CALL) ou tipo do statement (STMT)
    enclosing: str  # caminho do subprograma envolvente, ja com sufixo/sintetico
    signature: Optional[str] = None  # so CALL carrega; usado por resolve_call_target


@dataclass(frozen=True)
class DefinitionEntry:
    """Uma DEFINITION de PROCEDURE/FUNCTION do objeto, pronta para entrar no
    indice inter-objeto de destinos de CALL (regra 5)."""

    signature: str
    owner: str
    object_name: str
    subprogram: str  # caminho completo, com sufixo de overload se aplicavel


@dataclass(frozen=True)
class ResolvedCall:
    """Destino de uma CALL resolvido por signature contra o indice de
    DEFINITIONs (regra 5)."""

    signature: str
    owner: str
    object_name: str
    subprogram: str


@dataclass(frozen=True)
class UnresolvedCall:
    """Marcador explicito de "nao resolvido" (regra 5): a signature da CALL
    nao esta no indice fornecido -- objeto alvo ainda nao lido pela
    travessia, ou sem PL/Scope utilizavel. A signature original (pode ser
    None, se a propria linha CALL nao trouxer uma) e o nome chamado sao
    preservados para quem consome isto decidir o proximo passo; este modulo
    nunca inventa destino nem descarta a chamada."""

    signature: Optional[str]
    called_name: str


def _synthetic_root(object_type: str) -> str:
    """Ver criterio __INIT__ vs __SPEC__ no docstring do modulo."""
    if object_type in _SPEC_OBJECT_TYPES:
        return "__SPEC__"
    return "__INIT__"


def _overload_positions(identifiers: Sequence[IdentifierRow]) -> Dict[str, List[int]]:
    """Nome -> lista de usage_id das DEFINITIONs de PROCEDURE/FUNCTION com
    esse nome no objeto, ordenada por linha de declaracao (regra 3,
    deterministico -- empate por col/usage_id so para nunca depender de
    ordem de insercao). Nomes com uma unica DEFINITION ficam de fora: nao
    recebem sufixo."""
    by_name: Dict[str, List[IdentifierRow]] = {}
    for row in identifiers:
        if row.usage == "DEFINITION" and row.type in _SUBPROGRAM_TYPES:
            by_name.setdefault(row.name, []).append(row)

    positions: Dict[str, List[int]] = {}
    for name, rows in by_name.items():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=lambda r: (r.line, r.col, r.usage_id))
        positions[name] = [r.usage_id for r in ordered]
    return positions


def _suffixed_name(node: IdentifierRow, overload_positions: Dict[str, List[int]]) -> str:
    positions = overload_positions.get(node.name)
    if not positions:
        return node.name
    n = positions.index(node.usage_id) + 1
    return "{}#{}".format(node.name, n)


def _enclosing_chain(
    context_id: int, id_index: Dict[int, IdentifierRow]
) -> List[IdentifierRow]:
    """Sobe usage_context_id a partir de `context_id` (o PAI direto do item
    que estamos atribuindo -- CALL ou statement, nunca incluido aqui) ate a
    raiz da arvore (usage_context_id=0), coletando cada DEFINITION de
    PROCEDURE/FUNCTION encontrada pelo caminho (regra 1). Devolve da mais
    EXTERNA para a mais INTERNA -- e a ordem certa para juntar com "." e
    formar `OUTER.INNER` sem fundir nada (regra 2).

    `seen` protege contra usage_context_id ciclico nos dados de entrada
    (nunca deveria acontecer numa arvore PL/Scope real, mas um walk sem essa
    guarda trava o modulo inteiro num loop infinito diante de fixture
    malformada -- custo baixo, retorno alto)."""
    chain: List[IdentifierRow] = []
    current_id = context_id
    seen: set = set()
    while current_id != 0 and current_id not in seen:
        seen.add(current_id)
        node = id_index.get(current_id)
        if node is None:
            break
        if node.usage == "DEFINITION" and node.type in _SUBPROGRAM_TYPES:
            chain.append(node)
        current_id = node.usage_context_id
    chain.reverse()
    return chain


def _enclosing_path(
    context_id: int,
    id_index: Dict[int, IdentifierRow],
    overload_positions: Dict[str, List[int]],
    object_type: str,
) -> str:
    chain = _enclosing_chain(context_id, id_index)
    if not chain:
        return _synthetic_root(object_type)
    return ".".join(_suffixed_name(node, overload_positions) for node in chain)


def assign_context(
    identifiers: Sequence[IdentifierRow],
    statements: Sequence[StatementRow],
    object_type: str,
) -> List[Assignment]:
    """Atribui cada linha CALL e cada statement ao subprograma que o
    envolve (regras 1-4). Devolve lista ordenada por usage_id (regra 6:
    determinismo -- duas chamadas com a mesma entrada devolvem a mesma
    lista, na mesma ordem)."""
    id_index: Dict[int, IdentifierRow] = {row.usage_id: row for row in identifiers}
    overload_positions = _overload_positions(identifiers)

    assignments: List[Assignment] = []

    for row in identifiers:
        if row.usage != "CALL":
            continue
        enclosing = _enclosing_path(row.usage_context_id, id_index, overload_positions, object_type)
        assignments.append(
            Assignment(
                usage_id=row.usage_id,
                line=row.line,
                kind="CALL",
                target=row.name,
                enclosing=enclosing,
                signature=row.signature,
            )
        )

    for stmt in statements:
        enclosing = _enclosing_path(stmt.usage_context_id, id_index, overload_positions, object_type)
        assignments.append(
            Assignment(
                usage_id=stmt.usage_id,
                line=stmt.line,
                kind="STMT",
                target=stmt.stmt_type,
                enclosing=enclosing,
                signature=None,
            )
        )

    assignments.sort(key=lambda a: a.usage_id)
    return assignments


def extract_definitions(
    identifiers: Sequence[IdentifierRow], owner: str, object_name: str
) -> List[DefinitionEntry]:
    """Material bruto do indice inter-objeto de destinos de CALL (regra 5):
    uma `DefinitionEntry` por DEFINITION de PROCEDURE/FUNCTION do objeto,
    com o caminho completo (aninhado, regra 2) e sufixo de overload (regra
    3) ja resolvidos. DEFINITION sem signature (nunca deveria acontecer numa
    extracao real de ALL_IDENTIFIERS, mas pode acontecer numa fixture
    incompleta) e ignorada aqui -- sem signature ela e inalcancavel por
    `resolve_call_target` de qualquer forma, e nao ha por que indexar algo
    que nunca sera encontrado.

    Devolve ordenado por (signature, owner, object_name, subprogram) --
    regra 6, determinismo: quem funde entries de varios objetos
    (`build_definition_index`) nao depende de ordem de iteracao de nenhum
    set/dict para ser deterministico."""
    id_index: Dict[int, IdentifierRow] = {row.usage_id: row for row in identifiers}
    overload_positions = _overload_positions(identifiers)

    entries: List[DefinitionEntry] = []
    for row in identifiers:
        if row.usage != "DEFINITION" or row.type not in _SUBPROGRAM_TYPES:
            continue
        if not row.signature:
            continue
        ancestors = _enclosing_chain(row.usage_context_id, id_index)
        path_nodes = ancestors + [row]
        subprogram = ".".join(_suffixed_name(node, overload_positions) for node in path_nodes)
        entries.append(
            DefinitionEntry(
                signature=row.signature,
                owner=owner,
                object_name=object_name,
                subprogram=subprogram,
            )
        )

    entries.sort(key=lambda e: (e.signature, e.owner, e.object_name, e.subprogram))
    return entries


def build_definition_index(
    entries: Iterable[DefinitionEntry],
) -> Dict[str, DefinitionEntry]:
    """Funde `DefinitionEntry` de VARIOS objetos (cada `extract_definitions`
    ja devolve as suas ordenadas) num indice unico signature -> destino,
    consumido por `resolve_call_target`. Signature e a chave natural do
    PL/Scope: duas DEFINITIONs distintas (mesmo em objetos diferentes) nunca
    compartilham signature, entao a fusao nunca precisa de logica de
    desempate -- so popula o dict na ordem recebida (ja deterministica, regra
    6, porque cada `extract_definitions` ordena antes de devolver)."""
    index: Dict[str, DefinitionEntry] = {}
    for entry in entries:
        index[entry.signature] = entry
    return index


def resolve_call_target(
    call: IdentifierRow, index: Dict[str, DefinitionEntry]
) -> Union[ResolvedCall, UnresolvedCall]:
    """Resolve o destino de uma linha CALL pelo indice de DEFINITIONs
    (regra 5). NUNCA inventa destino: signature ausente na linha ou ausente
    do indice devolve `UnresolvedCall` com a signature original preservada
    (pode ser None) -- quem chama (T-03/procgraph) decide o que fazer:
    enfileirar o objeto ainda nao lido, ou registrar ponto cego se o alvo
    nunca tera PL/Scope."""
    if call.signature is not None:
        target = index.get(call.signature)
        if target is not None:
            return ResolvedCall(
                signature=target.signature,
                owner=target.owner,
                object_name=target.object_name,
                subprogram=target.subprogram,
            )
    return UnresolvedCall(signature=call.signature, called_name=call.name)
