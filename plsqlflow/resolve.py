"""Resolucao determinística de alvo (overloads) e cadeia de sinônimos.

Nao depende de conexao: opera sobre linhas ja extraidas (ResolveTargetRow) ou
sobre um "fetcher" injetado (para permitir teste offline da cadeia de
sinonimos sem mock de cursor).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .extract import ResolveTargetRow, SynonymRow


@dataclass(frozen=True)
class ArgumentSpec:
    position: Optional[int]
    argument_name: Optional[str]
    in_out: Optional[str]
    data_type: Optional[str]
    type_owner: Optional[str]
    type_name: Optional[str]


@dataclass(frozen=True)
class SignatureCandidate:
    object_name: str
    procedure_name: Optional[str]
    subprogram_id: Optional[int]
    overload: Optional[str]
    arguments: List[ArgumentSpec]
    label: str


def resolve_signatures(
    rows: List[ResolveTargetRow],
    subprogram: Optional[str] = None,
    arg_count: Optional[int] = None,
) -> List[SignatureCandidate]:
    """Agrupa linhas de resolve_target.sql por subprogram_id e devolve as
    assinaturas candidatas. Filtra por nome (case-insensitive) e, se
    ``arg_count`` for informado, tenta reduzir por aridade -- se sobrar mais
    de uma candidata, todas voltam rotuladas com sufixo #n (ambiguo)."""
    groups: Dict[int, List[ResolveTargetRow]] = {}
    order: List[int] = []
    for row in rows:
        if row.procedure_name is None:
            continue
        if subprogram is not None and row.procedure_name.upper() != subprogram.upper():
            continue
        sid = row.subprogram_id if row.subprogram_id is not None else -1
        if sid not in groups:
            groups[sid] = []
            order.append(sid)
        groups[sid].append(row)

    candidates: List[SignatureCandidate] = []
    for sid in sorted(order):
        group_rows = groups[sid]
        args = sorted(
            (
                ArgumentSpec(
                    r.position, r.argument_name, r.in_out, r.data_type, r.type_owner, r.type_name
                )
                for r in group_rows
                if r.position is not None and r.position > 0
            ),
            key=lambda a: a.position,
        )
        procedure_name = group_rows[0].procedure_name
        overload = group_rows[0].overload
        candidates.append(
            SignatureCandidate(
                object_name=group_rows[0].object_name,
                procedure_name=procedure_name,
                subprogram_id=None if sid == -1 else sid,
                overload=overload,
                arguments=args,
                label=procedure_name or "",
            )
        )

    if arg_count is not None:
        by_arity = [c for c in candidates if len(c.arguments) == arg_count]
        if by_arity:
            candidates = by_arity

    if len(candidates) > 1:
        candidates = [
            SignatureCandidate(
                object_name=cand.object_name,
                procedure_name=cand.procedure_name,
                subprogram_id=cand.subprogram_id,
                overload=cand.overload,
                arguments=cand.arguments,
                label="{}#{}".format(cand.procedure_name, cand.overload or str(idx)),
            )
            for idx, cand in enumerate(candidates, start=1)
        ]

    return candidates


SynonymFetcher = Callable[[str, str], List[SynonymRow]]


def resolve_synonym_chain(
    fetch_synonym: SynonymFetcher,
    owner: str,
    name: str,
    max_iterations: int = 10,
) -> List[SynonymRow]:
    """Segue a cadeia de sinonimos (inclui PUBLIC, via fetch_synonym) ate
    achar o objeto base, um db_link (folha externa) ou um ciclo.

    ``fetch_synonym`` isola a busca de linha (extract.fetch_resolve_synonym
    parcialmente aplicado a uma conexao) para permitir teste 100% offline.
    """
    chain: List[SynonymRow] = []
    seen: set = set()
    current_owner, current_name = owner, name
    for _ in range(max_iterations):
        key = (current_owner.upper(), current_name.upper())
        if key in seen:
            break
        seen.add(key)
        rows = fetch_synonym(current_owner, current_name)
        if not rows:
            break
        row = rows[0]
        chain.append(row)
        if row.db_link:
            break
        if not row.base_owner or not row.base_name:
            break
        current_owner, current_name = row.base_owner, row.base_name
    return chain
