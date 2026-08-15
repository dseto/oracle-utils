"""Testes offline (T-02, contrato depgraph-scale) para os limites de tamanho
(`max_objects`/`max_depth`) e a fronteira de schema (`stop_schemas`,
prefixo `DBMS_`/`UTL_`) de `plsqlflow/depgraph.py` sob o corte por NIVEL
introduzido pela T-01 (`_DepGraphEngine.run`/`_run_level_batch`).

O que este arquivo prova (spec.md, Escopo item 2):
- com o MESMO `max_objects`/`max_depth`, o caminho em lote
  (`deps_direct_batch`) e o caminho por-objeto preexistente (`deps_direct`)
  produzem `DepGraphResult` IDENTICO -- mesmos nos, mesmo `not_expanded`,
  mesmo `truncated`, mesmo `truncation_reason`;
- a fronteira (`stop_schemas`/`DBMS_`/`UTL_`) nunca entra num `object_list`
  enviado ao extractor nem gera `object_catalog`/`plscope_check` para o
  proprio objeto de fronteira, mesmo quando ele aparece no meio de um
  nivel largo junto com objetos normais do mesmo owner;
- `truncated=True` nunca vem sem `truncation_reason` nem com `not_expanded`
  vazio (contrato de `DepGraphResult`, ver seu docstring).

Por que o corte por nivel pode divergir (a causa raiz do defeito, ja
identificada no enunciado da tarefa e documentada agora no docstring de
`_run_level_batch`): `_process_object` decide o corte de `max_objects`
checando `len(self.nodes) >= self.max_objects` no MOMENTO em que cada
objeto e processado -- ou seja, a ORDEM DE PROCESSAMENTO do nivel decide
quem fica de fora. O caminho por-objeto sempre processa o nivel em ordem
FIFO (ordem de enfileiramento). Enquanto o nivel inteiro vem de UM SO
objeto-pai, essa ordem FIFO ja e `(owner, name)` porque `_process_object`
ordena `dep_rows` antes de enfileirar -- entao um nivel largo de UM UNICO
owner/pai (cenario 1 abaixo) NAO expõe divergencia nenhuma: reordenar por
`(owner, name)` uma lista que ja esta nessa ordem e um no-op. A divergencia
so aparece quando o nivel e a UNIAO de filhos de VARIOS pais processados em
sequencia (cenario 2) -- a ordem FIFO resultante ("todos os filhos do
pai A, depois todos os filhos do pai B") pode nao ser globalmente ordenada
por `(owner, name)`, e so ai um reordenamento (o bug do T-01) troca quem
"ganha a vaga" antes do corte.

Sem conexao real, sem rede, sem `random`, sem relogio.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from plsqlflow import depgraph, extract


# --------------------------------------------------------------------------
# Fakes: mesma fonte de dados, um par por presenca/ausencia de
# deps_direct_batch (mesmo padrao de tests/test_depgraph_bfs_batch.py) --
# a UNICA diferenca entre PerObjectExtractor e BatchExtractor e o metodo em
# lote, para que `hasattr(extractor, "deps_direct_batch")` seja a unica
# coisa que muda qual caminho `_DepGraphEngine.run` usa.
# --------------------------------------------------------------------------


class _Source:
    def __init__(self, deps=None, catalog=None, plscope=None, synonyms=None):
        self.deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = deps or {}
        self.catalog: Dict[str, List[extract.ObjectCatalogRow]] = catalog or {}
        self.plscope: Dict[str, List[extract.PlscopeCheckRow]] = plscope or {}
        self.synonyms: Dict[Tuple[str, str], List[extract.SynonymRow]] = synonyms or {}
        self.deps_direct_calls: List[Tuple[str, str]] = []
        self.object_catalog_calls: List[str] = []
        self.plscope_check_calls: List[str] = []

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        key = (owner.upper(), name.upper())
        self.deps_direct_calls.append(key)
        return list(self.deps.get(key, []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        self.object_catalog_calls.append(owner.upper())
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        self.plscope_check_calls.append(owner.upper())
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return list(self.synonyms.get((owner.upper(), name.upper()), []))


class PerObjectExtractor(_Source):
    """So o caminho por-objeto: sem deps_direct_batch."""


class BatchExtractor(_Source):
    """Mesma fonte, com deps_direct_batch: soma as linhas de todos os
    nomes de `object_list` sobre o mesmo dicionario `self.deps`."""

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


# --------------------------------------------------------------------------
# Cenario 1: 1 raiz -> 30 filhos no MESMO owner (max_objects=10).
# --------------------------------------------------------------------------


def _build_wide_single_owner(width: int) -> Tuple[Dict, Dict]:
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "CHILD{:02d}".format(i)) for i in range(width)]}
    catalog = {
        "GESTAO": [_cat("GESTAO", "ROOT")]
        + [_cat("GESTAO", "CHILD{:02d}".format(i), type_="TABLE") for i in range(width)]
    }
    return deps, catalog


def test_max_objects_cuts_mid_level_single_owner_batch_matches_per_object():
    # Nota (ver docstring do modulo): com UM SO pai/owner contribuindo para
    # o nivel, a ordem FIFO ja e `(owner, name)` -- este cenario nao expoe
    # o defeito do T-01 sozinho (reordenar uma lista ja ordenada e um
    # no-op), mas e o cenario minimo pedido pela tarefa e continua sendo
    # uma regressao valida: prova que o corte simples nao regrediu.
    deps, catalog = _build_wide_single_owner(30)

    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=10)
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=10)

    assert batch == per_object
    assert len(batch.nodes) == 10
    assert batch.truncated is True
    assert batch.truncation_reason is not None
    assert "max_objects" in batch.truncation_reason
    assert len(batch.not_expanded) == 21
    # os 9 primeiros filhos (ordem alfabetica = ordem FIFO) entram; o resto
    # fica de fora -- identico nos dois caminhos.
    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in batch.nodes}
    assert node_ids == {"GESTAO.ROOT"} | {"GESTAO.CHILD{:02d}".format(i) for i in range(9)}
    assert set(batch.not_expanded) == {"GESTAO.CHILD{:02d}".format(i) for i in range(9, 30)}


# --------------------------------------------------------------------------
# Cenario 2: filhos espalhados por DOIS owners, vindos de DOIS pais
# diferentes -- e aqui que o agrupamento por owner do T-01 divergia da
# ordem FIFO (ver docstring do modulo para o porque).
# --------------------------------------------------------------------------


def _build_two_owner_branch() -> Tuple[Dict, Dict]:
    """GESTAO.ROOT -> GESTAO.P1, GESTAO.P2 (mesmo owner, P1 antes de P2 em
    ordem alfabetica -- processados nessa ordem no nivel 1). P1 -> 10
    filhos em ZOWN.C00..C09; P2 -> 10 filhos em AOWN.C00..C09. No nivel 2,
    a ordem FIFO (por-objeto e, apos o conserto, tambem em lote) e "todos
    os filhos de P1 (ZOWN), depois todos os filhos de P2 (AOWN)" -- que
    NAO e a mesma coisa que ordenar o nivel inteiro por `(owner, name)`
    (isso colocaria AOWN antes de ZOWN, pois 'AOWN' < 'ZOWN')."""
    deps = {
        ("GESTAO", "ROOT"): [_dep("GESTAO", "P1"), _dep("GESTAO", "P2")],
        ("GESTAO", "P1"): [_dep("ZOWN", "C{:02d}".format(i)) for i in range(10)],
        ("GESTAO", "P2"): [_dep("AOWN", "C{:02d}".format(i)) for i in range(10)],
    }
    catalog = {
        "GESTAO": [_cat("GESTAO", "ROOT"), _cat("GESTAO", "P1"), _cat("GESTAO", "P2")],
        "ZOWN": [_cat("ZOWN", "C{:02d}".format(i), type_="TABLE") for i in range(10)],
        "AOWN": [_cat("AOWN", "C{:02d}".format(i), type_="TABLE") for i in range(10)],
    }
    return deps, catalog


def test_max_objects_cuts_mid_level_two_owners_batch_matches_per_object():
    deps, catalog = _build_two_owner_branch()

    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=8)
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=8)

    assert batch == per_object
    assert len(batch.nodes) == 8
    assert batch.truncated is True
    assert "max_objects" in (batch.truncation_reason or "")

    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in batch.nodes}
    # ROOT, P1, P2 (3) + os 5 primeiros filhos de P1 em ordem FIFO
    # (ZOWN.C00..C04) -- os filhos de P2 (AOWN) nao cabem, pois P1 (e seus
    # filhos) e processado ANTES de P2 na fila.
    expected_nodes = {"GESTAO.ROOT", "GESTAO.P1", "GESTAO.P2"} | {"ZOWN.C{:02d}".format(i) for i in range(5)}
    assert node_ids == expected_nodes

    expected_not_expanded = {"ZOWN.C{:02d}".format(i) for i in range(5, 10)} | {
        "AOWN.C{:02d}".format(i) for i in range(10)
    }
    assert set(batch.not_expanded) == expected_not_expanded
    assert len(batch.not_expanded) == 15


# --------------------------------------------------------------------------
# Cenario 3: max_depth cortando (mesma paridade exigida).
# --------------------------------------------------------------------------


def test_max_depth_cuts_batch_matches_per_object():
    deps, catalog = _build_two_owner_branch()

    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_depth=2)
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_depth=2)

    assert batch == per_object
    # ROOT (depth 0), P1/P2 (depth 1) expandem normalmente; os 20 filhos
    # (depth 2) entram como no mas nao expandem (2 >= max_depth=2).
    assert len(batch.nodes) == 23
    assert batch.truncated is True
    assert "max_depth" in (batch.truncation_reason or "")
    expected_not_expanded = {"ZOWN.C{:02d}".format(i) for i in range(10)} | {
        "AOWN.C{:02d}".format(i) for i in range(10)
    }
    assert set(batch.not_expanded) == expected_not_expanded


# --------------------------------------------------------------------------
# Cenario 4: fronteira (stop_schemas + prefixo DBMS_/UTL_) no meio de um
# nivel misto com objetos normais do MESMO owner -- nunca consultada.
# --------------------------------------------------------------------------


def _build_frontier_mixed_level() -> Dict:
    deps = {
        ("GESTAO", "ROOT"): [
            _dep("GESTAO", "NORMAL_CHILD"),
            _dep("GESTAO", "DBMS_HELPER"),  # fronteira por prefixo, MESMO owner do normal
            _dep("SYS", "STANDARD"),  # fronteira por stop_schemas default
            _dep("STOPOWNER", "SOME_OBJ"),  # fronteira por stop_schemas custom
        ],
    }
    catalog = {
        "GESTAO": [_cat("GESTAO", "ROOT"), _cat("GESTAO", "NORMAL_CHILD", type_="TABLE")],
    }
    return {"deps": deps, "catalog": catalog}


def _assert_frontier_never_queried(extractor, result) -> None:
    node_ids = {"{}.{}".format(n.owner, n.object_name) for n in result.nodes}
    assert node_ids == {
        "GESTAO.ROOT",
        "GESTAO.NORMAL_CHILD",
        "GESTAO.DBMS_HELPER",
        "SYS.STANDARD",
        "STOPOWNER.SOME_OBJ",
    }
    for ref in ("GESTAO.DBMS_HELPER", "SYS.STANDARD", "STOPOWNER.SOME_OBJ"):
        node = next(n for n in result.nodes if "{}.{}".format(n.owner, n.object_name) == ref)
        assert node.is_leaf is True

    assert "SYS" not in extractor.object_catalog_calls
    assert "STOPOWNER" not in extractor.object_catalog_calls
    assert "SYS" not in extractor.plscope_check_calls
    assert "STOPOWNER" not in extractor.plscope_check_calls


def test_frontier_objects_never_queried_in_mixed_level_batch_path():
    fixture = _build_frontier_mixed_level()
    extractor = BatchExtractor(deps=fixture["deps"], catalog=fixture["catalog"])

    result = depgraph.build_dep_graph(
        extractor, ("GESTAO", "ROOT"), stop_schemas=("SYS", "SYSTEM", "STOPOWNER")
    )

    _assert_frontier_never_queried(extractor, result)
    # nenhuma chamada em lote toca owner de fronteira...
    assert all(owner != "SYS" for owner, _ in extractor.deps_direct_batch_calls)
    assert all(owner != "STOPOWNER" for owner, _ in extractor.deps_direct_batch_calls)
    # ...e, dentro do owner GESTAO (misto), DBMS_HELPER nunca aparece no
    # object_list, mesmo NORMAL_CHILD aparecendo.
    gestao_names = set()
    for owner, object_list in extractor.deps_direct_batch_calls:
        if owner == "GESTAO":
            gestao_names.update((object_list or "").split(","))
    assert "DBMS_HELPER" not in gestao_names
    assert "NORMAL_CHILD" in gestao_names


def test_frontier_objects_never_queried_in_mixed_level_per_object_path():
    fixture = _build_frontier_mixed_level()
    extractor = PerObjectExtractor(deps=fixture["deps"], catalog=fixture["catalog"])

    result = depgraph.build_dep_graph(
        extractor, ("GESTAO", "ROOT"), stop_schemas=("SYS", "SYSTEM", "STOPOWNER")
    )

    _assert_frontier_never_queried(extractor, result)
    assert ("SYS", "STANDARD") not in extractor.deps_direct_calls
    assert ("STOPOWNER", "SOME_OBJ") not in extractor.deps_direct_calls
    assert ("GESTAO", "DBMS_HELPER") not in extractor.deps_direct_calls


def test_frontier_mixed_level_batch_matches_per_object():
    fixture = _build_frontier_mixed_level()
    per_object = depgraph.build_dep_graph(
        PerObjectExtractor(deps=fixture["deps"], catalog=fixture["catalog"]),
        ("GESTAO", "ROOT"),
        stop_schemas=("SYS", "SYSTEM", "STOPOWNER"),
    )
    batch = depgraph.build_dep_graph(
        BatchExtractor(deps=fixture["deps"], catalog=fixture["catalog"]),
        ("GESTAO", "ROOT"),
        stop_schemas=("SYS", "SYSTEM", "STOPOWNER"),
    )
    assert batch == per_object


# --------------------------------------------------------------------------
# Cenario 5: truncated=True nunca e silencioso (reason + not_expanded).
# --------------------------------------------------------------------------


def test_truncation_is_never_silent_across_scenarios_and_both_paths():
    wide_deps, wide_catalog = _build_wide_single_owner(30)
    branch_deps, branch_catalog = _build_two_owner_branch()

    scenarios = [
        (wide_deps, wide_catalog, ("GESTAO", "ROOT"), dict(max_objects=10)),
        (branch_deps, branch_catalog, ("GESTAO", "ROOT"), dict(max_objects=8)),
        (branch_deps, branch_catalog, ("GESTAO", "ROOT"), dict(max_depth=2)),
        (wide_deps, wide_catalog, ("GESTAO", "ROOT"), dict(max_objects=500)),  # nao trunca
    ]

    for deps, catalog, root, kwargs in scenarios:
        for extractor_cls in (PerObjectExtractor, BatchExtractor):
            result = depgraph.build_dep_graph(extractor_cls(deps=deps, catalog=catalog), root, **kwargs)
            if result.truncated:
                assert result.truncation_reason is not None
                assert len(result.not_expanded) > 0
            else:
                assert result.truncation_reason is None
                assert result.not_expanded == []


# --------------------------------------------------------------------------
# Bordas: max_objects=0 e max_objects=1.
# --------------------------------------------------------------------------


def test_max_objects_zero_boundary_no_exception_and_no_silent_truncation():
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "CHILD")]}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT"), _cat("GESTAO", "CHILD", type_="TABLE")]}

    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=0)
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=0)

    assert batch == per_object
    # com max_objects=0 nem a raiz cabe -- truncamento e esperado, mas
    # nunca silencioso.
    assert batch.nodes == []
    assert batch.truncated is True
    assert batch.truncation_reason is not None
    assert batch.not_expanded == ["GESTAO.ROOT"]


def test_max_objects_one_boundary_no_exception_and_no_silent_truncation():
    deps = {("GESTAO", "ROOT"): [_dep("GESTAO", "CHILD")]}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT"), _cat("GESTAO", "CHILD", type_="TABLE")]}

    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=1)
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=1)

    assert batch == per_object
    # a raiz cabe (1 no), o filho nao -- truncado com motivo, nao vazio.
    assert len(batch.nodes) == 1
    assert batch.nodes[0].object_name == "ROOT"
    assert batch.truncated is True
    assert batch.truncation_reason is not None
    assert batch.not_expanded == ["GESTAO.CHILD"]


def test_max_objects_one_boundary_without_children_does_not_truncate():
    # Borda adicional: max_objects=1 so trunca se de fato houver algo a
    # cortar -- uma raiz sem filhos nao deve gerar truncated=True "sem
    # motivo" (a raiz cabe exatamente no limite e nao ha mais nada).
    deps: Dict[Tuple[str, str], List[extract.DepsDirectRow]] = {("GESTAO", "ROOT"): []}
    catalog = {"GESTAO": [_cat("GESTAO", "ROOT")]}

    per_object = depgraph.build_dep_graph(PerObjectExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=1)
    batch = depgraph.build_dep_graph(BatchExtractor(deps=deps, catalog=catalog), ("GESTAO", "ROOT"), max_objects=1)

    assert batch == per_object
    assert len(batch.nodes) == 1
    assert batch.truncated is False
    assert batch.truncation_reason is None
    assert batch.not_expanded == []
