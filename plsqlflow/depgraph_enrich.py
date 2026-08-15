"""Enriquecimento do grafo de dependencias com arestas de acesso a tabela
(READ/WRITE) e classificacao de SQL dinamico em tres niveis
(resolved/partial/opaque) -- T-03 do contrato `oracle-depgraph`.

Modulo proprio (nao mexe em `depgraph.py`, que outra tarefa esta editando
em paralelo): usa `DepNode`/`DepEdge`/`EDGE_TYPES` de `depgraph.py` como
tipos ja definidos, reusa `dynsql.resolve_dynamic_sql` para o nivel
"resolved" (literal) e o modo "fragmento SQL" novo de `lexical.py`
(`find_object_reference_candidates` + `strip_comments_and_literals`) para
achar o alvo de cada statement e os candidatos do nivel "partial".

Determinismo: nenhuma dependencia de relogio, toda ordenacao final e por
lista (nao por iteracao de `set` sem ordenar antes de devolver).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from . import dynsql, lexical
from .depgraph import DepEdge
from .extract import FetchSourceRow, PlscopeStatementRow

# Marcador de alvo indeterminado (plano, T-03): a aresta nunca some, so o
# destino vira "?" quando o parse best-effort nao acha candidato nenhum.
# Mesmo marcador usado pelo nivel "opaque" de SQL dinamico.
UNKNOWN_TARGET = "?"

_READ_STMT_TYPES = {"SELECT"}
_WRITE_STMT_TYPES = {"INSERT", "UPDATE", "DELETE", "MERGE"}

# Deteccao de SQL dinamico diretamente no texto-fonte -- usada como reforco
# de `_dynamic_lines_from_statements` (PL/Scope) para o caso de objeto sem
# PL/Scope disponivel (plano, secao 4.1): mesmo sem `plscope_statements`,
# uma ocorrencia de EXECUTE IMMEDIATE/OPEN...FOR/DBMS_SQL.PARSE no fonte
# ainda precisa aparecer classificada (criterio de aceite literal do T-03).
_DYNAMIC_SQL_SOURCE_RE = re.compile(
    r"EXECUTE\s+IMMEDIATE\b|OPEN\s+\S+\s+FOR\b|DBMS_SQL\s*\.\s*PARSE\b",
    re.IGNORECASE,
)

# Copia local do padrao de identificador (mesmo alfabeto de `lexical.py`,
# nao importado de la para nao depender de simbolo interno `_IDENT_CHARS`).
_IDENT = r"[A-Za-z_][A-Za-z0-9_\$#]*"
_INSERT_COLS_RE = re.compile(
    r"INSERT\s+INTO\s+{ident}(?:\.{ident})?\s*\(([^)]*)\)".format(ident=_IDENT),
    re.IGNORECASE | re.DOTALL,
)
_SET_CLAUSE_RE = re.compile(r"\bSET\b(.*?)(?:\bWHERE\b|$)", re.IGNORECASE | re.DOTALL)
_SET_COL_RE = re.compile(r"^\s*({ident})\s*=".format(ident=_IDENT))


@dataclass
class DynFinding:
    """Um achado de SQL dinamico ja classificado num dos tres niveis.

    `snippet`/`snippet_ref` carregam o trecho do fonte (janela de `window`
    linhas ao redor de `line`) para embutir na secao `## SQL Dinamico` do
    node .md (plano, secao 4.5/5). `candidates` so e nao-vazio no nivel
    "partial" -- lista ordenada dos nomes que bateram no catalogo.
    """

    line: int
    level: str  # "resolved" | "partial" | "opaque"
    snippet: str
    snippet_ref: str
    candidates: List[str]
    edges: List[DepEdge]


def _qualify(name: str, owner: str) -> str:
    if "." in name:
        return name.upper()
    return "{}.{}".format(owner.upper(), name.upper())


def _dedupe_preserve_order(names: Sequence[str]) -> List[str]:
    seen_upper: List[str] = []
    result: List[str] = []
    for name in names:
        upper = name.upper()
        if upper in seen_upper:
            continue
        seen_upper.append(upper)
        result.append(name)
    return result


def _targets_from_text(text: Optional[str]) -> List[str]:
    if not text:
        return []
    clean_lines, _ = lexical.strip_comments_and_literals([text])
    raw = lexical.find_object_reference_candidates(clean_lines)
    return _dedupe_preserve_order([name for name, _ in raw])


def _split_top_level_commas(text: str) -> List[str]:
    """Divide por virgula respeitando profundidade de parenteses (best
    effort para `SET col = func(a, b), outra = 1` nao quebrar no meio da
    chamada de funcao)."""
    parts: List[str] = []
    depth = 0
    current: List[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _extract_write_cols(text: Optional[str], stmt_type: str) -> Optional[List[str]]:
    """Colunas de escrita, best-effort (plano, secao 4.7). Ausencia de
    coluna reconhecivel no texto NAO e erro -- devolve None, nunca inventa
    coluna."""
    if not text:
        return None
    if stmt_type == "INSERT":
        match = _INSERT_COLS_RE.search(text)
        if not match:
            return None
        cols = [c.strip().strip('"') for c in match.group(1).split(",")]
        cols = [c for c in cols if c]
        return cols or None
    if stmt_type in ("UPDATE", "MERGE"):
        match = _SET_CLAUSE_RE.search(text)
        if not match:
            return None
        cols = []
        for part in _split_top_level_commas(match.group(1)):
            col_match = _SET_COL_RE.match(part)
            if col_match:
                cols.append(col_match.group(1))
        return cols or None
    return None


def table_edges_from_statements(
    statements: Sequence[PlscopeStatementRow],
    source: Sequence[FetchSourceRow],
    owner: str,
    object_name: str,
) -> List[DepEdge]:
    """Cada statement SELECT/INSERT/UPDATE/DELETE/MERGE de `statements`
    (linhas de `plscope_statements.sql`, T-01) vira uma ou mais `DepEdge`
    READ (SELECT) ou WRITE (demais, com `op` = tipo do statement).

    O alvo (tabela) e extraido do texto do statement (campo `text` de
    `PlscopeStatementRow` quando presente; senao a linha correspondente do
    fonte `source`, best-effort) via o modo fragmento-SQL de `lexical.py`.
    Statement de SQL dinamico (EXECUTE IMMEDIATE/OPEN FOR/DBMS_SQL.PARSE)
    e ignorado aqui -- vira aresta DYNAMIC_SQL via `dynamic_sql_findings`.

    Se o alvo nao for determinavel, a aresta ainda e emitida com
    `to_ref=UNKNOWN_TARGET` -- o statement nunca some do grafo.
    """
    by_line: Dict[int, str] = {row.line: row.text for row in source}
    from_ref = _qualify(object_name, owner)
    edges: List[DepEdge] = []

    for stmt in sorted(statements, key=lambda s: s.line):
        stmt_type = (stmt.stmt_type or "").upper().strip()
        if stmt_type not in _READ_STMT_TYPES and stmt_type not in _WRITE_STMT_TYPES:
            continue

        text = stmt.text if stmt.text is not None else by_line.get(stmt.line)
        targets = _targets_from_text(text)

        if stmt_type in _READ_STMT_TYPES:
            if not targets:
                edges.append(
                    DepEdge(from_ref=from_ref, to_ref=UNKNOWN_TARGET, edge_type="READ", line=stmt.line)
                )
            else:
                for target in targets:
                    edges.append(
                        DepEdge(
                            from_ref=from_ref,
                            to_ref=_qualify(target, owner),
                            edge_type="READ",
                            line=stmt.line,
                        )
                    )
        else:
            to_ref = _qualify(targets[0], owner) if targets else UNKNOWN_TARGET
            cols = _extract_write_cols(text, stmt_type)
            edges.append(
                DepEdge(
                    from_ref=from_ref,
                    to_ref=to_ref,
                    edge_type="WRITE",
                    line=stmt.line,
                    op=stmt_type,
                    cols=cols,
                )
            )

    return edges


def _looks_dynamic_stmt_type(stmt_type: Optional[str]) -> bool:
    if not stmt_type:
        return False
    upper = stmt_type.upper().strip()
    if upper == "EXECUTE IMMEDIATE":
        return True
    if upper.startswith("OPEN"):
        return True
    if "DBMS_SQL" in upper:
        return True
    return False


def _dynamic_lines_from_statements(statements: Sequence[PlscopeStatementRow]) -> List[int]:
    return sorted({stmt.line for stmt in statements if _looks_dynamic_stmt_type(stmt.stmt_type)})


def _dynamic_lines_from_source(source: Sequence[FetchSourceRow]) -> List[int]:
    return sorted({row.line for row in source if row.text and _DYNAMIC_SQL_SOURCE_RE.search(row.text)})


def detect_dynamic_lines(
    statements: Sequence[PlscopeStatementRow], source: Sequence[FetchSourceRow]
) -> List[int]:
    """Linhas com ocorrencia de SQL dinamico, combinando deteccao via
    PL/Scope (`plscope_statements.sql`, tipo EXECUTE IMMEDIATE/OPEN/
    DBMS_SQL.PARSE) e deteccao direta no texto-fonte (reforco para objeto
    sem PL/Scope). Usada tanto por `dynamic_sql_findings` quanto pelos
    testes que verificam "nenhuma ocorrencia fica sem classificacao"."""
    from_statements = set(_dynamic_lines_from_statements(statements))
    from_source = set(_dynamic_lines_from_source(source))
    return sorted(from_statements | from_source)


def _first_target_from_sql(sql_text: Optional[str]) -> Optional[str]:
    targets = _targets_from_text(sql_text)
    return targets[0] if targets else None


def dynamic_sql_findings(
    statements: Sequence[PlscopeStatementRow],
    source: Sequence[FetchSourceRow],
    catalog_names: Sequence[str],
    owner: str,
    object_name: str,
    window: int = 30,
) -> List[DynFinding]:
    """Classifica toda ocorrencia de SQL dinamico detectada em `statements`
    (PL/Scope) e/ou `source` (deteccao direta) em um dos tres niveis:

    - "resolved": `dynsql.resolve_dynamic_sql` resolveu 100% literal ->
      aresta DYNAMIC_SQL com `dynamic=True`, `confidence="exact"`.
    - "partial": nao resolveu literalmente, mas o modo fragmento-SQL de
      `lexical.py` achou candidato(s) que batem com `catalog_names` ->
      uma aresta por candidato, `confidence="partial"`.
    - "opaque": nem literal nem candidato -> uma unica aresta marcadora
      com `to_ref=UNKNOWN_TARGET`, `confidence="opaque"` -- ponto cego
      obrigatorio (nunca fica sem aresta/entrada).

    Criterio de aceite literal do T-03: nenhuma ocorrencia detectada por
    `detect_dynamic_lines` fica sem finding -- `len(resultado) ==
    len(detect_dynamic_lines(...))` sempre.
    """
    by_line: Dict[int, str] = {row.line: row.text for row in source}
    max_line = max(by_line) if by_line else 0
    from_ref = _qualify(object_name, owner)
    catalog_upper = sorted({name.upper() for name in catalog_names if name})

    findings: List[DynFinding] = []
    for line in detect_dynamic_lines(statements, source):
        snippet_start = max(1, line - window)
        snippet_end = min(max_line, line + window) if max_line else line
        snippet = "".join(by_line[l] for l in range(snippet_start, snippet_end + 1) if l in by_line)
        snippet_ref = "L{}-L{}".format(snippet_start, snippet_end)

        result = dynsql.resolve_dynamic_sql(source, line, known_object_names=catalog_upper)

        if result.resolved:
            target = _first_target_from_sql(result.sql_text)
            to_ref = _qualify(target, owner) if target else UNKNOWN_TARGET
            edge = DepEdge(
                from_ref=from_ref,
                to_ref=to_ref,
                edge_type="DYNAMIC_SQL",
                line=line,
                dynamic=True,
                confidence="exact",
                snippet_ref=snippet_ref,
            )
            findings.append(
                DynFinding(
                    line=line,
                    level="resolved",
                    snippet=snippet,
                    snippet_ref=snippet_ref,
                    candidates=[],
                    edges=[edge],
                )
            )
            continue

        candidate_names = _targets_from_text(result.fragment)
        matched = sorted(
            (name for name in candidate_names if name.upper() in set(catalog_upper)),
            key=str.upper,
        )

        if matched:
            edges = [
                DepEdge(
                    from_ref=from_ref,
                    to_ref=_qualify(name, owner),
                    edge_type="DYNAMIC_SQL",
                    line=line,
                    dynamic=True,
                    confidence="partial",
                    snippet_ref=snippet_ref,
                )
                for name in matched
            ]
            findings.append(
                DynFinding(
                    line=line,
                    level="partial",
                    snippet=snippet,
                    snippet_ref=snippet_ref,
                    candidates=matched,
                    edges=edges,
                )
            )
        else:
            edge = DepEdge(
                from_ref=from_ref,
                to_ref=UNKNOWN_TARGET,
                edge_type="DYNAMIC_SQL",
                line=line,
                dynamic=True,
                confidence="opaque",
                snippet_ref=snippet_ref,
            )
            findings.append(
                DynFinding(
                    line=line,
                    level="opaque",
                    snippet=snippet,
                    snippet_ref=snippet_ref,
                    candidates=[],
                    edges=[edge],
                )
            )

    return findings
