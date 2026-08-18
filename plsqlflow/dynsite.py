"""Categoria provada pela FORMA do sitio de SQL dinamico (T-01, contrato
dynsql-dossie).

Modulo PURO: nenhuma linha aqui abre conexao, executa o SQL analisado ou
toca rede -- so texto-fonte ja lido por quem chama (mesmo padrao de
`plsqlflow/attribute.py` e `plsqlflow/lexical.py`: dataclass `frozen=True`,
funcoes deterministicas sobre estrutura de dados de entrada).

Regra central (docs/backlog-sql-dinamico-estatico.md, secao 4.1): a FORMA do
sitio de execucao (`OPEN ... FOR` / `EXECUTE IMMEDIATE` / `DBMS_SQL.PARSE`)
restringe, pelas regras da propria linguagem PL/SQL, o que o fragmento PODE
ser -- independente do que o texto do fragmento diz. Por isso este modulo
NUNCA olha para dentro de uma string literal nem para o valor de uma
variavel: so para a sintaxe do proprio statement de execucao, entre o
`OPEN`/`EXECUTE IMMEDIATE`/`DBMS_SQL.PARSE` e o `;` que fecha.

1. `OPEN cur FOR <expr>` so aceita `SELECT` -- a linguagem exige um cursor
   aberto sobre uma query. Categoria `QUERY`, nunca invoca procedure.

2. `EXECUTE IMMEDIATE <expr> BULK COLLECT INTO ...` so aceita query
   multi-linha (a colecao recebe N linhas). Categoria `QUERY_MULTI_LINHA`.
   Checada ANTES da regra 3 porque a clausula `BULK COLLECT INTO` CONTEM a
   palavra `INTO` -- checar `INTO` solto primeiro classificaria isto como
   `QUERY_LINHA_UNICA` por engano.

3. `EXECUTE IMMEDIATE <expr> INTO a, b, c` (sem `BULK COLLECT`) so aceita
   query de linha unica -- `into_arity` e o numero de destinos separados por
   virgula. Categoria `QUERY_LINHA_UNICA`.

4. `EXECUTE IMMEDIATE <expr> USING ...` com pelo menos um bind `OUT` ou
   `IN OUT` (a regex `\bOUT\b` casa os dois, porque `IN OUT` contem `OUT`
   como token proprio) so e valido para bloco anonimo ou DML com
   `RETURNING` -- e por isso PODE esconder chamada de procedure. Categoria
   `BLOCO_OU_RETURNING`.

5. Regra de desempate, SEM excecao (mandato do contrato): quando a forma NAO
   decide -- `EXECUTE IMMEDIATE` nu (sem `INTO`/`USING`), `USING` so com
   binds `IN` (bind `IN` nao restringe forma, so passa valor de entrada), ou
   `DBMS_SQL.PARSE` (a API espalha string/binds/execucao em chamadas
   diferentes; o `PARSE` sozinho nao prova nada) -- a categoria e sempre
   `NAO_DETERMINAVEL` e `pode_invocar_procedure=True`. Nunca o contrario, e
   nunca inferido do conteudo do fragmento (proibido pelo contrato: se decidir
   uma categoria exigisse olhar dentro das aspas, isso pertence a T-04
   (procedencia), nao a este modulo).

6. `em_loop`: verdadeiro quando o sitio esta lexico-sintaticamente dentro de
   um `LOOP ... END LOOP` (qualquer variante -- `LOOP` basico, `FOR ... LOOP`,
   `WHILE ... LOOP` compartilham o mesmo par de palavras-chave de abertura/
   fechamento). Calculado por balanco de `LOOP`/`END LOOP` sobre texto ja
   limpo de comentario/literal (`lexical.strip_comments_and_literals`), do
   inicio do subprograma envolvente (heuristica: ultima linha `PROCEDURE`/
   `FUNCTION` antes do sitio, ou inicio do texto fornecido se nenhuma) ate a
   linha do sitio, inclusive. Um `EXECUTE IMMEDIATE 'SELECT ... LOOP ...'`
   literal nunca contaminaria essa contagem mesmo sem o strip: a propria
   forma da regra central impede que o texto do fragmento seja considerado,
   e o strip remove a string de qualquer forma (defesa em profundidade).

A remocao de comentario/literal via `strip_comments_and_literals` tem um
efeito colateral usado deliberadamente por este modulo: cada literal vira UM
espaco (nao o texto original), entao um `;` DENTRO de uma string dinamica
nunca aparece no texto limpo -- o primeiro `;` encontrado ao juntar linhas a
partir do sitio e sempre o `;` real que fecha o statement PL/SQL. Isso evita
reimplementar o scanner `in_string` char-a-char que `dynsql.py` precisa (ali
o texto ORIGINAL da string importa, para reconstruir o SQL; aqui so a FORMA
do statement importa, entao o texto ja limpo basta). `_gather_statement_
window` abaixo e uma reimplementacao PROPRIA e minima deste modulo -- nao
importa `dynsql._gather_statement` (funcao privada de outro modulo).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .lexical import strip_comments_and_literals

# exec_kind
EXEC_KIND_OPEN_FOR = "OPEN_FOR"
EXEC_KIND_EXECUTE_IMMEDIATE = "EXECUTE_IMMEDIATE"
EXEC_KIND_DBMS_SQL_PARSE = "DBMS_SQL_PARSE"

# categoria_provada
CATEGORIA_QUERY = "QUERY"
CATEGORIA_QUERY_LINHA_UNICA = "QUERY_LINHA_UNICA"
CATEGORIA_QUERY_MULTI_LINHA = "QUERY_MULTI_LINHA"
CATEGORIA_BLOCO_OU_RETURNING = "BLOCO_OU_RETURNING"
CATEGORIA_NAO_DETERMINAVEL = "NAO_DETERMINAVEL"

_OPEN_FOR_RE = re.compile(r"\bOPEN\s+\S+\s+FOR\b", re.IGNORECASE)
_EXECUTE_IMMEDIATE_RE = re.compile(r"\bEXECUTE\s+IMMEDIATE\b", re.IGNORECASE)
_DBMS_SQL_PARSE_RE = re.compile(r"\bDBMS_SQL\s*\.\s*PARSE\s*\(", re.IGNORECASE)

_BULK_COLLECT_INTO_RE = re.compile(r"\bBULK\s+COLLECT\s+INTO\b", re.IGNORECASE)
_INTO_RE = re.compile(r"\bINTO\b", re.IGNORECASE)
_USING_RE = re.compile(r"\bUSING\b", re.IGNORECASE)
_OUT_BIND_RE = re.compile(r"\bOUT\b", re.IGNORECASE)

_SUBPROGRAM_START_RE = re.compile(r"^\s*(PROCEDURE|FUNCTION)\s+\w+", re.IGNORECASE)
_LOOP_TOKEN_RE = re.compile(r"\b(END\s+LOOP|LOOP)\b", re.IGNORECASE)


@dataclass(frozen=True)
class DynSiteForm:
    """Resultado da classificacao de UM sitio de SQL dinamico. Carrega a
    PROVA (`categoria_prova`), nunca so o enum -- regra do contrato: uma
    conclusao sem a prova nao e citavel no dossie final."""

    line: int
    exec_kind: str  # "OPEN_FOR" | "EXECUTE_IMMEDIATE" | "DBMS_SQL_PARSE"
    categoria_provada: str
    categoria_prova: str
    pode_invocar_procedure: bool
    pode_invocar_procedure_determinavel: bool  # False quando NAO_DETERMINAVEL
    into_arity: Optional[int]
    em_loop: bool


@dataclass(frozen=True)
class _Classification:
    """Resultado intermediario privado -- so o par categoria+prova e os dois
    campos derivados dela, antes de `line`/`exec_kind`/`em_loop` serem
    anexados pelo chamador publico."""

    categoria: str
    prova: str
    pode_invocar_procedure: bool
    determinavel: bool
    into_arity: Optional[int]


def _index_clean_lines(source_lines: Sequence[str]) -> Dict[int, str]:
    clean_lines, _literals = strip_comments_and_literals(source_lines)
    return {index + 1: text for index, text in enumerate(clean_lines)}


def _gather_statement_window(by_line: Dict[int, str], start_line: int) -> str:
    """Junta linhas a partir de `start_line` ate o primeiro `;` (o texto ja
    esta limpo de comentario/literal -- ver docstring do modulo para por que
    isso torna seguro procurar `;` sem rastrear `in_string`). Reimplementacao
    minima e propria deste modulo (nao importa funcao privada de outro
    modulo)."""
    if not by_line:
        return ""
    parts: List[str] = []
    max_line = max(by_line)
    line_no = start_line
    while line_no <= max_line:
        text = by_line.get(line_no, "")
        semi_idx = text.find(";")
        if semi_idx != -1:
            parts.append(text[: semi_idx + 1])
            break
        parts.append(text)
        line_no += 1
    return " ".join(parts)


def _detect_exec_kind(stmt_text: str, line: int) -> str:
    if _OPEN_FOR_RE.search(stmt_text):
        return EXEC_KIND_OPEN_FOR
    if _EXECUTE_IMMEDIATE_RE.search(stmt_text):
        return EXEC_KIND_EXECUTE_IMMEDIATE
    if _DBMS_SQL_PARSE_RE.search(stmt_text):
        return EXEC_KIND_DBMS_SQL_PARSE
    raise ValueError(
        "linha {} nao contem sitio de SQL dinamico reconhecido "
        "(OPEN ... FOR / EXECUTE IMMEDIATE / DBMS_SQL.PARSE): {!r}".format(
            line, stmt_text.strip()
        )
    )


def _segment_arity(after_marker: str) -> int:
    """Conta os destinos separados por virgula entre o fim de uma clausula
    `INTO`/`BULK COLLECT INTO` e o que vem depois (`USING` ou o `;` que
    fecha o statement)."""
    using_match = _USING_RE.search(after_marker)
    segment = after_marker[: using_match.start()] if using_match else after_marker
    segment = segment.split(";", 1)[0].strip()
    if not segment:
        return 0
    return len([token for token in segment.split(",") if token.strip()])


def _classify_open_for() -> _Classification:
    return _Classification(
        categoria=CATEGORIA_QUERY,
        prova="OPEN ... FOR so aceita query (SELECT) -- a linguagem exige um cursor aberto sobre um SELECT",
        pode_invocar_procedure=False,
        determinavel=True,
        into_arity=None,
    )


def _classify_dbms_sql_parse() -> _Classification:
    return _Classification(
        categoria=CATEGORIA_NAO_DETERMINAVEL,
        prova="DBMS_SQL.PARSE nao restringe a forma do comando -- a API aceita qualquer DDL/DML/bloco, "
        "os binds entram em chamada separada (BIND_VARIABLE), nunca no PARSE",
        pode_invocar_procedure=True,
        determinavel=False,
        into_arity=None,
    )


def _classify_execute_immediate(stmt_text: str) -> _Classification:
    bulk_match = _BULK_COLLECT_INTO_RE.search(stmt_text)
    if bulk_match:
        arity = _segment_arity(stmt_text[bulk_match.end() :])
        return _Classification(
            categoria=CATEGORIA_QUERY_MULTI_LINHA,
            prova="clausula BULK COLLECT INTO de EXECUTE IMMEDIATE so aceita query -- a colecao "
            "recebe todas as linhas devolvidas, por isso multi-linha em vez de escalar",
            pode_invocar_procedure=False,
            determinavel=True,
            into_arity=arity,
        )

    into_match = _INTO_RE.search(stmt_text)
    if into_match:
        arity = _segment_arity(stmt_text[into_match.end() :])
        return _Classification(
            categoria=CATEGORIA_QUERY_LINHA_UNICA,
            prova="clausula INTO de EXECUTE IMMEDIATE sem BULK COLLECT so aceita query de linha unica",
            pode_invocar_procedure=False,
            determinavel=True,
            into_arity=arity,
        )

    using_match = _USING_RE.search(stmt_text)
    if using_match:
        after_using = stmt_text[using_match.end() :]
        if _OUT_BIND_RE.search(after_using):
            return _Classification(
                categoria=CATEGORIA_BLOCO_OU_RETURNING,
                prova="clausula USING de EXECUTE IMMEDIATE com bind OUT/IN OUT so e valida para "
                "bloco anonimo ou DML com RETURNING -- por isso pode esconder chamada de procedure",
                pode_invocar_procedure=True,
                determinavel=True,
                into_arity=None,
            )
        return _Classification(
            categoria=CATEGORIA_NAO_DETERMINAVEL,
            prova="clausula USING de EXECUTE IMMEDIATE so com binds IN nao restringe a forma do "
            "comando -- bind IN so passa valor de entrada, nao prova bloco nem RETURNING",
            pode_invocar_procedure=True,
            determinavel=False,
            into_arity=None,
        )

    return _Classification(
        categoria=CATEGORIA_NAO_DETERMINAVEL,
        prova="EXECUTE IMMEDIATE sem INTO e sem USING nao restringe a forma do comando -- pode ser "
        "DDL, DML ou bloco anonimo, mesmo quando o texto do fragmento e um literal 100% estatico",
        pode_invocar_procedure=True,
        determinavel=False,
        into_arity=None,
    )


def _subprogram_start_line(by_line: Dict[int, str], line: int) -> int:
    candidate = line
    while candidate > 0:
        text = by_line.get(candidate, "")
        if _SUBPROGRAM_START_RE.match(text):
            return candidate
        candidate -= 1
    return 1


def _is_in_loop(by_line: Dict[int, str], line: int) -> bool:
    start = _subprogram_start_line(by_line, line)
    balance = 0
    for line_no in range(start, line + 1):
        text = by_line.get(line_no, "")
        for match in _LOOP_TOKEN_RE.finditer(text):
            if match.group(1).upper().startswith("END"):
                balance -= 1
            else:
                balance += 1
    return balance > 0


def classify_dynamic_site(source_lines: Sequence[str], line: int) -> DynSiteForm:
    """Classifica o sitio de SQL dinamico na linha `line` de `source_lines`
    (lista de linhas de texto-fonte de UM objeto PL/SQL, indice 0 = linha 1,
    mesma convencao de `dynsql.rows_from_lines`). `line` e a linha onde o
    statement de execucao (`OPEN ... FOR` / `EXECUTE IMMEDIATE` /
    `DBMS_SQL.PARSE`) COMECA.

    Puro e deterministico: nenhuma conexao, nenhuma execucao do SQL
    analisado. Ver docstring do modulo para as regras 1-6."""
    by_line = _index_clean_lines(source_lines)
    stmt_text = _gather_statement_window(by_line, line)
    exec_kind = _detect_exec_kind(stmt_text, line)

    if exec_kind == EXEC_KIND_OPEN_FOR:
        classification = _classify_open_for()
    elif exec_kind == EXEC_KIND_DBMS_SQL_PARSE:
        classification = _classify_dbms_sql_parse()
    else:
        classification = _classify_execute_immediate(stmt_text)

    em_loop = _is_in_loop(by_line, line)

    return DynSiteForm(
        line=line,
        exec_kind=exec_kind,
        categoria_provada=classification.categoria,
        categoria_prova=classification.prova,
        pode_invocar_procedure=classification.pode_invocar_procedure,
        pode_invocar_procedure_determinavel=classification.determinavel,
        into_arity=classification.into_arity,
        em_loop=em_loop,
    )
