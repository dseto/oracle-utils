"""Testes offline (T-01, contrato depgraph-scale) para a BFS por NIVEL de
plsqlflow/depgraph.py (`_DepGraphEngine.run`/`_run_level_batch`).

Objetivo: provar que o caminho em lote (`deps_direct_batch`, uma chamada
por owner/chunk por nivel da busca) produz o MESMO `DepGraphResult` que o
caminho por-objeto preexistente (`deps_direct`, uma chamada por objeto) --
e que o numero de chamadas do caminho em lote nao cresce com o numero de
nos do grafo. Sem conexao real, sem rede, sem `random`, sem relogio.

Duas fontes de dados, cada uma exposta por DOIS fakes irmaos (mesmos
dados, um so com `deps_direct`, outro com `deps_direct` +
`deps_direct_batch`) -- a UNICA diferenca entre o par e a presenca do
metodo em lote, detectada por `hasattr` dentro de `_DepGraphEngine.run`:
- FLOW_DEMO real: `tests/fixtures/depgraph_extract.json` (mesma fixture de
  `tests/test_depgraph_bfs.py`), inclui a fronteira real SYS.STANDARD.
- Grafo sintetico largo: 1 raiz -> N filhos no mesmo owner, gerado aqui,
  para a contagem de chamadas e o teste de chunking (N > BATCH_CHUNK_SIZE).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from plsqlflow import depgraph, extract

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "depgraph_extract.json"


def load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


FIXTURE = load_fixture()


def _kw(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k.lower(): v for k, v in row.items()}


# --------------------------------------------------------------- fake real (FLOW_DEMO)


class _FlowDemoSource:
    """Fonte de dados compartilhada pelos dois fakes FLOW_DEMO abaixo --
    mesmo padrao de `FlowDemoDepExtractor` de tests/test_depgraph_bfs.py
    (plscope_check/synonym sao dado sintetico local, fora do escopo da
    fixture JSON). Isolada numa classe base SEM `deps_direct_batch` para
    que a presenca do metodo em lote (e so ela) diferencie os dois fakes
    que herdam daqui -- `hasattr` nao pode "vazar" o metodo por engano."""

    def __init__(self, fixture: Dict[str, Any]):
        self._catalog = [extract.ObjectCatalogRow(**_kw(row)) for row in fixture["object_catalog"]]
        self._deps = fixture["deps_direct"]
        self.deps_direct_calls: List[Tuple[str, str]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        self.deps_direct_calls.append((owner.upper(), name.upper()))
        key = "{}.{}".format(owner.upper(), name.upper())
        return [extract.DepsDirectRow(**_kw(r)) for r in self._deps.get(key, [])]

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return [r for r in self._catalog if r.owner.upper() == owner.upper()]

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        if owner.upper() != "GESTAO":
            return []
        return [
            extract.PlscopeCheckRow(
                owner="GESTAO", name="FLOW_DEMO", type="PACKAGE",
                plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL",
            ),
            extract.PlscopeCheckRow(
                owner="GESTAO", name="FLOW_DEMO", type="PACKAGE BODY",
                plscope_settings="IDENTIFIERS:ALL,STATEMENTS:ALL",
            ),
        ]

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return []


class FlowDemoPerObjectExtractor(_FlowDemoSource):
    """So o caminho por-objeto: sem deps_direct_batch -- hasattr() falso,
    `_DepGraphEngine.run` cai no fallback para o nivel inteiro."""


class FlowDemoBatchExtractor(_FlowDemoSource):
    """Mesma fonte FLOW_DEMO, com deps_direct_batch: agrupa as linhas de
    TODOS os nomes pedidos em `object_list` (string "A,B,C" de
    `extract.chunk_names`) numa unica resposta, `row.name` identificando o
    objeto de origem -- exatamente o contrato de `DepsDirectBatchRow`."""

    def __init__(self, fixture: Dict[str, Any]):
        super().__init__(fixture)
        self.deps_direct_batch_calls: List[Tuple[str, str]] = []

    def deps_direct_batch(self, owner: str, object_list) -> List[extract.DepsDirectBatchRow]:
        self.deps_direct_batch_calls.append((owner.upper(), object_list))
        names = object_list.split(",") if object_list else []
        rows: List[extract.DepsDirectBatchRow] = []
        for name in names:
            key = "{}.{}".format(owner.upper(), name.upper())
            for r in self._deps.get(key, []):
                kw = _kw(r)
                rows.append(
                    extract.DepsDirectBatchRow(
                        name=name.upper(),
                        referenced_owner=kw["referenced_owner"],
                        referenced_name=kw["referenced_name"],
                        referenced_type=kw["referenced_type"],
                        dependency_type=kw["dependency_type"],
                    )
                )
        return rows


# ---------------------------------------------------------- fake sintetico generico


class _SyntheticSource:
    """Mesma logica de `SyntheticExtractor` de tests/test_depgraph_bfs.py,
    isolada numa base sem deps_direct_batch pelo mesmo motivo de
    `_FlowDemoSource`."""

    def __init__(self, deps=None, catalog=None, plscope=None, synonyms=None):
        self.deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = deps or {}
        self.catalog: Dict[str, List[extract.ObjectCatalogRow]] = catalog or {}
        self.plscope: Dict[str, List[extract.PlscopeCheckRow]] = plscope or {}
        self.synonyms: Dict[Tuple[str, str], List[extract.SynonymRow]] = synonyms or {}
        self.deps_direct_calls: List[Tuple[str, str]] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        key = (owner.upper(), name.upper())
        self.deps_direct_calls.append(key)
        return list(self.deps.get(key, []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return list(self.synonyms.get((owner.upper(), name.upper()), []))


class PerObjectExtractor(_SyntheticSource):
    """So o caminho por-objeto (sem deps_direct_batch)."""


class BatchExtractor(_SyntheticSource):
    """Caminho em lote: deps_direct_batch soma as linhas de todos os nomes
    de `object_list` sobre o mesmo dicionario `self.deps`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.deps_direct_batch_calls: List[Tuple[str, str]] = []

    def deps_direct_batch(self, owner: str, object_list) -> List[extract.DepsDirectBatchRow]:
        self.deps_direct_batch_calls.append((owner.upper(), object_list))
        names = object_list.split(",") if object_list else []
        rows: List[extract.DepsDirectBatchRow] = []
        for name in names:
            key = (owner.upper(), name.upper())
            for row in self.deps.get(key, []):
                rows.append(
                    extract.DepsDirectBatchRow(
                        name=name.upper(),
                        referenced_owner=row.referenced_owner,
                        referenced_name=row.referenced_name,
                        referenced_type=row.referenced_type,
                        dependency_type=row.dependency_type,
                    )
                )
        return rows


def _dep(owner: str, name: str, type_: str = "PACKAGE") -> extract.DepsDirectRow:
    return extract.DepsDirectRow(
        referenced_owner=owner, referenced_name=name, referenced_type=type_, dependency_type="HARD"
    )


def _cat(owner: str, name: str, type_: str = "PACKAGE", status: str = "VALID") -> extract.ObjectCatalogRow:
    return extract.ObjectCatalogRow(
        owner=owner, object_name=name, object_type=type_, status=status, last_ddl_time="2026-08-15 10:00:00"
    )


def _build_wide_synthetic(width: int) -> Tuple[Dict, Dict]:
    """1 raiz (GESTAO.ROOT) -> `width` filhos (GESTAO.CHILD000..), todos
    folha (sem dependencia propria) -- grafo de 2 niveis, largo o
    suficiente para exercitar chunking quando width > BATCH_CHUNK_SIZE."""
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "CHILD{:03d}".format(i)) for i in range(width)]}
    for i in range(width):
        deps[("GESTAO", "CHILD{:03d}".format(i))] = []
    catalog = {
        "GESTAO": [_cat("GESTAO", "ROOT")]
        + [_cat("GESTAO", "CHILD{:03d}".format(i), type_="TABLE") for i in range(width)]
    }
    return deps, catalog


# --------------------------------------------------------------------- equivalencia (FLOW_DEMO real)


def test_batch_and_per_object_paths_produce_identical_result_on_flow_demo_fixture():
    per_object = depgraph.build_dep_graph(FlowDemoPerObjectExtractor(FIXTURE), ("GESTAO", "FLOW_DEMO"))
    batch = depgraph.build_dep_graph(FlowDemoBatchExtractor(FIXTURE), ("GESTAO", "FLOW_DEMO"))

    assert batch == per_object
    # confere tambem os campos individualmente -- se a igualdade acima um
    # dia falhar por causa de um campo novo em DepGraphResult, o teste
    # ainda aponta exatamente qual lista divergiu.
    assert batch.nodes == per_object.nodes
    assert batch.edges == per_object.edges
    assert batch.needs_recompile == per_object.needs_recompile
    assert batch.truncated == per_object.truncated
    assert batch.truncation_reason == per_object.truncation_reason
    assert batch.not_expanded == per_object.not_expanded
    assert batch.stats == per_object.stats


def test_frontier_sys_standard_never_enters_any_batch_object_list():
    # SYS.STANDARD (fronteira default stop_schemas=SYS) e referenciado por
    # GESTAO.FLOW_DEMO na fixture real -- nunca pode ir parar num pedido
    # em lote, nem como owner da chamada nem como nome dentro dela.
    extractor = FlowDemoBatchExtractor(FIXTURE)
    depgraph.build_dep_graph(extractor, ("GESTAO", "FLOW_DEMO"))

    assert all(owner != "SYS" for owner, _ in extractor.deps_direct_batch_calls)
    for _, object_list in extractor.deps_direct_batch_calls:
        names = (object_list or "").split(",")
        assert "STANDARD" not in names


# ------------------------------------------------------------- contagem de chamadas


def test_batch_path_calls_do_not_grow_with_number_of_nodes():
    width = 50
    deps, catalog = _build_wide_synthetic(width)

    per_object_extractor = PerObjectExtractor(deps=deps, catalog=catalog)
    depgraph.build_dep_graph(per_object_extractor, ("GESTAO", "ROOT"))
    # per-objeto: 1 chamada para a raiz + 1 por filho = 51 idas ao banco.
    assert len(per_object_extractor.deps_direct_calls) == width + 1

    batch_extractor = BatchExtractor(deps=deps, catalog=catalog)
    depgraph.build_dep_graph(batch_extractor, ("GESTAO", "ROOT"))
    # em lote: 1 chamada para o nivel 0 (so a raiz) + 1 chamada para o
    # nivel 1 (50 filhos, mesmo owner, cabem num unico chunk de 200) = 2 --
    # nao cresce com o numero de nos do nivel.
    assert len(batch_extractor.deps_direct_batch_calls) == 2
    assert batch_extractor.deps_direct_calls == []


def test_batch_and_per_object_paths_produce_identical_result_on_wide_synthetic_graph():
    deps, catalog = _build_wide_synthetic(width=50)
    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"))
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"))

    assert batch == per_object
    assert len(batch.nodes) == 51


def test_batch_path_is_deterministic_across_runs():
    deps, catalog = _build_wide_synthetic(width=50)

    def run():
        return depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"))

    assert run() == run()


# --------------------------------------------------------------------------- chunking


def test_chunking_splits_level_above_batch_chunk_size_and_loses_no_object():
    width = extract.BATCH_CHUNK_SIZE + 50  # 250 filhos no mesmo nivel/owner
    deps, catalog = _build_wide_synthetic(width)
    extractor = BatchExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT"))

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert len(node_ids) == width + 1  # raiz + todos os filhos, nenhum perdido
    for i in range(width):
        assert "GESTAO.CHILD{:03d}".format(i) in node_ids

    # nivel 0 (so a raiz) = 1 chamada; nivel 1 (250 > BATCH_CHUNK_SIZE=200)
    # fatiado em 2 chunks = 2 chamadas; total 3 -- mais de uma chamada por
    # causa do chunking, mas ainda ORDENS DE GRANDEZA abaixo de width+1.
    assert len(extractor.deps_direct_batch_calls) == 3
    assert result.truncated is False


def test_chunking_produces_same_result_as_per_object_above_batch_chunk_size():
    width = extract.BATCH_CHUNK_SIZE + 50
    deps, catalog = _build_wide_synthetic(width)

    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"))
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"))

    assert batch == per_object


# --------------------------------------------------------------------------- fronteira sintetica


def test_dbms_prefixed_object_never_enters_batch_object_list():
    deps = {("GESTAO", "ROOT"): [_dep("SYS", "DBMS_OUTPUT")]}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT")]}
    extractor = BatchExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT"))

    dbms_node = next(n for n in result.nodes if n.object_name == "DBMS_OUTPUT")
    assert dbms_node.is_leaf is True
    assert all(owner != "SYS" for owner, _ in extractor.deps_direct_batch_calls)
    for _, object_list in extractor.deps_direct_batch_calls:
        assert "DBMS_OUTPUT" not in (object_list or "").split(",")


def test_stop_schema_object_never_enters_batch_object_list():
    deps = {("GESTAO", "ROOT"): [_dep("SYSTEM", "SOME_UTIL")]}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT")]}
    extractor = BatchExtractor(deps=deps, catalog=catalog)

    result = depgraph.build_dep_graph(extractor, ("GESTAO", "ROOT"))

    leaf = next(n for n in result.nodes if n.object_name == "SOME_UTIL")
    assert leaf.is_leaf is True
    assert all(owner != "SYSTEM" for owner, _ in extractor.deps_direct_batch_calls)
