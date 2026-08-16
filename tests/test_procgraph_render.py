"""Testes offline (T-08, contrato depgraph-granular) para
plsqlflow/procgraph_render.py -- saida em disco no grao SUBPROGRAMA e a
secao COBERTURA como obrigacao de prova (docs/plano-depgraph-granular.md,
secao 6).

Nenhum destes casos toca rede/banco: reusa os fakes ja existentes de
tests/test_procgraph_bfs.py (fixture real de GESTAO.FLOW_DEMO, ciclo
inter-package PKG_M1/PKG_M2) e tests/test_procgraph_fallback.py (cadeia
A->B(sem PL/Scope)->C, needs_recompile). Nenhum arquivo novo de fixture:
os casos de "soma quebrada" (o cerne da prova do T-08) constroem um
`ProcGraphResult` corrompido a mao -- e o unico jeito de provar que
`build_coverage`/`render_graph` levantam em vez de "consertar" o numero.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple

import pytest

from plsqlflow.attribute import Assignment, IdentifierRow
from plsqlflow.procgraph import ProcEdge, ProcGraphResult, ProcNode, build_proc_graph
from plsqlflow.procgraph_access import expand_access
from plsqlflow.procgraph_render import (
    CoverageError,
    build_coverage,
    capabilities_from_extractor,
    node_filename,
    node_ref,
    render_graph,
)
from tests.test_procgraph_access import _TriggerCycleExtractor
from tests.test_procgraph_bfs import FakeExtractor, _load_fixture
from tests.test_procgraph_fallback import FakeDepExtractor, FakeProcExtractor


def _flow_demo_result() -> ProcGraphResult:
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    return build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))


def _inter_package_cycle_result() -> ProcGraphResult:
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    return build_proc_graph(extractor, ("GESTAO", "PKG_M1", "P1"))


def _fallback_result() -> ProcGraphResult:
    proc_extractor = FakeProcExtractor()
    dep_extractor = FakeDepExtractor()
    return build_proc_graph(proc_extractor, ("GESTAO", "A", "P1"), dep_extractor=dep_extractor)


def _empty_result() -> ProcGraphResult:
    return ProcGraphResult(
        nodes=[],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={"nodes": 0, "edges": 0},
    )


FULL_CAPABILITIES = {"resolve_owner": True, "object_wrapped": True, "triggers": True, "source": True}


# --------------------------------------------------------------------------
# COBERTURA -- caso normal fecha
# --------------------------------------------------------------------------


def test_coverage_sums_close_on_golden_fixture():
    result = _flow_demo_result()

    coverage = build_coverage(result, FULL_CAPABILITIES)

    assert coverage.nodes_subprogram + coverage.nodes_object + coverage.nodes_unresolved == coverage.nodes_total
    assert coverage.calls_subprogram + coverage.calls_init_spec + coverage.calls_unattributed == coverage.calls_total
    # Defeito 2: o denominador de "statements" agora e HONESTO (statements
    # realmente lidos via PL/Scope, ver ProcGraphResult.statements_read) --
    # a soma so fecha porque `build_coverage` ja PROVOU (ou teria levantado
    # CoverageError) que cada statement lido tem aresta correspondente.
    # `statements_no_dependency` (correcao do DEFEITO BLOQUEANTE) entra na
    # mesma soma -- balde proprio, nao um extra escondido.
    assert (
        coverage.statements_read_subprogram
        + coverage.statements_read_init_spec
        + coverage.statements_no_dependency
        == coverage.statements_read_total
    )
    assert coverage.statements_read_total == len(result.statements_read)
    assert coverage.nodes_total == len(result.nodes)


def test_coverage_sums_close_on_fallback_fixture():
    # A(fino) -> B(sem PL/Scope, fallback) -> C(fino, alcancado via
    # ALL_DEPENDENCIES) -- exercita os tres graos ao mesmo tempo.
    result = _fallback_result()

    coverage = build_coverage(result, FULL_CAPABILITIES)

    assert coverage.nodes_subprogram + coverage.nodes_object + coverage.nodes_unresolved == coverage.nodes_total
    assert coverage.nodes_object >= 1
    assert "GESTAO.B" in coverage.needs_recompile


def test_coverage_lists_reasons_per_object_for_fallback_nodes():
    result = _fallback_result()

    coverage = build_coverage(result, FULL_CAPABILITIES)

    reasons_text = " ".join(coverage.reasons)
    assert "GESTAO.B" in reasons_text
    assert "PL/Scope" in reasons_text


# --------------------------------------------------------------------------
# COBERTURA -- regra dura: soma quebrada levanta, nunca "conserta"
# --------------------------------------------------------------------------


def test_coverage_raises_on_unknown_grain():
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="mystery")],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )

    with pytest.raises(CoverageError, match="objetos alcancados"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_coverage_raises_on_malformed_call_from_ref():
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[ProcEdge(from_ref="MALFORMED", to_ref="GESTAO.X.P1", edge_type="CALL", line=1)],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )

    with pytest.raises(CoverageError, match="calls"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_coverage_raises_on_malformed_statement_from_ref():
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[ProcEdge(from_ref="", to_ref="GESTAO.T1", edge_type="WRITE", line=1)],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )

    with pytest.raises(CoverageError, match="statements"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_render_graph_raises_and_writes_nothing_when_coverage_broken(tmp_path):
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="mystery")],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
    )
    out_dir = tmp_path / "broken-graph"

    with pytest.raises(CoverageError):
        render_graph(bogus, out_dir, {"capabilities": FULL_CAPABILITIES})

    # Regra dura: nenhum arquivo (nem o diretorio) pode ficar para tras --
    # "o grafo nao e gravado como valido" nao e so sobre o conteudo, e sobre
    # a existencia mesma dos arquivos.
    assert not out_dir.exists()


# --------------------------------------------------------------------------
# DEFEITO 2: reconciliacao de statements tem que partir do denominador
# honesto (statements_read), nunca de uma soma de arestas que fecha
# trivialmente quando nao ha aresta nenhuma.
# --------------------------------------------------------------------------


def test_coverage_raises_when_a_read_statement_has_no_edge_at_all():
    # Cenario construido a mao: um statement foi LIDO (entra em
    # `statements_read`, o denominador honesto) mas nenhuma aresta
    # corresponde a ele. A soma ANTIGA (edges) fechava aqui mesmo assim --
    # `stmt_edges` esta vazia, 0 == 0 -- exatamente o buraco que deixou o
    # SQL dinamico de RUN_DYNAMIC sumir sem erro (defeito 1). A soma NOVA
    # tem que quebrar.
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
        statements_read=[("GESTAO.X.P1", 5, "SELECT")],
    )

    with pytest.raises(CoverageError, match="statements"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_coverage_error_names_the_unknown_statement_type():
    # Correcao do DEFEITO BLOQUEANTE (relatorio de verificacao
    # independente): tipo GENUINAMENTE desconhecido continua levantando
    # CoverageError -- o sinal alto do Defeito 1 sobrevive intato -- mas
    # agora a mensagem tem que NOMEAR o tipo, nao so o ref/linha, para
    # quem le o erro entender o que faltou tratar.
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
        statements_read=[("GESTAO.X.P1", 5, "TIPO_QUE_NAO_EXISTE")],
    )

    with pytest.raises(CoverageError, match="TIPO_QUE_NAO_EXISTE"):
        build_coverage(bogus, FULL_CAPABILITIES)


def test_coverage_passes_when_the_only_edge_is_a_dynamic_sql_marker():
    # Contraste direto com o teste acima: o MESMO statement lido, agora
    # com a aresta marcadora que `_dynamic_sql_edges` sempre emite (mesmo
    # em nivel opaque) -- a reconciliacao tem que fechar, prova de que a
    # regra e "precisa de aresta", nao "precisa resolver 100%".
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[ProcEdge(from_ref="GESTAO.X.P1", to_ref="?", edge_type="DYNAMIC_SQL", line=5, confidence="opaque")],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=["GESTAO.X.P1 L5 [opaque] -> ?"],
        needs_recompile=[],
        stats={},
        statements_read=[("GESTAO.X.P1", 5, "EXECUTE IMMEDIATE")],
    )

    coverage = build_coverage(bogus, FULL_CAPABILITIES)
    assert coverage.statements_read_subprogram == 1
    assert coverage.statements_read_total == 1


def test_render_graph_raises_and_writes_nothing_when_statement_unrepresented(tmp_path):
    bogus = ProcGraphResult(
        nodes=[ProcNode(owner="GESTAO", object_name="X", subprogram="P1", grain="subprogram")],
        edges=[],
        truncated=False,
        truncation_reason=None,
        not_expanded=[],
        blind_spots=[],
        needs_recompile=[],
        stats={},
        statements_read=[("GESTAO.X.P1", 5, "SELECT")],
    )
    out_dir = tmp_path / "broken-graph-statements"

    with pytest.raises(CoverageError):
        render_graph(bogus, out_dir, {"capabilities": FULL_CAPABILITIES})

    assert not out_dir.exists()


def test_coverage_label_calls_fallback_edges_arestas_not_statements(tmp_path):
    # A licao literal do defeito: se uma fatia da linha continua contando
    # arestas (grao objeto/fallback, sem PL/Scope para atribuir por
    # statement), o rotulo tem que DIZER arestas -- nunca prometer
    # "statements" e entregar contagem de aresta.
    result = _fallback_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "statements lidos (PL/Scope, denominador honesto)" in index_text
    assert "arestas de acesso/estado/SQL dinamico em grao objeto" in index_text


# --------------------------------------------------------------------------
# DEFEITO BLOQUEANTE (relatorio de verificacao independente, 2026-08-16):
# COMMIT/ROLLBACK/FETCH/CLOSE (e demais tipos sem dependencia de dados por
# natureza) NAO podem mais abortar render_graph inteiro. Este e o teste
# CENTRAL exigido pela correcao: o cenario constroi exatamente o caso em
# que o codigo ANTES da correcao levantava CoverageError e gravava ZERO
# arquivos -- so um COMMIT dentro de um subprograma real, sem nenhum SQL
# dinamico envolvido.
# --------------------------------------------------------------------------


class _TransactionalBatchExtractor:
    """Fake de `procgraph.ProcExtractor`: pacote GESTAO.BATCH_PKG com UMA
    procedure (RUN_BATCH) que mistura um INSERT real (dependencia de dado
    de verdade) com COMMIT/ROLLBACK/FETCH/CLOSE (controle transacional e
    de cursor estatico, sem dependencia nenhuma) -- o padrao mais comum de
    PL/SQL batch/transacional, exatamente o alvo declarado pelo usuario
    ("processos gigantes, com centenas de sub chamadas")."""

    def __init__(self) -> None:
        self._identifiers = [
            {"usage_id": 1, "usage_context_id": 0, "line": 1, "col": 1, "name": "BATCH_PKG", "type": "PACKAGE", "usage": "DEFINITION", "signature": None},
            {"usage_id": 2, "usage_context_id": 1, "line": 3, "col": 1, "name": "RUN_BATCH", "type": "PROCEDURE", "usage": "DEFINITION", "signature": "SIG_RUN_BATCH"},
            # tabela referenciada pelo INSERT (usage_id=3) -- filha direta
            # do statement (usage_context_id=3, mesma convencao do resto
            # da suite, ver _TriggerCycleExtractor em test_procgraph_access.py).
            {"usage_id": 4, "usage_context_id": 3, "line": 4, "col": 10, "name": "BATCH_LOG", "type": "TABLE", "usage": "REFERENCE", "signature": None},
        ]
        self._statements = [
            {"usage_id": 3, "usage_context_id": 2, "line": 4, "stmt_type": "INSERT"},
            {"usage_id": 5, "usage_context_id": 2, "line": 5, "stmt_type": "COMMIT"},
            {"usage_id": 6, "usage_context_id": 2, "line": 6, "stmt_type": "ROLLBACK"},
            {"usage_id": 7, "usage_context_id": 2, "line": 7, "stmt_type": "FETCH"},
            {"usage_id": 8, "usage_context_id": 2, "line": 8, "stmt_type": "CLOSE"},
        ]

    def _matches(self, owner: str, object_name: str) -> bool:
        return (owner.upper(), object_name.upper()) == ("GESTAO", "BATCH_PKG")

    def plscope_identifiers(self, owner: str, object_name: str) -> List[Any]:
        if not self._matches(owner, object_name):
            return []
        return [SimpleNamespace(object_type="PACKAGE BODY", **row) for row in self._identifiers]

    def plscope_statements(self, owner: str, object_name: str) -> List[Any]:
        if not self._matches(owner, object_name):
            return []
        return [SimpleNamespace(object_type="PACKAGE BODY", **row) for row in self._statements]

    def resolve_owner(self, signature: str) -> Optional[Tuple[str, str]]:
        return None


def test_build_coverage_accepts_commit_rollback_fetch_close_without_raising():
    # A prova central em isolamento (sem tocar disco): build_coverage NAO
    # levanta mais para este cenario -- os quatro statements sem
    # dependencia (COMMIT/ROLLBACK/FETCH/CLOSE) caem no balde proprio, o
    # INSERT continua no balde normal.
    extractor = _TransactionalBatchExtractor()
    result = build_proc_graph(extractor, ("GESTAO", "BATCH_PKG", "RUN_BATCH"))

    coverage = build_coverage(result, {"resolve_owner": True})
    assert coverage.statements_no_dependency == 4
    assert coverage.statements_read_subprogram == 1  # so o INSERT
    assert coverage.statements_read_total == 5


def test_render_graph_writes_files_normally_when_subprogram_has_commit(tmp_path):
    # ANTES da correcao: este cenario levantava CoverageError e
    # render_graph gravava ZERO arquivos (nem o diretorio). DEPOIS: os
    # arquivos sao gravados normalmente.
    extractor = _TransactionalBatchExtractor()
    result = build_proc_graph(extractor, ("GESTAO", "BATCH_PKG", "RUN_BATCH"))
    out_dir = tmp_path / "graph-commit-batch"

    written = render_graph(result, out_dir, {"capabilities": {"resolve_owner": True}})

    assert out_dir.exists()
    assert (out_dir / "INDEX.md").exists()
    assert (out_dir / "edges.jsonl").exists()
    assert all(p.exists() for p in written)

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert (
        "statements sem dependencia de dados por natureza (controle transacional: "
        "COMMIT/ROLLBACK/SAVEPOINT/SET TRANSACTION/LOCK TABLE; cursor estatico: OPEN/"
        "FETCH/CLOSE) = 4"
    ) in index_text

    edges_text = (out_dir / "edges.jsonl").read_text(encoding="utf-8")
    no_dep_payloads = [
        json.loads(line) for line in edges_text.splitlines() if json.loads(line)["edge_type"] == "NO_DATA_DEP"
    ]
    assert len(no_dep_payloads) == 4
    assert {p["op"] for p in no_dep_payloads} == {"COMMIT", "ROLLBACK", "FETCH", "CLOSE"}
    assert all(p["from_ref"] == "GESTAO.BATCH_PKG.RUN_BATCH" for p in no_dep_payloads)

    # O INSERT continua com WRITE normal -- a correcao nao muda o
    # tratamento dos tipos que ja funcionavam.
    write_payloads = [json.loads(line) for line in edges_text.splitlines() if json.loads(line)["edge_type"] == "WRITE"]
    assert len(write_payloads) == 1
    assert write_payloads[0]["to_ref"] == "GESTAO.BATCH_LOG"


def test_coverage_error_message_names_unknown_type_end_to_end():
    # Mesmo cenario do teste acima, mas trocando UM dos statements por um
    # tipo GENUINAMENTE desconhecido -- o sinal alto do Defeito 1 tem que
    # sobreviver: CoverageError, com o nome do tipo na mensagem, ZERO
    # arquivos gravados.
    class _UnknownTypeExtractor(_TransactionalBatchExtractor):
        def __init__(self) -> None:
            super().__init__()
            self._statements = [
                {"usage_id": 3, "usage_context_id": 2, "line": 4, "stmt_type": "INSERT"},
                {"usage_id": 5, "usage_context_id": 2, "line": 5, "stmt_type": "TIPO_QUE_NAO_EXISTE"},
            ]

    extractor = _UnknownTypeExtractor()
    result = build_proc_graph(extractor, ("GESTAO", "BATCH_PKG", "RUN_BATCH"))

    with pytest.raises(CoverageError, match="TIPO_QUE_NAO_EXISTE"):
        build_coverage(result, {"resolve_owner": True})


def test_render_graph_writes_nothing_for_unknown_statement_type(tmp_path):
    class _UnknownTypeExtractor(_TransactionalBatchExtractor):
        def __init__(self) -> None:
            super().__init__()
            self._statements = [
                {"usage_id": 3, "usage_context_id": 2, "line": 4, "stmt_type": "INSERT"},
                {"usage_id": 5, "usage_context_id": 2, "line": 5, "stmt_type": "TIPO_QUE_NAO_EXISTE"},
            ]

    extractor = _UnknownTypeExtractor()
    result = build_proc_graph(extractor, ("GESTAO", "BATCH_PKG", "RUN_BATCH"))
    out_dir = tmp_path / "graph-unknown-type"

    with pytest.raises(CoverageError, match="TIPO_QUE_NAO_EXISTE"):
        render_graph(result, out_dir, {"capabilities": {"resolve_owner": True}})

    assert not out_dir.exists()


# --------------------------------------------------------------------------
# DEFEITO NAO-BLOQUEANTE (mesmo relatorio): TRIGGER_FIRES nao pode ser
# contado como se fosse fallback de grao objeto (sem PL/Scope) -- e
# estrutural (tabela -> trigger, sempre 2 partes), nao um sinal de
# capacidade ausente.
# --------------------------------------------------------------------------


def test_trigger_fires_edges_counted_separately_from_object_fallback(tmp_path):
    # T1 -> TRG1 -> T2 -> TRG2 -> T1 (mesmo ciclo de
    # test_procgraph_access.py::test_trigger_cycle_between_two_tables_terminates_and_is_declared),
    # 100% PL/Scope disponivel em todos os objetos -- ZERO fallback de
    # grao objeto de verdade. Antes da correcao, as arestas TRIGGER_FIRES
    # (from_ref de 2 partes por desenho) eram contadas junto com o
    # fallback e rotuladas "sem PL/Scope disponivel", uma mentira: a
    # saida real ja mostrou "= 1 arestas" atribuido a fallback com
    # fallback_objects: 0.
    extractor = _TriggerCycleExtractor()
    result = build_proc_graph(extractor, ("GESTAO", "P_START", "START"))

    coverage = build_coverage(result, {"resolve_owner": True, "triggers": True})
    assert coverage.edges_trigger_fires == 2  # T1->TRG1, T2->TRG2
    assert coverage.statements_edges_object == 0  # nenhum fallback de verdade

    out_dir = tmp_path / "graph-trigger-cycle"
    render_graph(result, out_dir, {"capabilities": {"resolve_owner": True, "triggers": True}})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "arestas TRIGGER_FIRES" in index_text
    assert "= 2 arestas" in index_text
    # a linha de fallback de verdade tem que mostrar ZERO, nunca a
    # contagem de TRIGGER_FIRES emprestada por engano.
    fallback_line = next(
        line
        for line in index_text.splitlines()
        if line.startswith("arestas de acesso/estado/SQL dinamico em grao objeto")
    )
    assert fallback_line.rstrip().endswith("= 0 arestas")


# --------------------------------------------------------------------------
# DEFEITO 1 (relatorio de verificacao independente, 2026-08-16): OPEN
# ESTATICO vs DINAMICO -- `ALL_STATEMENTS.TYPE` para os dois casos e o
# MESMO literal "OPEN" (fato confirmado contra banco real Oracle 21c,
# GESTAO_OO.PKG_DYNAMIC_EVALUATOR). A desambiguacao correta usa o
# TEXTO-FONTE (ver procgraph_access._open_stmt_is_dynamic); cobertura
# principal dos tres cenarios obrigatorios (fonte com FOR/sem FOR/
# indisponivel) fica em tests/test_procgraph_access.py -- aqui so a
# integracao com o resultado de `expand_access` continua validada, JA
# CORRIGIDA para a regra de desempate (indecidivel -> DINAMICO, nunca
# NO_DATA_DEP).
# --------------------------------------------------------------------------


def test_open_for_dynamic_stays_dynamic_sql_never_no_dependency():
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "RUNNER", "PROCEDURE", "DEFINITION", "SIG_RUNNER"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY", body_statements=[])
    # Rede de seguranca defensiva: literal "OPEN FOR" nunca confirmado
    # contra banco real (o caso normal e "OPEN" puro, coberto abaixo) --
    # `_is_dynamic_stmt_type` ainda o trata como dinamico se aparecer.
    statement = Assignment(usage_id=3, line=7, kind="STMT", target="OPEN FOR", enclosing="RUNNER")

    edges = expand_access(
        owner="GESTAO", object_name="PKG", subprogram="RUNNER", statement=statement, object_cache=cache
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"
    assert edges[0].confidence == "opaque"  # sem extractor.source neste teste


def test_open_pure_without_source_defaults_to_dynamic_never_no_dependency():
    # CORRECAO do DEFEITO 1: `stmt_type` PURO "OPEN" (o caso real -- os
    # dois casos, estatico e dinamico, saem identicos do compilador) SEM
    # capacidade `source` no extractor e INDECIDIVEL -- a regra de
    # desempate manda DINAMICO (opaque), nunca NO_DATA_DEP. ANTES desta
    # correcao este cenario (sem extractor nenhum) caia direto em
    # NO_DATA_DEP -- exatamente a falha que o DEFEITO 1 provou em
    # producao (SQL dinamico virando NO_DATA_DEP e sumindo de PONTOS
    # CEGOS).
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "RUNNER", "PROCEDURE", "DEFINITION", "SIG_RUNNER"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY", body_statements=[])
    statement = Assignment(usage_id=3, line=7, kind="STMT", target="OPEN", enclosing="RUNNER")

    edges = expand_access(
        owner="GESTAO", object_name="PKG", subprogram="RUNNER", statement=statement, object_cache=cache
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"
    assert edges[0].confidence == "opaque"
    assert edges[0].edge_type != "NO_DATA_DEP"


def test_open_pure_static_falls_into_no_dependency_bucket_when_source_proves_it():
    # Contraste direto: MESMO "OPEN" puro, agora com um extractor.source
    # que PROVA (fecha com ";" sem nenhum "FOR") que e um cursor
    # ESTATICO -- so ENTAO cai no balde NO_DATA_DEP.
    from plsqlflow import extract

    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "RUNNER", "PROCEDURE", "DEFINITION", "SIG_RUNNER"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY")
    statement = Assignment(usage_id=3, line=7, kind="STMT", target="OPEN", enclosing="RUNNER")

    class _StaticOpenSourceExtractor:
        def source(self, owner: str, object_name: str):
            return [extract.FetchSourceRow(type="PACKAGE BODY", line=7, text="OPEN c;\n")]

    edges = expand_access(
        owner="GESTAO",
        object_name="PKG",
        subprogram="RUNNER",
        statement=statement,
        object_cache=cache,
        extractor=_StaticOpenSourceExtractor(),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "NO_DATA_DEP"
    assert edges[0].op == "OPEN"


# --------------------------------------------------------------------------
# DEFEITO 2 (relatorio de verificacao independente, 2026-08-16): a
# estatistica "blind_spots" no INDEX.md tinha que refletir EXATAMENTE o
# que a secao PONTOS CEGOS lista -- achado ao vivo: INDEX.md dizia
# "blind_spots: 28" nas Estatisticas mas listava so 20 entradas em PONTOS
# CEGOS (dedup acontecia so em um lugar). GESTAO.FLOW_DEMO.CALC_OVERLOAD#2
# chama LENGTH (builtin, ver DEFEITO 3 abaixo) alem de RUN_DYNAMIC ter dois
# EXECUTE IMMEDIATE opacos -- fixture real que exercita os dois defeitos
# ao mesmo tempo.
# --------------------------------------------------------------------------


def test_stats_blind_spots_count_matches_pontos_cegos_section_exactly(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")

    stats_line = next(line for line in index_text.splitlines() if line.startswith("- blind_spots:"))
    stats_count = int(stats_line.split(":")[1].strip())

    pontos_cegos_section = index_text.split("## PONTOS CEGOS", 1)[1].split(
        "## Builtins SQL/PLSQL", 1
    )[0]
    listed_entries = [
        line
        for line in pontos_cegos_section.splitlines()
        if line.startswith("- ") and "nao expandido:" not in line and line.strip() != "- nenhum"
    ]

    assert stats_count == len(listed_entries)
    assert stats_count == len(set(result.blind_spots))


# --------------------------------------------------------------------------
# DEFEITO 3 (relatorio de verificacao independente, 2026-08-16): builtin
# de SYS.STANDARD (LENGTH, no caso da fixture real) some da secao PONTOS
# CEGOS -- onde afogava o SQL dinamico de verdade -- e ganha secao
# propria, ainda contada e visivel.
# --------------------------------------------------------------------------


def test_builtin_call_excluded_from_pontos_cegos_and_listed_in_own_section(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")

    pontos_cegos_section = index_text.split("## PONTOS CEGOS", 1)[1].split(
        "## Builtins SQL/PLSQL", 1
    )[0]
    builtins_section = index_text.split("## Builtins SQL/PLSQL", 1)[1]

    # LENGTH nunca aparece em PONTOS CEGOS (nao afoga mais o SQL dinamico).
    assert "LENGTH" not in pontos_cegos_section
    # ... mas continua contada e visivel na secao propria.
    assert "LENGTH" in builtins_section
    assert "chamadas a builtin SQL/PLSQL nao expandidas: 1" in builtins_section

    # O no UNKNOWN.UNKNOWN.LENGTH fica identificavel como builtin na
    # lista de nos.
    nos_section = index_text.split("## Nos", 1)[1].split("## COBERTURA", 1)[0]
    length_line = next(line for line in nos_section.splitlines() if "LENGTH" in line)
    assert "(builtin)" in length_line


def test_builtin_calls_field_matches_stats_and_never_empty_for_length_call():
    result = _flow_demo_result()

    assert result.stats["builtin_calls"] == len(set(result.builtin_calls))
    assert result.stats["builtin_calls"] >= 1
    assert any("LENGTH" in spot for spot in result.builtin_calls)
    assert not any("LENGTH" in spot for spot in result.blind_spots)

    length_node = next(
        n for n in result.nodes if n.grain == "unresolved" and n.subprogram == "LENGTH"
    )
    assert length_node.is_builtin is True


def test_builtin_classification_requires_resolve_owner_lookup():
    # Achado nao-bloqueante da 3a verificacao independente: a classificacao
    # de builtin e por NOME, e `owner_hint is None` pode significar
    # "capacidade resolve_owner AUSENTE" em vez de "signature buscada e nao
    # encontrada em nenhum owner de usuario". Sem a busca, um objeto do
    # USUARIO chamado NVL/ROUND/LENGTH num owner nao coberto seria engolido
    # pela lista de builtins -- sumindo da secao PONTOS CEGOS, que e a que
    # exige olhar humano. O guard: sem `resolve_owner`, nome de builtin cai
    # no ramo GENERICO de ponto cego (visivel), nunca em builtin_calls.
    fixture = _load_fixture()
    extractor = FakeExtractor(fixture)
    # remove a capacidade: a classificacao nao pode mais "provar" builtin
    if hasattr(type(extractor), "resolve_owner"):
        extractor_cls = type(
            "FakeExtractorSemResolveOwner",
            (FakeExtractor,),
            {"resolve_owner": property(doc="removida")},
        )
        # property sem getter levanta AttributeError no acesso -- hasattr
        # devolve False, que e exatamente o contrato de "capacidade ausente"
        extractor = extractor_cls(fixture)
    assert not hasattr(extractor, "resolve_owner")

    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"))

    # LENGTH continua no grafo (regra anti-omissao) -- mas como ponto cego
    # generico, nao como builtin.
    assert any("LENGTH" in spot for spot in result.blind_spots)
    assert not any("LENGTH" in spot for spot in result.builtin_calls)
    length_node = next(
        n for n in result.nodes if n.grain == "unresolved" and n.subprogram == "LENGTH"
    )
    assert length_node.is_builtin is False


# --------------------------------------------------------------------------
# DEFEITO 4 (relatorio de verificacao independente, 2026-08-16): motivo
# ausente no rebaixamento (objeto alcancado transitivamente pelo fallback
# nunca recebia `note`), e TABELA/fronteira aparecendo sob "Rebaixados
# para grao objeto" -- quando nunca foram candidatos a grao subprograma.
# --------------------------------------------------------------------------


def _demote_reason_fixture():
    """CALLER (fino) -> ROOT_FB (sem PL/Scope, gatilho do fallback) ->
    via ALL_DEPENDENCIES: SIBLING_PKG (PL/SQL completo, alcancado so
    TRANSITIVAMENTE -- nunca foi gatilho direto), TB_PROJETOS (TABLE,
    nunca foi candidato a grao subprograma) e dois objetos de FRONTEIRA
    (SYS.STANDARD por stop_schemas, PUBLIC.DBMS_LOB por prefixo DBMS_)."""

    class _ProcExtractor:
        def plscope_identifiers(self, owner, object_name):
            ref = "{}.{}".format(owner.upper(), object_name.upper())
            if ref == "GESTAO.CALLER":
                return [
                    SimpleNamespace(
                        usage_id=1, usage_context_id=0, line=1, col=1, name="CALLER",
                        type="PACKAGE", usage="DEFINITION", signature=None, object_type="PACKAGE BODY",
                    ),
                    SimpleNamespace(
                        usage_id=2, usage_context_id=1, line=3, col=1, name="P1",
                        type="PROCEDURE", usage="DEFINITION", signature="SIG_CALLER_P1", object_type="PACKAGE BODY",
                    ),
                    SimpleNamespace(
                        usage_id=3, usage_context_id=2, line=4, col=3, name="P1",
                        type="PROCEDURE", usage="CALL", signature="SIG_ROOT_FB_P1", object_type="PACKAGE BODY",
                    ),
                ]
            return []

        def plscope_statements(self, owner, object_name):
            return []

        def resolve_owner(self, signature):
            if signature == "SIG_ROOT_FB_P1":
                return ("GESTAO", "ROOT_FB")
            return None

        def object_wrapped(self, owner, object_name):
            return False

    class _DepExtractor:
        CATALOG = {
            "GESTAO": [
                ("ROOT_FB", "PACKAGE BODY", "VALID"),
                ("SIBLING_PKG", "PACKAGE BODY", "VALID"),
                ("TB_PROJETOS", "TABLE", "VALID"),
            ],
        }
        DEP_PLSCOPE_FULL = {"SIBLING_PKG"}
        DEPS_DIRECT = {
            ("GESTAO", "ROOT_FB"): [
                ("GESTAO", "SIBLING_PKG", "PACKAGE"),
                ("GESTAO", "TB_PROJETOS", "TABLE"),
                ("SYS", "STANDARD", "PACKAGE"),
                ("PUBLIC", "DBMS_LOB", "PACKAGE"),
            ],
        }

        def deps_direct(self, owner, name):
            key = (owner.upper(), name.upper())
            return [
                SimpleNamespace(referenced_owner=o, referenced_name=n, referenced_type=t, dependency_type="HARD")
                for (o, n, t) in self.DEPS_DIRECT.get(key, [])
            ]

        def object_catalog(self, owner, object_list=None):
            return [
                SimpleNamespace(owner=owner.upper(), object_name=name, object_type=type_, status=status, last_ddl_time="2026-08-15 10:00:00")
                for (name, type_, status) in self.CATALOG.get(owner.upper(), [])
            ]

        def plscope_check(self, owner):
            rows = []
            for (name, type_, _status) in self.CATALOG.get(owner.upper(), []):
                settings = "IDENTIFIERS:ALL,STATEMENTS:ALL" if name in self.DEP_PLSCOPE_FULL else None
                rows.append(SimpleNamespace(owner=owner.upper(), name=name, type=type_, plscope_settings=settings))
            return rows

        def synonym(self, owner, name):
            return []

    return build_proc_graph(_ProcExtractor(), ("GESTAO", "CALLER", "P1"), dep_extractor=_DepExtractor())


def test_transitively_reached_plsql_object_gets_own_reason_never_generic():
    result = _demote_reason_fixture()

    sibling = next(n for n in result.nodes if n.object_name == "SIBLING_PKG")
    assert sibling.grain == "object"
    assert sibling.note is not None
    assert sibling.note != "sem motivo registrado"
    # PL/Scope completo neste objeto -- alcancado so pela cadeia de
    # fallback, nao por deficiencia propria (ver _object_grain_reason).
    assert "PL/Scope completo" in sibling.note

    coverage = build_coverage(result, FULL_CAPABILITIES)
    reasons_text = " ".join(coverage.reasons)
    assert "GESTAO.SIBLING_PKG" in reasons_text
    assert "sem motivo registrado" not in reasons_text


def test_table_and_frontier_never_appear_under_rebaixados(tmp_path):
    result = _demote_reason_fixture()

    table_node = next(n for n in result.nodes if n.object_name == "TB_PROJETOS")
    assert table_node.grain == "object"
    assert table_node.object_type == "TABLE"
    assert table_node.is_frontier is False

    standard_node = next(n for n in result.nodes if n.object_name == "STANDARD")
    dbms_lob_node = next(n for n in result.nodes if n.object_name == "DBMS_LOB")
    assert standard_node.is_frontier is True
    assert dbms_lob_node.is_frontier is True

    coverage = build_coverage(result, FULL_CAPABILITIES)
    reasons_text = " ".join(coverage.reasons)
    assert "TB_PROJETOS" not in reasons_text
    assert "STANDARD" not in reasons_text
    assert "DBMS_LOB" not in reasons_text

    no_plsql_text = " ".join(coverage.no_plsql_objects)
    assert "GESTAO.TB_PROJETOS: TABLE" in no_plsql_text

    frontier_text = " ".join(coverage.frontier_objects)
    assert "SYS.STANDARD" in frontier_text
    assert "PUBLIC.DBMS_LOB" in frontier_text

    out_dir = tmp_path / "graph-demote-reason"
    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})
    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")

    rebaixados_section = index_text.split("### Rebaixados para grao objeto", 1)[1].split("### ", 1)[0]
    assert "TB_PROJETOS" not in rebaixados_section
    assert "STANDARD" not in rebaixados_section
    assert "DBMS_LOB" not in rebaixados_section
    assert "SIBLING_PKG" in rebaixados_section

    assert "### Sem PL/SQL" in index_text
    assert "### Fronteira de schema" in index_text


# --------------------------------------------------------------------------
# capacidades opcionais ausentes -- degradacao declarada (item 3 do T-08)
# --------------------------------------------------------------------------


def test_index_declares_missing_resolve_owner_explicitly(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(
        result,
        out_dir,
        {"capabilities": {"resolve_owner": False, "object_wrapped": True, "triggers": True}},
    )

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "resolve_owner" in index_text
    assert "AUSENTE" in index_text
    assert "podem NAO ter sido seguidas" in index_text


def test_index_declares_missing_source_and_dynamic_sql_stays_opaque_blind_spot(tmp_path):
    # Defeito 1 + item 3 do T-08 juntos: sem a capacidade `source`, o SQL
    # dinamico de RUN_DYNAMIC (FakeExtractor de test_procgraph_bfs.py NAO
    # implementa `source`) nunca desaparece -- vira `opaque` declarado, e
    # a AUSENCIA da capacidade aparece "em alto e bom som" na COBERTURA
    # (nao so em log).
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(
        result,
        out_dir,
        {"capabilities": {"resolve_owner": True, "object_wrapped": True, "triggers": True, "source": False}},
    )

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "source (texto-fonte para resolver/classificar SQL dinamico): AUSENTE" in index_text
    assert "cai em 'opaque' declarada" in index_text

    edges_text = (out_dir / "edges.jsonl").read_text(encoding="utf-8")
    dyn_payloads = [json.loads(line) for line in edges_text.splitlines() if json.loads(line)["edge_type"] == "DYNAMIC_SQL"]
    assert len(dyn_payloads) == 2
    assert all(p["confidence"] == "opaque" for p in dyn_payloads)
    assert all(p["from_ref"] == "GESTAO.FLOW_DEMO.RUN_DYNAMIC" for p in dyn_payloads)

    # Ponto cego declarado, nunca omitido.
    assert "GESTAO.FLOW_DEMO.RUN_DYNAMIC" in index_text
    assert "[opaque]" in index_text


def test_index_declares_capabilities_not_informed(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {})  # sem "capabilities" nenhuma

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "NAO INFORMADO" in index_text


def test_index_declares_all_capabilities_available(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "resolve_owner (resolucao de CALL inter-package por signature): disponivel" in index_text
    assert "object_wrapped (deteccao de objeto PL/SQL wrapped): disponivel" in index_text
    assert "triggers (descoberta de trigger de tabela escrita): disponivel" in index_text


def test_capabilities_from_extractor_reflects_hasattr():
    assert capabilities_from_extractor(FakeExtractor(_load_fixture())) == {
        "resolve_owner": True,
        "object_wrapped": False,
        "triggers": False,
        "source": False,
    }
    assert capabilities_from_extractor(FakeProcExtractor()) == {
        "resolve_owner": True,
        "object_wrapped": True,
        "triggers": False,
        "source": False,
    }


# --------------------------------------------------------------------------
# ## Ciclos
# --------------------------------------------------------------------------


def test_cycles_section_renders_same_object_mutual_recursion(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "## Ciclos" in index_text
    assert "recursao mutua (mesmo objeto)" in index_text
    assert "GESTAO.FLOW_DEMO.PROC_A" in index_text
    assert "GESTAO.FLOW_DEMO.PROC_B" in index_text


def test_cycles_section_renders_cross_object_inter_package(tmp_path):
    result = _inter_package_cycle_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "acoplamento inter-package" in index_text
    assert "GESTAO.PKG_M1.P1" in index_text
    assert "GESTAO.PKG_M2.P2" in index_text


def test_cycles_section_says_nenhum_when_acyclic(tmp_path):
    result = _fallback_result()  # A -> B -> C, sem ciclo nenhum
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    index_text = (out_dir / "INDEX.md").read_text(encoding="utf-8")
    cycles_section = index_text.split("## Ciclos", 1)[1].split("## PONTOS CEGOS", 1)[0]
    assert "- nenhum" in cycles_section


# --------------------------------------------------------------------------
# arvore em disco
# --------------------------------------------------------------------------


def test_render_writes_expected_tree_structure(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    written = render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES, "root_ref": "GESTAO.FLOW_DEMO.MAIN"})

    assert (out_dir / "INDEX.md").exists()
    assert (out_dir / "edges.jsonl").exists()
    assert (out_dir / "meta.json").exists()
    assert (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").exists()
    assert all(p.exists() for p in written)
    # recompile.sql NUNCA gerado neste modo (ProcNode nao carrega
    # object_type -- ver docstring do modulo) mesmo quando needs_recompile
    # existiria em outro caso; aqui nao ha needs_recompile de qualquer forma.
    assert not (out_dir / "recompile.sql").exists()


def test_node_md_content_has_ref_header_and_outbound_calls(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    main_md = (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").read_text(encoding="utf-8")
    assert main_md.startswith("# GESTAO.FLOW_DEMO.MAIN\n")
    assert "## Chama (outbound)" in main_md
    assert "GESTAO.FLOW_DEMO.PROC_A" in main_md
    # assercao negativa (mesmo criterio de tests/test_procgraph_bfs.py):
    # MAIN nunca chama LENGTH nem escreve em FLOW_DEMO_LOG.
    assert "LENGTH" not in main_md
    assert "FLOW_DEMO_LOG" not in main_md


def test_node_md_shows_dynamic_sql_including_the_exact_level(tmp_path):
    """Regressao de omissao encontrada na execucao AO VIVO contra o dev.

    RUN_DYNAMIC executa dois EXECUTE IMMEDIATE (L28 e L31 do FLOW_DEMO
    real). A aresta DYNAMIC_SQL de nivel `exact` cai num vao: nao e ponto
    cego (esta resolvida), entao fica fora da secao PONTOS CEGOS do INDEX;
    e nao e READ/WRITE, entao fica fora de `## Tabelas acessadas`. Sem a
    secao `## SQL Dinamico` no node .md ela existia SO em edges.jsonl, e o
    arquivo do subprograma mostrava um no aparentemente sem efeito nenhum
    -- mentira por omissao, no formato que uma pessoa de fato le para
    planejar a migracao.

    O nivel (exact/partial/opaque) precisa estar visivel na linha: e a
    diferenca entre alvo provado e alvo que alguem tem que conferir a mao.
    """
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    node_md = (out_dir / "nodes" / "GESTAO.FLOW_DEMO.RUN_DYNAMIC.md").read_text(encoding="utf-8")

    assert "## SQL Dinâmico" in node_md
    # as DUAS ocorrencias, nao so a que vira ponto cego
    assert "L28" in node_md
    assert "L31" in node_md

    # Nivel explicito em toda ocorrencia. Aqui as duas saem `opaque` porque o
    # FakeExtractor da fixture NAO oferece a capacidade `source` -- sem o
    # texto-fonte nao da para resolver o literal nem casar candidato no
    # catalogo. Isso e a garantia anti-omissao no seu caso mais duro: mesmo
    # sem conseguir dizer PARA ONDE o SQL dinamico aponta, a ocorrencia
    # continua no mapa, marcada, em vez de sumir. Com a capacidade presente
    # os niveis sobem (a execucao real contra o dev da L28 [exact] e L31
    # [partial]); a classificacao em si e coberta pelos testes dedicados de
    # tests/test_procgraph_access.py.
    for line in ("L28", "L31"):
        assert any(
            line in row and "[" in row and "]" in row
            for row in node_md.splitlines()
        ), "ocorrencia {} sem nivel de classificacao explicito".format(line)

    # e continuam pertencendo a RUN_DYNAMIC, nunca a MAIN
    main_md = (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").read_text(encoding="utf-8")
    assert "## SQL Dinâmico" not in main_md


def test_edges_jsonl_has_one_line_per_edge_sorted(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    lines = (out_dir / "edges.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(result.edges)
    payloads = [json.loads(line) for line in lines]
    assert payloads == sorted(
        payloads, key=lambda p: (p["from_ref"], p["to_ref"], p["edge_type"], p["line"] if p["line"] is not None else -1)
    )


def test_meta_json_has_chain_hash_and_no_timestamp(tmp_path):
    result = _flow_demo_result()
    out_dir = tmp_path / "graph"

    render_graph(result, out_dir, {"capabilities": FULL_CAPABILITIES})

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert "chain_hash" in meta
    assert len(meta["chain_hash"]) == 64  # SHA-256 hex
    assert "params" in meta
    assert "timestamp" not in meta
    assert "time" not in meta


def test_stale_node_files_removed_between_generations(tmp_path):
    out_dir = tmp_path / "graph"
    render_graph(_flow_demo_result(), out_dir, {"capabilities": FULL_CAPABILITIES})
    assert (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").exists()

    # segunda geracao com um resultado MENOR -- o no antigo tem que sumir,
    # nao ficar orfao (mesma disciplina de depgraph_render.render_graph).
    render_graph(_empty_result(), out_dir, {"capabilities": FULL_CAPABILITIES})

    assert not (out_dir / "nodes" / "GESTAO.FLOW_DEMO.MAIN.md").exists()
    assert list((out_dir / "nodes").glob("*.md")) == []


# --------------------------------------------------------------------------
# determinismo
# --------------------------------------------------------------------------


def test_render_is_byte_for_byte_deterministic_across_two_runs(tmp_path):
    result1 = _flow_demo_result()
    result2 = _flow_demo_result()

    out_dir_1 = tmp_path / "run1"
    out_dir_2 = tmp_path / "run2"
    meta_params = {"capabilities": FULL_CAPABILITIES, "root_ref": "GESTAO.FLOW_DEMO.MAIN"}

    render_graph(result1, out_dir_1, meta_params)
    render_graph(result2, out_dir_2, meta_params)

    index_1 = (out_dir_1 / "INDEX.md").read_bytes()
    index_2 = (out_dir_2 / "INDEX.md").read_bytes()
    assert index_1 == index_2

    edges_1 = (out_dir_1 / "edges.jsonl").read_bytes()
    edges_2 = (out_dir_2 / "edges.jsonl").read_bytes()
    assert edges_1 == edges_2

    meta_1 = json.loads((out_dir_1 / "meta.json").read_text(encoding="utf-8"))
    meta_2 = json.loads((out_dir_2 / "meta.json").read_text(encoding="utf-8"))
    assert meta_1["chain_hash"] == meta_2["chain_hash"]


def test_no_file_uses_crlf_line_endings(tmp_path):
    # Byte-exatidao em Windows (mesma preocupacao literal de
    # depgraph_render.py): nenhuma escrita pode deixar o modo texto padrao
    # trocar "\n" por "\r\n".
    out_dir = tmp_path / "graph"
    render_graph(_flow_demo_result(), out_dir, {"capabilities": FULL_CAPABILITIES})

    for path in [out_dir / "INDEX.md", out_dir / "edges.jsonl", out_dir / "meta.json"]:
        raw = path.read_bytes()
        assert b"\r\n" not in raw


# --------------------------------------------------------------------------
# node_ref / node_filename
# --------------------------------------------------------------------------


def test_node_ref_three_part_for_subprogram():
    node = ProcNode(owner="GESTAO", object_name="FLOW_DEMO", subprogram="MAIN", grain="subprogram")
    assert node_ref(node) == "GESTAO.FLOW_DEMO.MAIN"


def test_node_ref_two_part_for_object_grain_fallback():
    node = ProcNode(owner="GESTAO", object_name="B", subprogram="", grain="object")
    assert node_ref(node) == "GESTAO.B"


def test_node_filename_sanitizes_overload_suffix():
    assert node_filename("GESTAO", "FLOW_DEMO", "CALC_OVERLOAD#2") == "GESTAO.FLOW_DEMO.CALC_OVERLOAD_2.md"


def test_node_filename_two_part_for_fallback_object():
    assert node_filename("GESTAO", "B", "") == "GESTAO.B.md"
