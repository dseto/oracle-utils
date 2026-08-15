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

Numeracao "(T-05)" acima e do contrato `plsqlflow-py`, ja concluido -- nao
confundir com a T-05 do contrato `depgraph-scale`
(.harness/work/depgraph-scale/), que estende O MESMO subcomando `depgraph`
com multiplas raizes (`target` vira `nargs="+"`, `--name` obrigatorio
acima de uma raiz, ver `depgraph_main`/`build_depgraph_arg_parser`) e
`--max-objects` com default maior e capaz de ser desligado (`0`, ver
`_effective_max_objects`).
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
    PL/SQL com PL/Scope disponivel.

    `statements_batch`/`source_batch` (T-03, contrato oracle-depgraph-scale)
    sao a versao em lote dos dois metodos acima -- OPCIONAIS do ponto de
    vista do orquestrador (`_build_depgraph_result` detecta com `hasattr`
    e cai no caminho por-objeto quando ausentes, mesmo contrato de
    fallback do T-01)."""

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

    def statements_batch(
        self, owner: str, object_names: Sequence[str]
    ) -> List[extract.PlscopeStatementBatchRow]:
        """Irma em lote de `statements` (T-03, contrato oracle-depgraph-scale):
        mesmo estilo de `triggers`/`tab_columns` acima -- fatia `object_names`
        com `extract.chunk_names` (limite de bind, ver extract.BATCH_CHUNK_SIZE)
        e concatena o resultado de cada chunk. Metodo OPCIONAL do ponto de
        vista de `_build_depgraph_result`: extractor que nao o expoe (os
        fakes de tests/test_depgraph_cli.py) cai no caminho por-objeto via
        `statements`, detectado com `hasattr`."""
        rows: List[extract.PlscopeStatementBatchRow] = []
        for chunk in extract.chunk_names(object_names):
            rows.extend(extract.fetch_plscope_statements_batch(self._conn, owner, chunk))
        return rows

    def source_batch(
        self, owner: str, object_names: Sequence[str]
    ) -> List[extract.FetchSourceBatchRow]:
        """Irma em lote de `source` -- mesmo padrao de `statements_batch`
        acima."""
        rows: List[extract.FetchSourceBatchRow] = []
        for chunk in extract.chunk_names(object_names):
            rows.extend(extract.fetch_source_batch(self._conn, owner, chunk))
        return rows


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
# T-05 (contrato depgraph-scale, escopo item 5): 500 nao aguenta sistema
# real (10-50 mil objetos) -- sobe para 5000. `--max-objects 0` desliga o
# cap por completo (ver _UNLIMITED_MAX_OBJECTS/_effective_max_objects
# abaixo); o aviso por max_depth continua valendo, e nao muda aqui.
DEFAULT_MAX_OBJECTS = 5000
DEFAULT_MAX_DEPTH = 20
DEFAULT_INDEX_SPLIT = 1000

# Sentinela de "sem cap de quantidade" (T-05). O motor da BFS
# (`depgraph._DepGraphEngine._process_object`, congelado pelo contrato
# T-02 e sua suite `tests/test_depgraph_bfs_limits.py`, fora da superficie
# desta tarefa) trata `max_objects=0` como "cabe ZERO objetos" -- corta a
# propria raiz na hora. Traduzir "0 = sem limite" ali quebraria esse
# contrato congelado. A traducao acontece so aqui, na borda do CLI: um
# `--max-objects 0` vira, antes de entrar no motor, um numero
# efetivamente impossivel de atingir (grafos reais nao chegam a
# `sys.maxsize` objetos) -- o motor nunca ve "0" nem muda de
# comportamento; so o aviso por `max_depth` continua podendo truncar.
_UNLIMITED_MAX_OBJECTS = sys.maxsize


def _effective_max_objects(value: int) -> int:
    """`--max-objects 0` (CLI) -> sem cap de quantidade (motor da BFS).
    Qualquer outro valor passa direto -- inclusive negativos, que o motor
    ja trata como "corta tudo" (comportamento preexistente, nao alterado
    aqui)."""
    return _UNLIMITED_MAX_OBJECTS if value == 0 else value


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
    parser.add_argument(
        "target",
        nargs="+",
        help="owner.objeto (raiz da BFS) -- aceita mais de uma raiz (T-05, "
        "grafo unico de subsistema: 'depgraph A.X A.Y --name modulo'); com "
        "mais de uma raiz, --name e obrigatorio",
    )
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
        "--name",
        default=None,
        help="nome do grafo -- obrigatorio com mais de uma raiz (saida em "
        "<output>/<name>/); com uma raiz so e opcional e, se informado, "
        "tambem rotula a saida em <output>/<name>/ (default sem --name: "
        "<output>/<OWNER>.<OBJETO>/)",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT_DIR,
        help="diretorio base de saida; o grafo e gravado em "
        "<output>/<OWNER>.<OBJETO>/ (raiz unica, sem --name) ou "
        "<output>/<name>/ (com --name ou multiplas raizes) "
        "(default: {})".format(DEFAULT_OUTPUT_DIR),
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
        help="cap de seguranca: numero maximo de objetos no grafo; 0 desliga "
        "o cap de quantidade (sem limite -- o aviso por --max-depth continua "
        "valendo) (default: {})".format(DEFAULT_MAX_OBJECTS),
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=DEFAULT_MAX_DEPTH,
        help="cap de seguranca: profundidade maxima da BFS (default: {})".format(
            DEFAULT_MAX_DEPTH
        ),
    )
    parser.add_argument(
        "--index-split",
        type=int,
        default=DEFAULT_INDEX_SPLIT,
        help="limiar de nos acima do qual INDEX.md vira sumario (estatisticas, hubs "
        "e lista de particoes) em vez da lista completa, com o fechamento "
        "transitivo movido para INDEX-<OWNER>.md por schema (default: {})".format(
            DEFAULT_INDEX_SPLIT
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
    extra_roots: Optional[Sequence[Tuple[str, str]]] = None,
) -> depgraph.DepGraphResult:
    """Orquestra T-02 (BFS) + T-03 (READ/WRITE/SQL dinamico, reusado sem
    alteracao) + T-04 (triggers), na ordem descrita no plano (secao 3):
    conecta (chamador) -> build_dep_graph -> enrich por objeto PL/SQL com
    PL/Scope -> add_trigger_phase (que tambem popula colunas de tabela).

    `extra_roots` (T-05, contrato oracle-depgraph-scale): raizes ALEM de
    `(owner, object_name)`, semeando a MESMA BFS (repassado direto a
    `depgraph.build_dep_graph(..., roots=...)`) -- mesmo motivo de design
    documentado la: parametro aditivo com default `None`, para nao mudar a
    assinatura posicional que `tests/test_depgraph_enrich_batch.py` (fora
    da superficie desta tarefa) ja chama com `owner, object_name`
    posicionais. Todo o enriquecimento abaixo (statements/source/dynamic
    SQL/triggers) ja opera sobre `bfs_result.nodes` sem saber quantas
    raizes contribuiram -- nenhuma mudanca necessaria dali para baixo."""
    bfs_result = depgraph.build_dep_graph(
        extractor,
        (owner, object_name),
        stop_schemas=stop_schemas,
        max_objects=max_objects,
        max_depth=max_depth,
        roots=extra_roots,
    )

    catalog_names_cache: Dict[str, List[str]] = {}

    def catalog_names_for(node_owner: str) -> List[str]:
        key = node_owner.upper()
        if key not in catalog_names_cache:
            rows = extractor.object_catalog(key)
            catalog_names_cache[key] = [r.object_name for r in rows]
        return catalog_names_cache[key]

    # T-03 (contrato oracle-depgraph-scale): objetos PL/SQL elegiveis (tipo
    # em PLSQL_OBJECT_TYPES + PL/Scope disponivel) sao os mesmos de antes --
    # so muda COMO statements/source sao buscados. Com extractor em lote
    # (statements_batch/source_batch, ambos OPCIONAIS no Protocol), agrupa
    # por owner e faz UMA chamada de cada por owner em vez de duas por
    # objeto; sem eles (fakes de tests/test_depgraph_cli.py), cai no
    # caminho por-objeto de sempre. Os dois caminhos tem que produzir o
    # mesmo DepGraphResult -- criterio de aceite do T-03.
    eligible_nodes = [
        node
        for node in bfs_result.nodes
        if node.object_type in depgraph.PLSQL_OBJECT_TYPES and node.plscope
    ]
    has_batch = hasattr(extractor, "statements_batch") and hasattr(extractor, "source_batch")

    statements_by_obj: Dict[Tuple[str, str], List[extract.PlscopeStatementRow]] = {}
    source_by_obj: Dict[Tuple[str, str], List[extract.FetchSourceRow]] = {}

    if has_batch:
        names_by_owner: Dict[str, List[str]] = {}
        for node in eligible_nodes:
            names_by_owner.setdefault(node.owner.upper(), []).append(node.object_name.upper())

        for owner_key, names in names_by_owner.items():
            unique_names = sorted(set(names))
            for row in extractor.statements_batch(owner_key, unique_names):
                key = (owner_key, row.object_name.upper())
                # Reagrupa preservando a ORDEM que a query em lote entregou
                # (ORDER BY object_name, line, ver plscope_statements_batch.sql)
                # -- ela ja e a mesma ordem por-objeto que a query individual
                # produzia, so intercalada entre objetos.
                statements_by_obj.setdefault(key, []).append(
                    extract.PlscopeStatementRow(
                        line=row.line,
                        stmt_type=row.stmt_type,
                        sql_id=row.sql_id,
                        has_into_record=row.has_into_record,
                        text=row.text,
                    )
                )
            for row in extractor.source_batch(owner_key, unique_names):
                key = (owner_key, row.name.upper())
                # Idem, ORDER BY name, type, line (fetch_source_batch.sql).
                source_by_obj.setdefault(key, []).append(
                    extract.FetchSourceRow(type=row.type, line=row.line, text=row.text)
                )

    enrich_edges: List[depgraph.DepEdge] = []
    snippets: Dict[str, str] = {}
    for node in eligible_nodes:
        node_key = (node.owner.upper(), node.object_name.upper())
        if has_batch:
            statements = statements_by_obj.get(node_key, [])
            source = source_by_obj.get(node_key, [])
        else:
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


def _missing_roots(extractor: "DbDepExtractor", roots: Sequence[Tuple[str, str]]) -> List[str]:
    """Devolve os `OWNER.OBJETO` de `roots` que nao existem em ALL_OBJECTS
    (T-05) -- checa TODAS as raizes antes de prosseguir, em vez de parar na
    primeira, para o erro em stderr listar todas de uma vez (menos idas e
    vindas quando quem chama tem 2+ raizes erradas)."""
    return [
        "{}.{}".format(owner, name) for owner, name in roots if not _root_exists(extractor, owner, name)
    ]


def depgraph_main(argv: Optional[List[str]] = None) -> int:
    """Subcomando `python -m plsqlflow depgraph <owner.objeto> [<owner.objeto> ...]`
    (T-05). Somente leitura -- ver docs/plano-oracle-dependency-graph.md.

    Multiplas raizes (T-05, contrato oracle-depgraph-scale): `target` aceita
    1+ alvos (`nargs="+"`, ver `build_depgraph_arg_parser`); todos semeiam a
    MESMA BFS via `depgraph.build_dep_graph(..., roots=...)` e saem num
    grafo unico. Com mais de uma raiz, `--name` e obrigatorio (senao nao ha
    como nomear o diretorio de saida sem escolher arbitrariamente uma das
    raizes) -- validado ANTES de conectar, mesmo padrao do alvo malformado
    abaixo. Com uma raiz so, `--name` e opcional; quando informado, tambem
    rotula a saida (`<output>/<name>/`) -- quando omitido, o comportamentos
    de sempre (`<output>/<OWNER>.<OBJETO>/`) nao muda.

    Exit codes (nao usar 2, reservado ao argparse):
    0 sucesso; 3 objetos sem PL/Scope (recompile.sql gerado, grafo parcial
    emitido mesmo assim -- NAO e falha); 4 raiz(es) inexistente(s)/invalida(s)
    OU --name ausente com multiplas raizes; 5 erro de conexao
    (`db.ConnectionConfigError` ou erro do driver `oracledb`, em qualquer
    ponto da extracao -- este pipeline so faz SELECT via `db.run_query`,
    entao qualquer `oracledb.Error` depois de conectar e da mesma familia).
    Nenhuma excecao propaga como traceback cru -- toda excecao esperada vira
    mensagem em stderr + codigo de saida."""
    parser = build_depgraph_arg_parser()
    args = parser.parse_args(argv)

    try:
        roots = [_parse_depgraph_target(t) for t in args.target]
    except ValueError as exc:
        print("erro: {}".format(exc), file=sys.stderr)
        return EXIT_INVALID_ROOT

    if len(roots) > 1 and not args.name:
        print(
            "erro: --name e obrigatorio com mais de uma raiz ({} informadas) -- "
            "todas as raizes saem num grafo unico e precisam de um nome de "
            "diretorio (ex.: --name modulo-financeiro)".format(len(roots)),
            file=sys.stderr,
        )
        return EXIT_INVALID_ROOT

    stop_schemas = _stop_schemas_from_arg(args.stop_schemas)
    effective_max_objects = _effective_max_objects(args.max_objects)

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

            missing = _missing_roots(extractor, roots)
            if missing:
                print(
                    "erro: raiz(es) nao encontrada(s) (ALL_OBJECTS): {} -- "
                    "confira o nome ou o privilegio de leitura".format(", ".join(missing)),
                    file=sys.stderr,
                )
                return EXIT_INVALID_ROOT

            owner0, name0 = roots[0]
            result = _build_depgraph_result(
                extractor,
                owner0,
                name0,
                stop_schemas=stop_schemas,
                max_objects=effective_max_objects,
                max_depth=args.max_depth,
                dynamic_window=args.dynamic_window,
                extra_roots=roots[1:],
            )
        finally:
            conn.close()
    except db.ConnectionConfigError as exc:
        print("erro de conexao: {}".format(exc), file=sys.stderr)
        return EXIT_CONNECTION_ERROR
    except oracledb.Error as exc:
        print("erro de conexao (driver): {}".format(exc), file=sys.stderr)
        return EXIT_CONNECTION_ERROR

    root_refs = ["{}.{}".format(owner, name) for owner, name in roots]
    # Nome do diretorio de saida (T-05): --name, quando informado, vale
    # para qualquer quantidade de raizes; sem --name (so possivel com UMA
    # raiz -- o caso multi-raiz ja saiu acima) o comportamento de sempre
    # nao muda (`<OWNER>.<OBJETO>/`).
    out_dir = Path(args.output) / (args.name if args.name else "{}.{}".format(*roots[0]))
    meta_params = {
        # root_ref (compat): continua STRING, igual ao formato de sempre --
        # com UMA raiz e byte-a-byte o mesmo valor de antes (nao perturba o
        # cabecalho do INDEX.md, `": {}".format(root_ref)` em
        # depgraph_render.py, congelado); com varias, vira a lista
        # separada por virgula (ainda uma string, o header continua legivel
        # sem mudar o `.format` do modulo congelado).
        "root_ref": ", ".join(root_refs),
        # roots (T-05, novo): registro explicito de TODAS as raizes -- e o
        # que o escopo item 5 pede ("meta.json registra todas as raizes"),
        # sem depender de quem le fazer `.split(", ")` em root_ref.
        "roots": root_refs,
        "stop_schemas": stop_schemas,
        "max_objects": args.max_objects,
        "max_depth": args.max_depth,
        "dynamic_window": args.dynamic_window,
        # T-04: registrado em meta.json para o valor usado ficar
        # rastreavel -- render_graph le "index_split" (ou usa o mesmo
        # default 1000 se a chave faltar) pra decidir entre INDEX.md flat
        # e sumario.
        "index_split": args.index_split,
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
