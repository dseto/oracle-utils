"""Testes offline (T-06, contrato depgraph-granular) para a correcao do
defeito de omissao silenciosa em `_DepGraphEngine.has_plscope`/
`plscope_capabilities` (plsqlflow/depgraph.py).

Defeito corrigido: a versao antiga marcava um objeto como "coberto"
(`plscope=True`, fora de `needs_recompile`) so por ter `IDENTIFIERS:ALL`,
sem checar `STATEMENTS:ALL`. Um objeto assim tem `ALL_STATEMENTS` vazio no
banco -- todo acesso a tabela e todo SQL dinamico dele some do grafo sem
aparecer em PONTOS CEGOS nem em `needs_recompile`. Este arquivo prova que a
correcao reporta as duas capacidades separadas (`DepNode.
plscope_identifiers`/`plscope_statements`) e que cobertura completa
(`DepNode.plscope`) exige as duas.

100% sem banco, mesmo padrao de fake extractor de tests/test_depgraph_bfs.py
(arquivo autocontido -- nao importa o fake de outro modulo de teste).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from plsqlflow import depgraph, depgraph_render, extract

DepNode = depgraph.DepNode
DepGraphResult = depgraph.DepGraphResult


class SyntheticExtractor:
    """Fake minimo: um objeto raiz, sem dependencias de saida, com
    `plscope_check` parametrizavel por teste. Mesmo formato de
    tests/test_depgraph_bfs.py::SyntheticExtractor, reduzido ao necessario
    aqui."""

    def __init__(
        self,
        catalog: Dict[str, List[extract.ObjectCatalogRow]],
        plscope: Dict[str, List[extract.PlscopeCheckRow]],
        deps: Optional[Dict[Tuple[str, str], List[extract.DepsDirectRow]]] = None,
    ):
        self.catalog = catalog
        self.plscope = plscope
        self.deps = deps or {}

    def deps_direct(self, owner: str, name: str) -> List[extract.DepsDirectRow]:
        return list(self.deps.get((owner.upper(), name.upper()), []))

    def object_catalog(self, owner: str, object_list=None) -> List[extract.ObjectCatalogRow]:
        return list(self.catalog.get(owner.upper(), []))

    def plscope_check(self, owner: str) -> List[extract.PlscopeCheckRow]:
        return list(self.plscope.get(owner.upper(), []))

    def synonym(self, owner: str, name: str) -> List[extract.SynonymRow]:
        return []


def _cat(owner: str, name: str, type_: str, status: str = "VALID") -> extract.ObjectCatalogRow:
    return extract.ObjectCatalogRow(
        owner=owner, object_name=name, object_type=type_, status=status, last_ddl_time="2026-08-15 10:00:00"
    )


def _plscope_row(owner: str, name: str, type_: str, settings: Optional[str]) -> extract.PlscopeCheckRow:
    return extract.PlscopeCheckRow(owner=owner, name=name, type=type_, plscope_settings=settings)


def _build(settings: Optional[str], object_type: str = "PACKAGE") -> DepGraphResult:
    catalog = {"GESTAO": [_cat("GESTAO", "OBJ", object_type)]}
    plscope = {"GESTAO": [_plscope_row("GESTAO", "OBJ", object_type, settings)]}
    extractor = SyntheticExtractor(catalog=catalog, plscope=plscope)
    return depgraph.build_dep_graph(extractor, ("GESTAO", "OBJ"))


def _node(result: DepGraphResult) -> DepNode:
    return next(n for n in result.nodes if n.object_name == "OBJ")


# --------------------------------------------------------------------------
# caso central da regressao (T-06): so IDENTIFIERS, sem STATEMENTS
# --------------------------------------------------------------------------


def test_identifiers_only_is_not_covered_and_enters_needs_recompile():
    result = _build("IDENTIFIERS:ALL")
    node = _node(result)

    # o defeito original marcava isso como plscope=True (coberto) -- a
    # correcao tem que reportar NAO coberto, porque ALL_STATEMENTS vem
    # vazio para este objeto.
    assert node.plscope is False
    assert node.plscope_identifiers is True
    assert node.plscope_statements is False
    assert "GESTAO.OBJ" in result.needs_recompile


def test_identifiers_only_reason_is_legible_in_blind_spots():
    result = _build("IDENTIFIERS:ALL")
    text = depgraph_render.render_index_md(result, {})
    assert "### Objetos sem PL/Scope" in text
    assert "- GESTAO.OBJ (falta statements)" in text


# --------------------------------------------------------------------------
# cobertura completa
# --------------------------------------------------------------------------


def test_identifiers_and_statements_is_covered_and_out_of_needs_recompile():
    result = _build("IDENTIFIERS:ALL, STATEMENTS:ALL")
    node = _node(result)

    assert node.plscope is True
    assert node.plscope_identifiers is True
    assert node.plscope_statements is True
    assert "GESTAO.OBJ" not in result.needs_recompile

    text = depgraph_render.render_index_md(result, {})
    assert "### Objetos sem PL/Scope" not in text


# --------------------------------------------------------------------------
# so STATEMENTS, sem IDENTIFIERS -- tambem nao coberto
# --------------------------------------------------------------------------


def test_statements_only_is_not_covered():
    result = _build("STATEMENTS:ALL")
    node = _node(result)

    # sem IDENTIFIERS nao ha nenhuma atribuicao de chamada/variavel --
    # cobertura parcial na direcao oposta, tambem nao e "coberto".
    assert node.plscope is False
    assert node.plscope_identifiers is False
    assert node.plscope_statements is True
    assert "GESTAO.OBJ" in result.needs_recompile

    text = depgraph_render.render_index_md(result, {})
    assert "- GESTAO.OBJ (falta identifiers)" in text


# --------------------------------------------------------------------------
# settings vazio/None -- comportamento atual, nao pode regredir
# --------------------------------------------------------------------------


def test_empty_settings_is_not_covered():
    result = _build("")
    node = _node(result)

    assert node.plscope is False
    assert node.plscope_identifiers is False
    assert node.plscope_statements is False
    assert "GESTAO.OBJ" in result.needs_recompile


def test_none_settings_is_not_covered():
    result = _build(None)
    node = _node(result)

    assert node.plscope is False
    assert node.plscope_identifiers is False
    assert node.plscope_statements is False
    assert "GESTAO.OBJ" in result.needs_recompile


def test_no_plscope_row_at_all_is_not_covered():
    # nenhuma linha de plscope_check para o objeto (nao so settings vazio)
    catalog = {"GESTAO": [_cat("GESTAO", "OBJ", "PACKAGE")]}
    extractor = SyntheticExtractor(catalog=catalog, plscope={"GESTAO": []})
    result = depgraph.build_dep_graph(extractor, ("GESTAO", "OBJ"))
    node = _node(result)

    assert node.plscope is False
    assert node.plscope_identifiers is False
    assert node.plscope_statements is False
    assert "GESTAO.OBJ" in result.needs_recompile


# --------------------------------------------------------------------------
# tipo de objeto que nao e PL/SQL -- a pergunta nao se aplica
# --------------------------------------------------------------------------


def test_table_type_never_enters_needs_recompile_regardless_of_settings():
    result = _build("IDENTIFIERS:ALL", object_type="TABLE")
    node = _node(result)

    assert node.plscope is False
    assert node.plscope_identifiers is False
    assert node.plscope_statements is False
    assert "GESTAO.OBJ" not in result.needs_recompile


def test_view_type_never_enters_needs_recompile_regardless_of_settings():
    result = _build("IDENTIFIERS:ALL, STATEMENTS:ALL", object_type="VIEW")

    assert "GESTAO.OBJ" not in result.needs_recompile


# --------------------------------------------------------------------------
# variacoes de formatacao (espaco extra, ordem invertida, minusculas)
# --------------------------------------------------------------------------


def test_extra_whitespace_is_tolerated():
    result = _build("IDENTIFIERS:ALL,  STATEMENTS:ALL")
    node = _node(result)
    assert node.plscope is True


def test_reversed_order_is_tolerated():
    result = _build("STATEMENTS:ALL, IDENTIFIERS:ALL")
    node = _node(result)
    assert node.plscope is True
    assert node.plscope_identifiers is True
    assert node.plscope_statements is True


def test_lowercase_is_tolerated():
    result = _build("identifiers:all, statements:all")
    node = _node(result)
    assert node.plscope is True


def test_lowercase_identifiers_only_is_not_covered():
    result = _build("identifiers:all")
    node = _node(result)
    assert node.plscope is False
    assert node.plscope_identifiers is True
    assert node.plscope_statements is False
    assert "GESTAO.OBJ" in result.needs_recompile


# --------------------------------------------------------------------------
# recompile.sql sempre pede as duas flags, mesmo quando so uma falta
# (ja era o comportamento de plsqlflow/depgraph_render.py -- confirmado
# aqui como parte da prova do T-06, item 4 da tarefa)
# --------------------------------------------------------------------------


def test_recompile_sql_requests_both_flags_even_when_only_statements_missing():
    result = _build("IDENTIFIERS:ALL")
    node = _node(result)
    nodes_by_ref = {"GESTAO.OBJ": node}

    sql = depgraph_render.render_recompile_sql(result, nodes_by_ref)

    assert "PLSCOPE_SETTINGS='IDENTIFIERS:ALL, STATEMENTS:ALL'" in sql
