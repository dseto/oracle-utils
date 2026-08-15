"""plsqlflow.lexical -- camada B mecanica (fallback sem PL/Scope).

Tokenizador leve sobre texto-fonte PL/SQL: remove comentarios e literais de
string (preservando-os com posicao, uteis para dynsql.py), identifica
candidatos a chamada de subprograma e atribui um nivel de confianca com base
numa lista fechada de dependencias diretas conhecidas (ALL_DEPENDENCIES na
integracao real). Nao e um parser PL/SQL -- e uma heuristica pratica,
documentada como tal (residual de ambiguidade fica para o LLM).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple

# Lista pratica, nao exaustiva -- cobre o vocabulario mais comum de blocos
# PL/SQL para evitar que palavras reservadas virem falsos candidatos.
KEYWORDS = {
    "BEGIN", "END", "IF", "THEN", "ELSE", "ELSIF", "LOOP", "FOR", "WHILE",
    "IS", "AS", "PROCEDURE", "FUNCTION", "RETURN", "EXCEPTION", "WHEN",
    "RAISE", "NULL", "DECLARE", "PACKAGE", "BODY", "CURSOR", "TYPE",
    "RECORD", "TABLE", "OF", "IN", "OUT", "NOCOPY", "CONSTANT", "DEFAULT",
    "NOT", "AND", "OR", "OPEN", "CLOSE", "FETCH", "INTO", "FROM", "WHERE",
    "SELECT", "INSERT", "UPDATE", "DELETE", "VALUES", "SET", "COMMIT",
    "ROLLBACK", "EXECUTE", "IMMEDIATE", "PRAGMA", "AUTONOMOUS_TRANSACTION",
    "OTHERS", "GOTO", "EXIT", "CONTINUE",
}

_IDENT_CHARS = r"[A-Za-z_][A-Za-z0-9_\$#]*"
_CALL_CANDIDATE_RE = re.compile(r"\b({})\b\s*(?=[(;])".format(_IDENT_CHARS))

# Modo "fragmento SQL" (plano, secao 2.3 / T-03): find_call_candidates acima
# so serve para candidato a CHAMADA de subprograma (identificador seguido de
# '(' ou ';') -- nao casa nome de tabela num comando SQL (`FROM x WHERE ...`
# nao tem '(' nem ';' logo apos `x`). _OBJECT_REF_RE busca identificador em
# posicao de OBJETO referenciado, reconhecido pela palavra-chave que o
# precede: FROM, JOIN, INTO, UPDATE. Cobre tambem DELETE FROM (termina em
# FROM) e MERGE INTO (termina em INTO) sem precisar de alternativas extras.
# Nomes qualificados (OWNER.TABELA) sao capturados como uma unica unidade.
_QUALIFIED_IDENT = r"{ident}(?:\.{ident})?".format(ident=_IDENT_CHARS)
_OBJECT_REF_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE)\s+({})".format(_QUALIFIED_IDENT),
    re.IGNORECASE,
)


@dataclass
class Literal:
    line: int
    text: str


@dataclass
class Candidate:
    name: str
    line: int
    confidence: str  # "lexical" (confirmado por deps_direct) ou "heuristic"
    confirmed: bool


def strip_comments_and_literals(lines: Sequence[str]) -> Tuple[List[str], List[Literal]]:
    clean_lines: List[str] = []
    literals: List[Literal] = []
    in_block_comment = False
    for line_no, raw_line in enumerate(lines, start=1):
        out: List[str] = []
        idx = 0
        length = len(raw_line)
        while idx < length:
            if in_block_comment:
                if raw_line[idx : idx + 2] == "*/":
                    in_block_comment = False
                    idx += 2
                else:
                    idx += 1
                continue
            two = raw_line[idx : idx + 2]
            if two == "--":
                break
            if two == "/*":
                in_block_comment = True
                idx += 2
                continue
            ch = raw_line[idx]
            if ch == "'":
                buf = [ch]
                jdx = idx + 1
                while jdx < length:
                    if raw_line[jdx] == "'":
                        if jdx + 1 < length and raw_line[jdx + 1] == "'":
                            buf.append("''")
                            jdx += 2
                            continue
                        buf.append("'")
                        jdx += 1
                        break
                    buf.append(raw_line[jdx])
                    jdx += 1
                literals.append(Literal(line=line_no, text="".join(buf)))
                out.append(" ")
                idx = jdx
                continue
            out.append(ch)
            idx += 1
        clean_lines.append("".join(out))
    return clean_lines, literals


def find_call_candidates(
    clean_lines: Sequence[str],
    local_vars: Sequence[str],
    keywords: Optional[Set[str]] = None,
) -> List[Tuple[str, int]]:
    local_set = {name.upper() for name in local_vars}
    keyword_set = {name.upper() for name in (keywords if keywords is not None else KEYWORDS)}
    candidates: List[Tuple[str, int]] = []
    for line_no, line in enumerate(clean_lines, start=1):
        for match in _CALL_CANDIDATE_RE.finditer(line):
            name = match.group(1)
            upper = name.upper()
            if upper in keyword_set or upper in local_set:
                continue
            candidates.append((name, line_no))
    return candidates


def find_object_reference_candidates(clean_lines: Sequence[str]) -> List[Tuple[str, int]]:
    """Extrai identificadores em posicao de OBJETO (tabela/view) num
    fragmento SQL/PL-SQL ja limpo (sem comentarios/literais -- aplicar
    ``strip_comments_and_literals`` antes de chamar esta funcao).

    Complementa ``find_call_candidates``: aquele busca candidato a chamada
    de subprograma; este busca candidato a objeto referenciado num comando
    SQL (SELECT/INSERT/UPDATE/DELETE/MERGE ou fragmento de SQL dinamico),
    reconhecido pela palavra-chave FROM/JOIN/INTO/UPDATE que o precede.
    Nomes qualificados (``OWNER.TABELA``) saem como uma unica unidade.

    Nao e um parser SQL -- heuristica pratica com limitacao documentada:
    ``SELECT col INTO v_var FROM tabela`` tambem casa ``v_var`` (variavel,
    nao tabela) como candidato, porque a heuristica so olha a palavra-chave
    precedente. O residual de ambiguidade fica para ``classify_candidates``
    decidir confianca via catalogo, mesmo espirito do restante do modulo.
    """
    candidates: List[Tuple[str, int]] = []
    for line_no, line in enumerate(clean_lines, start=1):
        for match in _OBJECT_REF_RE.finditer(line):
            candidates.append((match.group(1), line_no))
    return candidates


def classify_candidates(
    raw_candidates: Sequence[Tuple[str, int]], deps_direct: Sequence[str]
) -> List[Candidate]:
    known = {name.upper() for name in deps_direct}
    result: List[Candidate] = []
    for name, line_no in raw_candidates:
        confirmed = name.upper() in known
        confidence = "lexical" if confirmed else "heuristic"
        result.append(Candidate(name=name, line=line_no, confidence=confidence, confirmed=confirmed))
    return result


@dataclass
class LexicalScanResult:
    candidates: List[Candidate]
    literals: List[Literal]


def scan_candidates(
    lines: Sequence[str],
    local_vars: Sequence[str],
    deps_direct: Sequence[str],
    keywords: Optional[Set[str]] = None,
) -> LexicalScanResult:
    clean_lines, literals = strip_comments_and_literals(lines)
    raw_candidates = find_call_candidates(clean_lines, local_vars, keywords)
    candidates = classify_candidates(raw_candidates, deps_direct)
    return LexicalScanResult(candidates=candidates, literals=literals)
