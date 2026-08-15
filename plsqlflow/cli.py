"""plsqlflow.cli -- entrada `python -m plsqlflow <owner.objeto[.subprograma]> --conn <alias>` (T-04).

Liga banco real (db.py/extract.py) + resolve.py + graph.py + report.py +
mermaid.py. Resolve sinonimo (inclui PUBLIC/db_link) antes de montar o
alvo e falha alto (RuntimeError) se o objeto nao tiver PL/Scope disponivel
-- o fallback lexico (plsqlflow/lexical.py) existe como biblioteca testada
mas ainda nao esta ligado ao pipeline automatico (limitacao conhecida,
documentada na SKILL.md; achado do blind review do contrato plsqlflow-py).
`synonym_fallback_target`/`plscope_available` sao funcoes puras, testadas
offline em tests/test_plsqlflow_skill.py; o resto exige conexao real, sem
teste dedicado aqui (o golden test exercita report.py/mermaid.py direto
com um Extractor fake).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from . import db, extract, report, resolve
from .graph import RootTarget


class DbExtractor:
    """Extractor concreto (graph.Extractor + report.Extractor) sobre uma
    conexao de verdade. Cada metodo chama extract.fetch_* com a conexao
    ja aberta -- sem SQL montado a partir de entrada do usuario (identificadores
    ja validados por db.py / resolve.py)."""

    def __init__(self, conn):
        self._conn = conn

    def calls(self, owner: str, object_name: str):
        return extract.fetch_plscope_calls(self._conn, owner, object_name)

    def statements(self, owner: str, object_name: str):
        return extract.fetch_plscope_statements(self._conn, owner, object_name)

    def triggers(self, owner: str, table_names: List[str]):
        if not table_names:
            return []
        table_list = ",".join(sorted(set(table_names)))
        return extract.fetch_triggers_for_tables(self._conn, owner, table_list)

    def fk_cascade(self, owner: str, table_name: str):
        return extract.fetch_fk_cascade(self._conn, owner, table_name)

    def type_hierarchy(self, owner: str, type_name: str):
        return extract.fetch_type_hierarchy(self._conn, owner, type_name)

    def source(self, owner: str, object_name: str):
        return extract.fetch_source(self._conn, owner, object_name)


def _parse_target(target: str) -> RootTarget:
    parts = target.split(".")
    if len(parts) == 2:
        owner, object_name = parts
        return RootTarget(owner=owner.upper(), object_name=object_name.upper())
    if len(parts) == 3:
        owner, object_name, subprogram = parts
        return RootTarget(owner=owner.upper(), object_name=object_name.upper(), subprogram=subprogram.upper())
    raise ValueError(
        "alvo invalido: {!r}. Esperado owner.objeto ou owner.objeto.subprograma".format(target)
    )


def synonym_fallback_target(
    chain: List[extract.SynonymRow], target: RootTarget
) -> RootTarget:
    """Se a cadeia de sinonimos (ja resolvida por resolve.resolve_synonym_chain)
    chegar a um objeto base, devolve o RootTarget apontando pra la. Cadeia
    vazia ou terminando em db_link (folha externa, sem objeto local pra
    expandir) devolve o alvo original sem alteracao."""
    if not chain:
        return target
    last = chain[-1]
    if last.db_link or not last.base_owner or not last.base_name:
        return target
    return RootTarget(
        owner=last.base_owner.upper(),
        object_name=last.base_name.upper(),
        subprogram=target.subprogram,
        overload=target.overload,
    )


def plscope_available(
    checks: List[extract.PlscopeCheckRow], object_name: str
) -> bool:
    """`plscope_check.sql` traz uma linha por objeto/tipo (PACKAGE, PACKAGE
    BODY, ...) -- basta UM deles ter IDENTIFIERS:ALL pra camada A (PL/Scope)
    valer pra este objeto."""
    object_name = object_name.upper()
    return any(
        c.name.upper() == object_name
        and c.plscope_settings
        and "IDENTIFIERS:ALL" in c.plscope_settings.upper()
        for c in checks
    )


def _resolve_root(conn, target: RootTarget) -> RootTarget:
    rows = extract.fetch_resolve_target(conn, target.owner, target.object_name, target.subprogram)
    if not rows:
        chain = resolve.resolve_synonym_chain(
            lambda owner, name: extract.fetch_resolve_synonym(conn, owner, name),
            target.owner,
            target.object_name,
        )
        resolved = synonym_fallback_target(chain, target)
        if resolved is not target:
            target = resolved
            rows = extract.fetch_resolve_target(conn, target.owner, target.object_name, target.subprogram)
    if not target.subprogram:
        return target
    candidates = resolve.resolve_signatures(rows, subprogram=target.subprogram)
    if len(candidates) == 1 and candidates[0].overload is None:
        return target
    if len(candidates) >= 1:
        overload = candidates[0].overload if len(candidates) == 1 else None
        return RootTarget(
            owner=target.owner,
            object_name=target.object_name,
            subprogram=target.subprogram,
            overload=overload,
        )
    return target


def _ensure_plscope(conn, target: RootTarget) -> None:
    checks = extract.fetch_plscope_check(conn, target.owner)
    if plscope_available(checks, target.object_name):
        return
    raise RuntimeError(
        "{}.{} nao esta compilado com PLSCOPE_SETTINGS contendo IDENTIFIERS:ALL -- "
        "o fallback lexico automatico (camada B, plsqlflow/lexical.py) ainda nao esta "
        "ligado ao pipeline do script (limitacao conhecida, ver SKILL.md). Recompile o "
        "objeto com PLSCOPE_SETTINGS='IDENTIFIERS:ALL, STATEMENTS:ALL' antes de rodar "
        "'python -m plsqlflow', ou peca ao assistente para montar o grafo manualmente "
        "a partir de ALL_SOURCE.".format(target.owner, target.object_name)
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m plsqlflow",
        description="Diagrama mermaid deterministico do caminho de execucao de uma procedure/function Oracle.",
    )
    parser.add_argument("target", help="owner.objeto[.subprograma]")
    parser.add_argument(
        "--conn",
        default=None,
        help="alias de conexao (tools/flow-connections.json); omitido usa "
        "PLSQLFLOW_USER/PLSQLFLOW_PWD/PLSQLFLOW_DSN diretas",
    )
    parser.add_argument("--max-depth", type=int, default=10)
    parser.add_argument("--node-budget", type=int, default=120)
    parser.add_argument("--json", action="store_true", help="imprime o relatorio completo em JSON")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    root = _parse_target(args.target)
    conn = db.connect(alias=args.conn)
    try:
        root = _resolve_root(conn, root)
        _ensure_plscope(conn, root)
        extractor = DbExtractor(conn)
        result = report.build_report(
            extractor, root, max_depth=args.max_depth, node_budget=args.node_budget
        )
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(result["mermaid"])
    stats = result["stats"]
    print(
        "nos={} arestas={} profundidade={} colapsados={} pct_plscope={}".format(
            stats.get("nodes"),
            stats.get("edges"),
            stats.get("depth_reached"),
            stats.get("collapsed_groups"),
            stats.get("pct_plscope"),
        )
    )
    blind_spots = result["blind_spots"]
    if blind_spots:
        print("pontos cegos ({}):".format(len(blind_spots)))
        for spot in blind_spots:
            print("  - {} @ {}".format(spot["type"], spot["at"]))
    else:
        print("pontos cegos: nenhum")
    return 0


if __name__ == "__main__":
    sys.exit(main())
