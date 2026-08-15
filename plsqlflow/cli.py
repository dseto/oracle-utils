"""plsqlflow.cli -- entrada `python -m plsqlflow <owner.objeto[.subprograma]> --conn <alias>` (T-04)
e `python -m plsqlflow depgraph <owner.objeto> --conn <alias>` (T-05).

Liga banco real (db.py/extract.py) + resolve.py + graph.py + report.py +
mermaid.py (modo flow, T-04) ou + depgraph.py + depgraph_enrich.py +
depgraph_render.py (modo depgraph, T-05). Resolve sinonimo (inclui
PUBLIC/db_link) antes de montar o alvo (modo flow) e falha alto
(RuntimeError) se o objeto nao tiver PL/Scope disponivel -- o fallback
lexico (plsqlflow/lexical.py) existe como biblioteca testada mas ainda nao
esta ligado ao pipeline automatico do modo flow (limitacao conhecida,
documentada na SKILL.md; achado do blind review do contrato plsqlflow-py).
`synonym_fallback_target`/`plscope_available` sao funcoes puras, testadas
offline em tests/test_plsqlflow_skill.py; o resto exige conexao real, sem
teste dedicado aqui (o golden test exercita report.py/mermaid.py direto
com um Extractor fake).

Dispatch de subcomando (T-05, plano secao 4.3): `main()` decide, ANTES de
qualquer argparse, se o primeiro argumento e o subcomando `depgraph` (sem
`.`) ou um alvo do modo flow (sempre `owner.objeto[.subprograma]`, com
`.`). Retro-compatibilidade total com o modo flow e criterio de aceite --
o corpo original de `main()` foi preservado, ipsis litteris, em
`_flow_main()`.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import oracledb

from . import db, depgraph, depgraph_enrich, depgraph_render, extract, report, resolve
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


class DbDepExtractor:
    """Extractor concreto do Protocol `depgraph.DepExtractor` (T-02/T-04)
    sobre uma conexao de verdade -- mesmo estilo de `DbExtractor` acima
    (cada metodo chama extract.fetch_* com a conexao ja aberta, sem SQL
    montado a partir de entrada do usuario). Usado pelo subcomando
    `depgraph` (T-05).

    Diferenca deliberada de `DbExtractor.triggers`: aqui o fetcher e
    `extract.fetch_triggers_any_status` (T-05, Entrega 1), NAO o filtrado
    `fetch_triggers_for_tables` do modo flow -- o grafo de dependencias
    precisa do trigger desabilitado como no (dependencia estrutural real,
    ver sql/flow/triggers_any_status.sql).

    `statements`/`source` nao fazem parte do Protocol `DepExtractor` (que
    so cobre a BFS + fase de triggers) -- sao acrescentados aqui porque o
    orquestrador do subcomando (`_build_depgraph_result`) precisa deles
    para chamar `depgraph_enrich.table_edges_from_statements`/
    `dynamic_sql_findings` (T-03, reusado sem alteracao) por objeto
    PL/SQL com PL/Scope disponivel."""

    def __init__(self, conn):
        self._conn = conn

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        return extract.fetch_deps_direct(self._conn, owner, name)

    def object_catalog(
        self, owner: str, object_list: Optional[str] = None
    ) -> List[extract.ObjectCatalogRow]:
        return extract.fetch_object_catalog(self._conn, owner, object_list)

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return extract.fetch_plscope_check(self._conn, owner)

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return extract.fetch_resolve_synonym(self._conn, owner, name)

    def triggers(self, owner: str, table_names: Sequence[str]) -> List[extract.TriggerRow]:
        table_names = list(table_names)
        if not table_names:
            return []
        table_list = ",".join(sorted({n.upper() for n in table_names}))
        return extract.fetch_triggers_any_status(self._conn, owner, table_list)

    def tab_columns(self, owner: str, table_names: Sequence[str]) -> List[extract.TabColumnRow]:
        table_names = list(table_names)
        if not table_names:
            return []
        table_list = ",".join(sorted({n.upper() for n in table_names}))
        return extract.fetch_tab_columns(self._conn, owner, table_list)

    def statements(self, owner: str, object_name: str) -> List[extract.PlscopeStatementRow]:
        return extract.fetch_plscope_statements(self._conn, owner, object_name)

    def source(self, owner: str, object_name: str) -> List[extract.FetchSourceRow]:
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


def _flow_main(argv: Optional[List[str]] = None) -> int:
    """Corpo original do `main()` do modo flow (T-04), preservado ipsis
    litteris -- retro-compatibilidade total e criterio de aceite do T-05
    (dispatch de subcomando, ver docstring do modulo)."""
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


# ==========================================================================
# T-05: subcomando `depgraph` -- dispatch, exit codes, orquestracao.
# ==========================================================================

# Exit codes proprios do depgraph (plano, secao 4.3). argparse ja usa 2
# para erro de uso (target ausente, flag desconhecida) -- nao colidir.
EXIT_OK = 0
EXIT_NEEDS_RECOMPILE = 3
EXIT_INVALID_ROOT = 4
EXIT_CONNECTION_ERROR = 5

DEPGRAPH_SUBCOMMAND = "depgraph"

DEFAULT_OUTPUT_DIR = "./oracle-graph"
DEFAULT_STOP_SCHEMAS_ARG = "SYS,SYSTEM"
DEFAULT_DYNAMIC_WINDOW = 30
DEFAULT_MAX_OBJECTS = 500
DEFAULT_MAX_DEPTH = 20


def _parse_depgraph_target(target: str) -> Tuple[str, str]:
    """`owner.objeto` -- SEM subprograma (o depgraph e grao OBJETO, nao
    subprograma; ver depgraph.py). Alvo com 0, 1 ou 3+ partes e invalido
    (raiz invalida, T-05 exit 4)."""
    parts = (target or "").split(".")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError(
            "alvo invalido para depgraph: {!r}. Esperado owner.objeto (sem "
            "subprograma -- o grafo de dependencias e de grao objeto)".format(target)
        )
    return parts[0].strip().upper(), parts[1].strip().upper()


def _stop_schemas_from_arg(value: str) -> List[str]:
    return [s.strip().upper() for s in (value or "").split(",") if s.strip()]


def build_depgraph_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m plsqlflow depgraph",
        description=(
            "Grafo estatico de dependencias Oracle (grao objeto), materializado em "
            "disco a partir de uma raiz owner.objeto -- BFS sobre ALL_DEPENDENCIES, "
            "enriquecido com PL/Scope, triggers das tabelas escritas e classificacao "
            "de SQL dinamico (resolved/partial/opaque). Ver "
            "docs/plano-oracle-dependency-graph.md."
        ),
    )
    parser.add_argument("target", help="owner.objeto (raiz da BFS)")
    # Senha NUNCA por argumento (regra de seguranca, T-05): so --conn (alias
    # em tools/flow-connections.json + env PLSQLFLOW_PWD_<ALIAS>) ou as env
    # vars diretas PLSQLFLOW_USER/PLSQLFLOW_PWD/PLSQLFLOW_DSN -- exatamente
    # o mesmo mecanismo do modo flow (db.resolve_connection_params). Nao
    # acrescentar flag de senha/DSN/host/porta posicional aqui.
    parser.add_argument(
        "--conn",
        default=None,
        help="alias de conexao (tools/flow-connections.json); omitido usa "
        "PLSQLFLOW_USER/PLSQLFLOW_PWD/PLSQLFLOW_DSN diretas",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_DIR,
        help="diretorio base de saida; o grafo e gravado em "
        "<output>/<OWNER>.<OBJETO>/ (default: {})".format(DEFAULT_OUTPUT_DIR),
    )
    parser.add_argument(
        "--stop-schemas",
        default=DEFAULT_STOP_SCHEMAS_ARG,
        help="schemas de fronteira, separados por virgula -- viram no folha sem "
        "expansao (default: {})".format(DEFAULT_STOP_SCHEMAS_ARG),
    )
    parser.add_argument(
        "--dynamic-window",
        type=int,
        default=DEFAULT_DYNAMIC_WINDOW,
        help="linhas de ALL_SOURCE ao redor de cada ocorrencia de SQL dinamico, "
        "para o trecho embutido no node .md (default: {})".format(DEFAULT_DYNAMIC_WINDOW),
    )
    parser.add_argument(
        "--max-objects",
        type=int,
        default=DEFAULT_MAX_OBJECTS,
        help="cap de seguranca: numero maximo de objetos no grafo (default: {})".format(
            DEFAULT_MAX_OBJECTS
        ),
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help="cap de seguranca: profundidade maxima da BFS (default: {})".format(
            DEFAULT_MAX_DEPTH
        ),
    )
    return parser


def _root_exists(extractor: "DbDepExtractor", owner: str, object_name: str) -> bool:
    rows = extractor.object_catalog(owner)
    names = {r.object_name.upper() for r in rows}
    return object_name.upper() in names


def _build_depgraph_result(
    extractor: "DbDepExtractor",
    owner: str,
    object_name: str,
    stop_schemas: Sequence[str],
    max_objects: int,
    max_depth: int,
    dynamic_window: int,
) -> depgraph.DepGraphResult:
    """Orquestra T-02 (BFS) + T-03 (READ/WRITE/SQL dinamico, reusado sem
    alteracao) + T-04 (triggers), na ordem descrita no plano (secao 3):
    conecta (chamador) -> build_dep_graph -> enrich por objeto PL/SQL com
    PL/Scope -> add_trigger_phase (que tambem popula colunas de tabela)."""
    bfs_result = depgraph.build_dep_graph(
        extractor,
        (owner, object_name),
        stop_schemas=stop_schemas,
        max_objects=max_objects,
        max_depth=max_depth,
    )

    catalog_names_cache: Dict[str, List[str]] = {}

    def catalog_names_for(node_owner: str) -> List[str]:
        key = node_owner.upper()
        if key not in catalog_names_cache:
            rows = extractor.object_catalog(key)
            catalog_names_cache[key] = [r.object_name for r in rows]
        return catalog_names_cache[key]

    enrich_edges: List[depgraph.DepEdge] = []
    snippets: Dict[str, str] = {}
    for node in bfs_result.nodes:
        if node.object_type not in depgraph.PLSQL_OBJECT_TYPES or not node.plscope:
            continue
        statements = extractor.statements(node.owner, node.object_name)
        source = extractor.source(node.owner, node.object_name)
        enrich_edges.extend(
            depgraph_enrich.table_edges_from_statements(statements, source, node.owner, node.object_name)
        )
        findings = depgraph_enrich.dynamic_sql_findings(
            statements,
            source,
            catalog_names=catalog_names_for(node.owner),
            owner=node.owner,
            object_name=node.object_name,
            window=dynamic_window,
        )
        for finding in findings:
            enrich_edges.extend(finding.edges)
            snippets[finding.snippet_ref] = finding.snippet

    merged = replace(bfs_result, edges=list(bfs_result.edges) + enrich_edges, snippets=snippets)

    return depgraph.add_trigger_phase(
        extractor, merged, stop_schemas=stop_schemas, max_objects=max_objects, max_depth=max_depth
    )


def _count_blind_spots(result: depgraph.DepGraphResult) -> int:
    """Mesma contagem das secoes de `## PONTOS CEGOS` que
    `depgraph_render._blind_spot_lines` monta (modulo congelado, T-04, nao
    alterado aqui) -- usada so para o resumo impresso no stdout."""
    dyn_issues = sum(
        1 for e in result.edges if e.edge_type == "DYNAMIC_SQL" and e.confidence in ("partial", "opaque")
    )
    total = dyn_issues + len(set(result.needs_recompile))
    if result.truncated:
        total += 1
    return total


def depgraph_main(argv: Optional[List[str]] = None) -> int:
    """Subcomando `python -m plsqlflow depgraph <owner.objeto>` (T-05).
    Somente leitura -- ver docs/plano-oracle-dependency-graph.md.

    Exit codes (nao usar 2, reservado ao argparse):
    0 sucesso; 3 objetos sem PL/Scope (recompile.sql gerado, grafo parcial
    emitido mesmo assim -- NAO e falha); 4 raiz inexistente/invalida; 5 erro
    de conexao (`db.ConnectionConfigError` ou erro do driver `oracledb`, em
    qualquer ponto da extracao -- este pipeline so faz SELECT via
    `db.run_query`, entao qualquer `oracledb.Error` depois de conectar e da
    mesma familia). Nenhuma excecao propaga como traceback cru -- toda
    excecao esperada vira mensagem em stderr + codigo de saida."""
    parser = build_depgraph_arg_parser()
    args = parser.parse_args(argv)

    try:
        owner, object_name = _parse_depgraph_target(args.target)
    except ValueError as exc:
        print("erro: {}".format(exc), file=sys.stderr)
        return EXIT_INVALID_ROOT

    stop_schemas = _stop_schemas_from_arg(args.stop_schemas)

    try:
        conn = db.connect(alias=args.conn)
    except db.ConnectionConfigError as exc:
        print("erro de conexao: {}".format(exc), file=sys.stderr)
        return EXIT_CONNECTION_ERROR
    except oracledb.Error as exc:
        print("erro de conexao (driver): {}".format(exc), file=sys.stderr)
        return EXIT_CONNECTION_ERROR

    try:
        try:
            extractor = DbDepExtractor(conn)

            if not _root_exists(extractor, owner, object_name):
                print(
                    "erro: raiz {}.{} nao encontrada (ALL_OBJECTS, owner {}) -- "
                    "confira o nome ou o privilegio de leitura".format(owner, object_name, owner),
                    file=sys.stderr,
                )
                return EXIT_INVALID_ROOT

            result = _build_depgraph_result(
                extractor,
                owner,
                object_name,
                stop_schemas=stop_schemas,
                max_objects=args.max_objects,
                max_depth=args.max_depth,
                dynamic_window=args.dynamic_window,
            )
        finally:
            conn.close()
    except db.ConnectionConfigError as exc:
        print("erro de conexao: {}".format(exc), file=sys.stderr)
        return EXIT_CONNECTION_ERROR
    except oracledb.Error as exc:
        print("erro de conexao (driver): {}".format(exc), file=sys.stderr)
        return EXIT_CONNECTION_ERROR

    out_dir = Path(args.output) / "{}.{}".format(owner, object_name)
    meta_params = {
        "root_ref": "{}.{}".format(owner, object_name),
        "stop_schemas": stop_schemas,
        "max_objects": args.max_objects,
        "max_depth": args.max_depth,
        "dynamic_window": args.dynamic_window,
    }
    depgraph_render.render_graph(result, out_dir, meta_params)

    blind_spots = _count_blind_spots(result)
    print(
        "depgraph {}: nos={} arestas={} pontos_cegos={} saida={}".format(
            meta_params["root_ref"], len(result.nodes), len(result.edges), blind_spots, out_dir
        )
    )
    if result.truncated:
        print("aviso: grafo truncado -- {}".format(result.truncation_reason), file=sys.stderr)
    if result.needs_recompile:
        print(
            "aviso: {} objeto(s) sem PL/Scope -- recompile.sql gerado em {} (grafo parcial, "
            "gravado mesmo assim)".format(len(result.needs_recompile), out_dir),
            file=sys.stderr,
        )
        return EXIT_NEEDS_RECOMPILE

    return EXIT_OK


def _dispatch_argv(argv: List[str]) -> Tuple[Optional[str], List[str]]:
    """Decide entre subcomando e alvo do modo flow (plano, secao 4.3):
    primeiro argumento SEM `.` e uma flag (nao comeca com `-`) vira
    candidato a subcomando; alvo do modo flow sempre contem `.`
    (`owner.objeto[.subprograma]`). Devolve `(None, argv)` quando o
    primeiro argumento e um alvo (ou esta ausente/e uma flag) -- nesse caso
    quem chama roteia para `_flow_main`, preservando o comportamento atual
    byte a byte."""
    if not argv:
        return None, argv
    head = argv[0]
    if head.startswith("-") or "." in head:
        return None, argv
    return head, argv[1:]


def main(argv: Optional[List[str]] = None) -> int:
    """Ponto de entrada unico do pacote (`python -m plsqlflow ...`).
    Dispatch de subcomando ANTES de qualquer argparse (T-05, plano secao
    4.3) -- retro-compatibilidade total com o modo flow existente."""
    if argv is None:
        argv = sys.argv[1:]

    subcommand, rest = _dispatch_argv(argv)
    if subcommand is None:
        return _flow_main(rest)
    if subcommand == DEPGRAPH_SUBCOMMAND:
        return depgraph_main(rest)

    print(
        "erro: subcomando desconhecido: {!r}. Esperado '{}' ou owner.objeto[.subprograma] "
        "(alvos sempre contem '.').".format(subcommand, DEPGRAPH_SUBCOMMAND),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
