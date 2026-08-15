"""plsqlflow.dynsql -- constant folding de SQL dinamico (EXECUTE IMMEDIATE/OPEN FOR/DBMS_SQL.PARSE).

Concatenacao 100% literal (com no maximo um nivel de indirecao via variavel
atribuida antes) vira SQL resolvido. Qualquer identificador dentro da
concatenacao que nao seja um literal e nao esteja em ``package_constants``
(constante de spec fornecida pelo chamador) torna o resultado irresoluvel --
nunca adivinhamos o valor de uma variavel/parametro/constante sem dado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .extract import FetchSourceRow

_LITERAL_RE = re.compile(r"^'((?:[^']|'')*)'$", re.DOTALL)
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\$#]*$")

_EXEC_IMMEDIATE_RE = re.compile(r"EXECUTE\s+IMMEDIATE\s+(.+);", re.IGNORECASE | re.DOTALL)
_OPEN_FOR_RE = re.compile(r"OPEN\s+\S+\s+FOR\s+(.+);", re.IGNORECASE | re.DOTALL)
_DBMS_SQL_PARSE_RE = re.compile(
    r"DBMS_SQL\.PARSE\s*\(\s*\S+?\s*,\s*(.+?)\s*,", re.IGNORECASE | re.DOTALL
)
_SUBPROGRAM_START_RE = re.compile(r"^\s*(PROCEDURE|FUNCTION)\s+\w+", re.IGNORECASE)


@dataclass
class DynSqlResult:
    resolved: bool
    sql_text: Optional[str]
    fragment: str
    unresolved_ref: Optional[str]
    line: int
    likely: bool = False


def rows_from_lines(lines: Sequence[str], type_: str = "PACKAGE BODY") -> List[FetchSourceRow]:
    return [FetchSourceRow(type=type_, line=index + 1, text=text) for index, text in enumerate(lines)]


def _index_by_line(source: Sequence[FetchSourceRow]) -> Dict[int, str]:
    return {row.line: row.text for row in source}


def _gather_statement(by_line: Dict[int, str], start_line: int) -> str:
    parts: List[str] = []
    in_string = False
    line_no = start_line
    while line_no in by_line:
        text = by_line[line_no]
        parts.append(text)
        idx = 0
        length = len(text)
        terminated = False
        while idx < length:
            ch = text[idx]
            if in_string:
                if ch == "'":
                    if idx + 1 < length and text[idx + 1] == "'":
                        idx += 1
                    else:
                        in_string = False
            else:
                if ch == "'":
                    in_string = True
                elif ch == ";":
                    terminated = True
                    break
            idx += 1
        if terminated:
            break
        line_no += 1
    return "".join(parts)


def _extract_dynamic_expr(stmt_text: str) -> Optional[str]:
    for pattern in (_EXEC_IMMEDIATE_RE, _OPEN_FOR_RE, _DBMS_SQL_PARSE_RE):
        match = pattern.search(stmt_text)
        if match:
            return match.group(1).strip()
    return None


def _split_concat(expr: str) -> List[str]:
    tokens: List[str] = []
    current: List[str] = []
    in_string = False
    idx = 0
    length = len(expr)
    while idx < length:
        ch = expr[idx]
        if in_string:
            current.append(ch)
            if ch == "'":
                if idx + 1 < length and expr[idx + 1] == "'":
                    current.append(expr[idx + 1])
                    idx += 1
                else:
                    in_string = False
        else:
            if ch == "'":
                in_string = True
                current.append(ch)
            elif expr[idx : idx + 2] == "||":
                tokens.append("".join(current).strip())
                current = []
                idx += 1
            else:
                current.append(ch)
        idx += 1
    tokens.append("".join(current).strip())
    return tokens


def _unescape_literal(raw_inner: str) -> str:
    return raw_inner.replace("''", "'")


def _subprogram_start(by_line: Dict[int, str], line: int) -> int:
    candidate = line
    while candidate > 0:
        text = by_line.get(candidate, "")
        if _SUBPROGRAM_START_RE.match(text):
            return candidate
        candidate -= 1
    return 1


def _find_assignment(by_line: Dict[int, str], line: int, var_name: str) -> Optional[str]:
    lower_bound = _subprogram_start(by_line, line)
    assign_re = re.compile(r"^\s*{}\s*:=".format(re.escape(var_name)), re.IGNORECASE)
    for candidate in range(line - 1, lower_bound - 1, -1):
        text = by_line.get(candidate, "")
        if assign_re.match(text):
            stmt = _gather_statement(by_line, candidate)
            match = re.search(r":=\s*(.+);", stmt, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
            return None
    return None


def _evaluate_tokens(
    tokens: Sequence[str], package_constants: Optional[Dict[str, str]]
) -> Tuple[bool, Optional[str], str, Optional[str], str]:
    consts = {key.upper(): value for key, value in (package_constants or {}).items()}
    parts: List[str] = []
    frag_parts: List[str] = []
    unresolved_ref: Optional[str] = None
    ok = True
    for token in tokens:
        literal_match = _LITERAL_RE.match(token)
        if literal_match:
            value = _unescape_literal(literal_match.group(1))
            parts.append(value)
            frag_parts.append(value)
            continue
        if _IDENT_RE.match(token) and token.upper() in consts:
            value = consts[token.upper()]
            parts.append(value)
            frag_parts.append(value)
            continue
        ok = False
        if unresolved_ref is None:
            unresolved_ref = token
        frag_parts.append("<{}>".format(token))
    literal_only_text = "".join(parts)
    sql_text = literal_only_text if ok else None
    fragment = "".join(frag_parts)
    return ok, sql_text, fragment, unresolved_ref, literal_only_text


def _contains_known_object(text: str, known_object_names: Optional[Sequence[str]]) -> bool:
    if not known_object_names or not text:
        return False
    upper = text.upper()
    for name in known_object_names:
        if not name:
            continue
        if re.search(r"\b{}\b".format(re.escape(name.upper())), upper):
            return True
    return False


def resolve_dynamic_sql(
    source: Sequence[FetchSourceRow],
    line: int,
    package_constants: Optional[Dict[str, str]] = None,
    known_object_names: Optional[Sequence[str]] = None,
) -> DynSqlResult:
    by_line = _index_by_line(source)
    stmt_text = _gather_statement(by_line, line)
    expr = _extract_dynamic_expr(stmt_text)
    if expr is None:
        return DynSqlResult(
            resolved=False,
            sql_text=None,
            fragment=stmt_text.strip(),
            unresolved_ref=None,
            line=line,
        )

    tokens = _split_concat(expr)
    if len(tokens) == 1 and _IDENT_RE.match(tokens[0]) and not _LITERAL_RE.match(tokens[0]):
        var_name = tokens[0]
        assign_expr = _find_assignment(by_line, line, var_name)
        if assign_expr is None:
            return DynSqlResult(
                resolved=False,
                sql_text=None,
                fragment="<{}>".format(var_name),
                unresolved_ref=var_name,
                line=line,
            )
        eval_tokens = _split_concat(assign_expr)
    else:
        eval_tokens = tokens

    ok, sql_text, fragment, unresolved_ref, literal_only_text = _evaluate_tokens(
        eval_tokens, package_constants
    )
    likely = False
    if not ok:
        likely = _contains_known_object(literal_only_text, known_object_names)
    return DynSqlResult(
        resolved=ok,
        sql_text=sql_text,
        fragment=fragment,
        unresolved_ref=unresolved_ref,
        line=line,
        likely=likely,
    )
