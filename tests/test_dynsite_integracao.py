"""Testes offline (T-07, contrato dynsql-dossie) -- integracao do dossie
estatico de SQL dinamico (`dynamic_sql.jsonl`/`SQL-DINAMICO.md`) ao
pipeline existente do `depgraph --granular`:

    plsqlflow/cli.py             `_build_dynamic_sql_records` (monta os
                                  registros a partir de `ProcGraphResult`)
    plsqlflow/procgraph_render.py `render_graph`/`render_node_md`/
                                  `render_index_md` (grava os dois arquivos
                                  do dossie, referencia sem duplicar)

Nenhum destes casos toca rede/banco -- reusa os fakes ja existentes de
tests/test_procgraph_bfs.py e tests/test_procgraph_access.py: a fixture
real de GESTAO.FLOW_DEMO.RUN_DYNAMIC tem DOIS `EXECUTE IMMEDIATE` reais
(L28 100% literal -> confidence "exact", nunca vira sitio do dossie; L31
montado com `p_tag` -> confidence "partial", o unico que vira sitio nestes
testes)."""
from __future__ import annotations

from plsqlflow import cli
from plsqlflow.procgraph import ProcGraphResult, ProcNode, build_proc_graph
from plsqlflow.procgraph_render import render_graph

from tests.test_procgraph_access import (
    FakeExtractorWithSource,
    _CatalogDepExtractor,
    _flow_demo_source_by_ref,
)
from tests.test_procgraph_bfs import FakeExtractor, _load_fixture

FULL_CAPABILITIES = {"resolve_owner": True, "object_wrapped": True, "triggers": True, "source": True}


def _run_dynamic_result_with_source():
    """Fechamento real GESTAO.FLOW_DEMO.MAIN -> RUN_DYNAMIC, extractor COM
    capacidade `source` (mesmo fixture de
    tests/test_procgraph_access.py::test_build_proc_graph_attributes_both_
    dynamic_sql_lines_to_run_dynamic_never_main)."""
    fixture = _load_fixture()
    extractor = FakeExtractorWithSource(fixture, _flow_demo_source_by_ref())
    dep_extractor = _CatalogDepExtractor(["FLOW_DEMO_LOG", "FLOW_DEMO_AUDIT"])
    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"), dep_extractor=dep_extractor)
    return result, extractor


def _run_dynamic_result_without_source():
    """Mesmo fechamento, extractor SEM capacidade `source` (FakeExtractor
    puro de tests/test_procgraph_bfs.py -- so `plscope_identifiers`/
    `plscope_statements`/`resolve_owner`)."""
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))
    return result, extractor


def _minimal_result_no_dynamic_sql() -> ProcGraphResult:
    return ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram", is_leaf=True)],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )


# --------------------------------------------------------------------------
# caso 1 -- sem sitio: dossie vazio-mas-declarado, nunca ausente
# --------------------------------------------------------------------------


def test_dossie_written_empty_and_declared_when_no_dynamic_sql_site(tmp_path):
    result = _minimal_result_no_dynamic_sql()

    written = render_graph(result, tmp_path / "out", {"capabilities": FULL_CAPABILITIES})

    dynsql_path = tmp_path / "out" / "dynamic_sql.jsonl"
    md_path = tmp_path / "out" / "SQL-DINAMICO.md"
    assert dynsql_path in written
    assert md_path in written
    assert dynsql_path.read_text(encoding="utf-8") == ""
    md_text = md_path.read_text(encoding="utf-8")
    assert "Nenhum sitio de SQL dinamico neste fechamento." in md_text


def test_dossie_written_empty_when_dynamic_sql_records_omitted_entirely(tmp_path):
    # Mesma prova acima, mas SEM passar `dynamic_sql_records` (default
    # None) -- garante que chamadores antigos (testes existentes que ainda
    # chamam render_graph(result, out_dir, meta_params) sem o 4o argumento)
    # continuam recebendo o dossie vazio-mas-declarado, nunca uma excecao.
    result = _minimal_result_no_dynamic_sql()

    render_graph(result, tmp_path / "out", {"capabilities": FULL_CAPABILITIES})

    assert (tmp_path / "out" / "dynamic_sql.jsonl").read_text(encoding="utf-8") == ""


# --------------------------------------------------------------------------
# caso 2 -- COM sitio real: T-01 de verdade rodou (nao um stub)
# --------------------------------------------------------------------------


def test_build_dynamic_sql_records_runs_real_t01_classification():
    result, extractor = _run_dynamic_result_with_source()

    records = cli._build_dynamic_sql_records(result, extractor)

    # L28 e 100% literal -> confidence "exact" -> NUNCA vira sitio do
    # dossie (criterio identico ao de blind_spots, procgraph.py ~1654).
    # L31 e o UNICO candidato (confidence "partial").
    assert len(records) == 1
    record = records[0]
    assert record.owner == "GESTAO"
    assert record.object_name == "FLOW_DEMO"
    assert record.subprogram == "RUN_DYNAMIC"
    assert record.line == 31
    assert record.site_id == "GESTAO.FLOW_DEMO.RUN_DYNAMIC#31"

    # T-01 real (dynsite.classify_dynamic_site): EXECUTE IMMEDIATE nu, sem
    # INTO/USING -> regra de desempate do modulo, NAO_DETERMINAVEL com
    # pode_invocar_procedure=True -- prova que a classificacao de verdade
    # rodou (um stub nao produziria esta prova textual especifica).
    assert record.categoria_provada == "NAO_DETERMINAVEL"
    assert "nao restringe a forma do comando" in record.categoria_prova
    assert record.pode_invocar_procedure is True
    assert record.exec_form == "EXECUTE_IMMEDIATE"

    # T-02 real (dynsite_template.reconstruct_template): variavel montada
    # identificada (`v_sql`), template reconstruido com uma lacuna (p_tag).
    assert record.variavel_montada.upper() == "V_SQL"
    assert record.reconstrucao == "completa"
    assert len(record.lacunas) == 1
    assert record.chave_correlacao is not None
    assert record.chave_correlacao.startswith("INSERT INTO flow_demo_log")


def test_records_write_into_dynamic_sql_jsonl(tmp_path):
    result, extractor = _run_dynamic_result_with_source()
    records = cli._build_dynamic_sql_records(result, extractor)

    render_graph(
        result,
        tmp_path / "out",
        {"capabilities": FULL_CAPABILITIES, "root_ref": "GESTAO.FLOW_DEMO.MAIN"},
        dynamic_sql_records=records,
    )

    import json

    lines = (tmp_path / "out" / "dynamic_sql.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["site_id"] == "GESTAO.FLOW_DEMO.RUN_DYNAMIC#31"
    assert payload["categoria_provada"] == "NAO_DETERMINAVEL"

    md_text = (tmp_path / "out" / "SQL-DINAMICO.md").read_text(encoding="utf-8")
    assert "GESTAO.FLOW_DEMO.RUN_DYNAMIC#31" in md_text


# --------------------------------------------------------------------------
# caso 3 -- extractor SEM capacidade `source`: sitio nunca some
# --------------------------------------------------------------------------


def test_site_survives_without_source_capability_as_nao_determinavel():
    result, extractor = _run_dynamic_result_without_source()
    assert not hasattr(extractor, "source")

    records = cli._build_dynamic_sql_records(result, extractor)

    # Sem capacidade `source`, `procgraph_access._dynamic_sql_edges` nunca
    # consegue classificar NADA -- as DUAS ocorrencias de RUN_DYNAMIC (L28
    # e L31) caem em confidence="opaque" (nao so a L31 "partial" do caso
    # com fonte disponivel) -- as duas batem o criterio ("partial","opaque")
    # e as duas tem que aparecer, nenhuma pode sumir.
    assert len(records) == 2
    by_line = {r.line: r for r in records}
    assert set(by_line) == {28, 31}
    for record in records:
        assert record.owner == "GESTAO"
        assert record.object_name == "FLOW_DEMO"
        assert record.subprogram == "RUN_DYNAMIC"
        assert record.categoria_provada == "NAO_DETERMINAVEL"
        assert "codigo-fonte indisponivel" in record.categoria_prova
        assert record.reconstrucao == "parcial"
        assert record.template == []
        assert record.lacunas == []
        assert record.pode_invocar_procedure is True


# --------------------------------------------------------------------------
# caso 4 -- node .md: categoria + link, sem duplicar template/lacunas
# --------------------------------------------------------------------------


def test_node_md_shows_categoria_and_link_without_duplicating_template(tmp_path):
    result, extractor = _run_dynamic_result_with_source()
    records = cli._build_dynamic_sql_records(result, extractor)

    render_graph(
        result,
        tmp_path / "out",
        {"capabilities": FULL_CAPABILITIES, "root_ref": "GESTAO.FLOW_DEMO.MAIN"},
        dynamic_sql_records=records,
    )

    node_text = (tmp_path / "out" / "nodes" / "GESTAO.FLOW_DEMO.RUN_DYNAMIC.md").read_text(encoding="utf-8")

    assert "## SQL Dinâmico" in node_text
    assert "L31" in node_text
    assert "categoria_provada=NAO_DETERMINAVEL" in node_text
    assert "pode_invocar_procedure=sim" in node_text
    assert "site_id=GESTAO.FLOW_DEMO.RUN_DYNAMIC#31" in node_text
    assert "dynamic_sql.jsonl" in node_text and "SQL-DINAMICO.md" in node_text

    # backlog secao 5.1: node .md carrega SO categoria_provada +
    # pode_invocar_procedure + link -- template/lacunas moram so no
    # dossie, nunca duplicados aqui.
    assert "### Lacunas" not in node_text
    assert "reconstrucao_motivo" not in node_text
    assert "chave_correlacao" not in node_text


# --------------------------------------------------------------------------
# caso 5 -- INDEX.md, PONTOS CEGOS: link pro dossie na linha correspondente
# --------------------------------------------------------------------------


def test_index_md_pontos_cegos_links_to_dossie(tmp_path):
    result, extractor = _run_dynamic_result_with_source()
    records = cli._build_dynamic_sql_records(result, extractor)

    render_graph(
        result,
        tmp_path / "out",
        {"capabilities": FULL_CAPABILITIES, "root_ref": "GESTAO.FLOW_DEMO.MAIN"},
        dynamic_sql_records=records,
    )

    index_text = (tmp_path / "out" / "INDEX.md").read_text(encoding="utf-8")
    pontos_cegos = index_text.split("## PONTOS CEGOS", 1)[1]

    assert "GESTAO.FLOW_DEMO.RUN_DYNAMIC" in pontos_cegos
    assert "L31" in pontos_cegos
    assert "[partial]" in pontos_cegos
    assert "site_id=GESTAO.FLOW_DEMO.RUN_DYNAMIC#31" in pontos_cegos
    assert "dynamic_sql.jsonl" in pontos_cegos and "SQL-DINAMICO.md" in pontos_cegos


# --------------------------------------------------------------------------
# caso 6 -- NAO-REGRESSAO: modo objeto e modo flow seguem intactos
# --------------------------------------------------------------------------


def test_flow_mode_golden_untouched_by_t07(tmp_path):
    # tests/test_plsqlflow_golden.py::test_mermaid_matches_golden_file_
    # byte_for_byte e test_blind_spots_has_exactly_one_unresolved_dynsql_
    # at_line_31 -- rodados AQUI DENTRO, sem nenhuma alteracao neles, para
    # provar que plsqlflow/graph.py e plsqlflow/report.py (nunca tocados
    # por T-07, stop condition do contrato) continuam produzindo a mesma
    # saida byte a byte.
    from tests import test_plsqlflow_golden

    test_plsqlflow_golden.test_mermaid_matches_golden_file_byte_for_byte()
    test_plsqlflow_golden.test_blind_spots_has_exactly_one_unresolved_dynsql_at_line_31()


def test_object_mode_golden_untouched_by_t07(monkeypatch, tmp_path, capsys):
    # tests/test_depgraph_cli.py::test_depgraph_success_returns_zero_and_
    # writes_files -- rodado AQUI DENTRO, sem alteracao, para provar que o
    # subcomando `depgraph` SEM --granular continua identico (T-07 so
    # mexe no caminho --granular; ver `_depgraph_granular_main`, unica
    # funcao alterada em plsqlflow/cli.py por esta tarefa).
    from tests import test_depgraph_cli

    test_depgraph_cli.test_depgraph_success_returns_zero_and_writes_files(monkeypatch, tmp_path, capsys)


# --------------------------------------------------------------------------
# caso 7 -- lacuna NAO_DETERMINADO, motivo honesto sobre a limitacao (T-07
# nao consulta ALL_ARGUMENTS/parametros formais reais)
# --------------------------------------------------------------------------


def test_gap_origin_is_honestly_undetermined_without_formal_params():
    result, extractor = _run_dynamic_result_with_source()
    records = cli._build_dynamic_sql_records(result, extractor)
    record = records[0]

    assert len(record.lacunas) == 1
    lacuna = record.lacunas[0]

    # `p_tag` E de fato um parametro formal REAL de run_dynamic(p_tag IN
    # VARCHAR2) -- mas esta integracao (T-07) nunca consulta ALL_ARGUMENTS
    # (fora da superficie de arquivos da tarefa), entao `classify_gap_
    # origin` roda com `formal_params=[]` e NUNCA pode provar isso: a
    # origem tem que sair NAO_DETERMINADO, nunca um palpite otimista de
    # PARAMETRO_FORMAL sem prova. E exatamente a prova de honestidade que
    # este caso de teste existe para fixar.
    assert lacuna["nome"].lower() == "p_tag"
    assert lacuna["origem"] == "NAO_DETERMINADO"
    assert lacuna["motivo"]
    assert "parametro formal" in lacuna["motivo"]
    assert "informado a classify_gap_origin" in lacuna["motivo"]
