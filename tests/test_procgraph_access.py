"""Testes offline (T-05, contrato depgraph-granular) para
plsqlflow/procgraph_access.py -- o seam de acesso que
plsqlflow/procgraph.py ja chama (`expand_access`).

Nenhum caso toca rede/banco. O golden de READ/WRITE reusa a arvore REAL de
GESTAO.FLOW_DEMO (tests/fixtures/plscope_tree.json, T-01) -- inclusive o
texto literal do INSERT de LOG_MSG e do INSERT do trigger
TRG_FLOW_DEMO_LOG, ambos colados diretamente do fonte lido no banco dev em
2026-08-15 (`all_source`), para a comparacao contra o regex antigo ser
honesta. Estado de package e a descoberta de trigger usam fixtures
sinteticas pequenas construidas inline (mesmo padrao de
tests/test_procgraph_bfs.py para os casos que a arvore real nao cobre).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from plsqlflow import depgraph_enrich, extract
from plsqlflow.attribute import Assignment, IdentifierRow, StatementRow, assign_context
from plsqlflow.procgraph import ProcEdge, build_proc_graph
from plsqlflow.procgraph_access import expand_access
from tests.test_procgraph_bfs import FakeExtractor, _load_fixture

REPO = Path(__file__).resolve().parent.parent
FLOW_DEMO_FIXTURE = REPO / "tests" / "fixtures" / "plscope_tree.json"
FLOW_DEMO_SOURCE_FIXTURE = REPO / "tests" / "fixtures" / "flow_demo_extract.json"


def _load_flow_demo() -> Dict[str, Any]:
    return json.loads(FLOW_DEMO_FIXTURE.read_text(encoding="utf-8"))


def _identifiers_from(rows: List[Dict[str, Any]]) -> List[IdentifierRow]:
    return [
        IdentifierRow(
            usage_id=row["usage_id"],
            usage_context_id=row["usage_context_id"],
            line=row["line"],
            col=row["col"],
            name=row["name"],
            type=row["type"],
            usage=row["usage"],
            signature=row.get("signature"),
        )
        for row in rows
    ]


def _flow_demo_cache() -> SimpleNamespace:
    """Duck-typed `_ObjectCache` minimo -- so os dois atributos que
    `procgraph_access` le (`body_identifiers`/`body_type`). Deliberadamente
    NAO importa a dataclass privada de procgraph.py (mesmo desacoplamento
    que o proprio modulo sob teste pratica)."""
    fixture = _load_flow_demo()
    return SimpleNamespace(
        body_identifiers=_identifiers_from(fixture["identifiers"]),
        body_type=fixture["object_type"],
    )


def _assignment_for(fixture: Dict[str, Any], usage_id: int) -> Assignment:
    identifiers = _identifiers_from(fixture["identifiers"])
    statements = [
        # StatementRow shape esperado por assign_context -- reconstrucao
        # minima, mesmo padrao de tests/test_attribute.py.
        SimpleNamespace(
            usage_id=row["usage_id"],
            usage_context_id=row["usage_context_id"],
            line=row["line"],
            stmt_type=row["stmt_type"],
        )
        for row in fixture["statements"]
    ]
    from plsqlflow.attribute import StatementRow

    stmt_rows = [
        StatementRow(usage_id=s.usage_id, usage_context_id=s.usage_context_id, line=s.line, stmt_type=s.stmt_type)
        for s in statements
    ]
    assignments = assign_context(identifiers, stmt_rows, fixture["object_type"])
    return next(a for a in assignments if a.usage_id == usage_id)


# --------------------------------------------------------------- entrega 1


def test_log_msg_write_has_compiler_column_msg():
    # PROVA do contrato: LOG_MSG escreve em FLOW_DEMO_LOG com a coluna MSG
    # vinda do compilador (usage_id=6 = o INSERT, linha 6, secao 2 do
    # plano).
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)
    assert insert_assignment.kind == "STMT"
    assert insert_assignment.enclosing == "LOG_MSG"

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
    )

    write_edges = [e for e in edges if e.edge_type == "WRITE"]
    assert len(write_edges) == 1
    edge = write_edges[0]
    assert isinstance(edge, ProcEdge)
    assert edge.from_ref == "GESTAO.FLOW_DEMO.LOG_MSG"
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.op == "INSERT"
    assert edge.cols == ["MSG"]
    assert edge.line == 6


def test_main_never_gets_a_write_edge_to_flow_demo_log():
    # Segunda metade da prova: MAIN NAO pode ter essa aresta. MAIN so tem
    # CALLs no golden (secao 2 do plano) -- nenhum Assignment kind=STMT
    # atribuido a MAIN, entao o seam nunca e chamado com statement para
    # MAIN, e a unica outra forma de chamada (statement=None, entrega 2)
    # nunca produz READ/WRITE. As duas verificacoes ficam explicitas
    # abaixo.
    fixture = _load_flow_demo()
    identifiers = _identifiers_from(fixture["identifiers"])
    from plsqlflow.attribute import StatementRow

    stmt_rows = [
        StatementRow(usage_id=s["usage_id"], usage_context_id=s["usage_context_id"], line=s["line"], stmt_type=s["stmt_type"])
        for s in fixture["statements"]
    ]
    assignments = assign_context(identifiers, stmt_rows, fixture["object_type"])
    main_stmt_assignments = [a for a in assignments if a.enclosing == "MAIN" and a.kind == "STMT"]
    assert main_stmt_assignments == []

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="MAIN",
        statement=None,
        object_cache=_flow_demo_cache(),
    )
    assert not any(e.edge_type in ("READ", "WRITE") for e in edges)


def test_compiler_columns_at_least_as_complete_as_regex():
    # Comparacao explicita contra o regex antigo (depgraph_enrich, NAO
    # tocado). Caso 1: com o texto real do INSERT (colado do fonte lido em
    # GESTAO.FLOW_DEMO no banco dev), regex e compilador batem -- mesmo
    # conjunto de colunas (case-insensitive: o fonte escreve "msg" minusculo,
    # o compilador reporta "MSG" -- ALL_IDENTIFIERS normaliza identificador
    # nao-quotado para maiusculo). Caso 2: SEM o texto (regex.
    # _extract_write_cols exige `text`; o compilador nao precisa de texto
    # nenhum, so da arvore PL/Scope) o regex nao acha NADA enquanto o
    # compilador continua achando a coluna -- a demonstracao mais direta de
    # "nunca menos" que este contrato pede.
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)
    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
    )
    compiler_cols = {c.upper() for c in (next(e for e in edges if e.edge_type == "WRITE").cols or [])}

    real_source_text = "INSERT INTO flow_demo_log (msg) VALUES (p_msg);"
    regex_cols_with_text = {c.upper() for c in (depgraph_enrich._extract_write_cols(real_source_text, "INSERT") or [])}
    assert regex_cols_with_text <= compiler_cols
    assert regex_cols_with_text == {"MSG"}
    assert compiler_cols == {"MSG"}

    regex_cols_without_text = depgraph_enrich._extract_write_cols(None, "INSERT")
    assert regex_cols_without_text is None  # regex fica sem NADA sem o texto-fonte
    assert compiler_cols == {"MSG"}  # compilador nao depende de texto nenhum


def test_trigger_body_write_has_compiler_columns_in_source_order():
    # Segunda evidencia real (GESTAO.TRG_FLOW_DEMO_LOG, fonte lido do banco
    # dev): "INSERT INTO flow_demo_audit (log_id, acao) VALUES (...)" --
    # prova que a ordem das colunas vem de (line, col), nao de usage_id
    # (LOG_ID tem usage_id MAIOR que ACAO na arvore real, mas aparece
    # PRIMEIRO no texto e tem que aparecer primeiro na lista).
    identifiers = [
        IdentifierRow(1, 0, 1, 9, "TRG_FLOW_DEMO_LOG", "TRIGGER", "DECLARATION", "CCCF5F75225193E6C290768FFF7B5C96"),
        IdentifierRow(2, 1, 1, 9, "TRG_FLOW_DEMO_LOG", "TRIGGER", "DEFINITION", "CCCF5F75225193E6C290768FFF7B5C96"),
        IdentifierRow(4, 3, 5, 15, "FLOW_DEMO_AUDIT", "TABLE", "REFERENCE"),
        IdentifierRow(5, 3, 5, 40, "ACAO", "COLUMN", "REFERENCE"),
        IdentifierRow(6, 3, 5, 32, "LOG_ID", "COLUMN", "REFERENCE"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="TRIGGER")
    statement = Assignment(usage_id=3, line=5, kind="STMT", target="INSERT", enclosing="TRG_FLOW_DEMO_LOG")

    edges = expand_access(
        owner="GESTAO",
        object_name="TRG_FLOW_DEMO_LOG",
        subprogram="TRG_FLOW_DEMO_LOG",
        statement=statement,
        object_cache=cache,
    )
    write_edges = [e for e in edges if e.edge_type == "WRITE"]
    assert len(write_edges) == 1
    assert write_edges[0].to_ref == "GESTAO.FLOW_DEMO_AUDIT"
    assert write_edges[0].cols == ["LOG_ID", "ACAO"]


def test_read_edge_for_select_statement():
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "READER", "PROCEDURE", "DEFINITION", "SIG_READER"),
        IdentifierRow(4, 3, 5, 10, "SOME_TABLE", "TABLE", "REFERENCE"),
        IdentifierRow(5, 3, 5, 20, "SOME_COL", "COLUMN", "REFERENCE"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY")
    statement = Assignment(usage_id=3, line=5, kind="STMT", target="SELECT", enclosing="READER")

    edges = expand_access(
        owner="GESTAO", object_name="PKG", subprogram="READER", statement=statement, object_cache=cache
    )
    read_edges = [e for e in edges if e.edge_type == "READ"]
    assert len(read_edges) == 1
    assert read_edges[0].to_ref == "GESTAO.SOME_TABLE"
    assert read_edges[0].op is None


def test_unknown_target_marker_when_no_table_found():
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "P", "PROCEDURE", "DEFINITION", "SIG_P"),
    ]
    cache = SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY")
    statement = Assignment(usage_id=3, line=5, kind="STMT", target="SELECT", enclosing="P")

    edges = expand_access(owner="GESTAO", object_name="PKG", subprogram="P", statement=statement, object_cache=cache)
    assert len(edges) == 1
    assert edges[0].to_ref == depgraph_enrich.UNKNOWN_TARGET


# --------------------------------------------------------------- entrega 5
#
# DEFEITO BLOQUEANTE (relatorio de verificacao independente sobre este
# contrato, 2026-08-16): antes desta correcao, `_table_access_edges`
# devolvia `[]` para QUALQUER stmt_type fora de SELECT/INSERT/UPDATE/
# DELETE/MERGE/dinamico -- o proprio comentario do codigo citava COMMIT/
# ROLLBACK/LOCK TABLE/FETCH/CLOSE como exemplo. Como a reconciliacao de
# COBERTURA (procgraph_render.py) exige aresta para todo statement lido,
# um UNICO COMMIT dentro de QUALQUER subprograma derrubava render_graph
# inteiro (CoverageError, zero arquivos). Os testes abaixo cobrem o seam
# em isolamento: cada tipo catalogado como "sem dependencia por natureza"
# ganha aresta NO_DATA_DEP (nunca `[]`); tipo desconhecido continua `[]`.


def _no_dep_cache() -> SimpleNamespace:
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "P", "PROCEDURE", "DEFINITION", "SIG_P"),
    ]
    return SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY")


@pytest.mark.parametrize(
    "stmt_type",
    [
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "SET TRANSACTION",
        "LOCK TABLE",
        # "OPEN" DELIBERADAMENTE fora desta lista (correcao do DEFEITO 1):
        # ao contrario dos demais tipos aqui, "OPEN" puro NAO tem
        # classificacao incondicional -- exige desambiguacao por
        # texto-fonte (ver `_open_stmt_is_dynamic` e os testes na secao
        # "entrega 5-B" abaixo, incluindo o caso SEM extractor, que agora
        # cai em DYNAMIC_SQL opaque, nunca NO_DATA_DEP -- regra de
        # desempate).
        "FETCH",
        "CLOSE",
    ],
)
def test_no_dependency_statement_types_get_marker_edge_never_empty(stmt_type):
    statement = Assignment(usage_id=3, line=9, kind="STMT", target=stmt_type, enclosing="P")

    edges = expand_access(
        owner="GESTAO", object_name="PKG", subprogram="P", statement=statement, object_cache=_no_dep_cache()
    )

    assert len(edges) == 1
    edge = edges[0]
    assert edge.edge_type == "NO_DATA_DEP"
    assert edge.from_ref == "GESTAO.PKG.P"
    assert edge.op == stmt_type
    assert edge.line == 9
    # nunca aponta para um objeto/tabela real -- nao ha nenhum, por
    # natureza do statement.
    assert edge.to_ref != depgraph_enrich.UNKNOWN_TARGET


def test_genuinely_unknown_statement_type_still_returns_empty():
    # O sinal alto do Defeito 1 sobrevive INTATO para este caso -- so o
    # caso NORMAL (controle transacional/cursor) deixa de derrubar tudo.
    statement = Assignment(usage_id=3, line=9, kind="STMT", target="TIPO_QUE_NAO_EXISTE", enclosing="P")

    edges = expand_access(
        owner="GESTAO", object_name="PKG", subprogram="P", statement=statement, object_cache=_no_dep_cache()
    )

    assert edges == []


def test_open_for_dynamic_never_falls_into_no_dependency_bucket():
    # Rede de seguranca defensiva: SE algum dia `stmt_type` chegar com um
    # literal "OPEN FOR" (nunca observado contra banco real -- ver DEFEITO
    # 1 abaixo, o caso NORMAL e `stmt_type == "OPEN"` puro, desambiguado
    # por texto-fonte), `_is_dynamic_stmt_type` ainda o trata como
    # dinamico -- nunca cai no balde novo (OPEN estatico).
    statement = Assignment(usage_id=3, line=9, kind="STMT", target="OPEN FOR", enclosing="P")
    cache = SimpleNamespace(body_identifiers=_no_dep_cache().body_identifiers, body_type="PACKAGE BODY", body_statements=[])

    edges = expand_access(
        owner="GESTAO", object_name="PKG", subprogram="P", statement=statement, object_cache=cache
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"
    assert edges[0].edge_type != "NO_DATA_DEP"


# --------------------------------------------- entrega 5-B (DEFEITO 1, OPEN puro)
#
# FATO confirmado ao vivo contra `GESTAO_OO.PKG_DYNAMIC_EVALUATOR` (Oracle
# 21c real): `ALL_STATEMENTS.TYPE` para `OPEN v_cursor FOR v_sql;` (SQL
# dinamico, `v_sql` montado por concatenacao com parametro de entrada) e
# `'OPEN'` PURO -- identico ao tipo de um `OPEN c;` ESTATICO. A premissa
# anterior (`stmt_type` viria como "OPEN FOR") nunca foi confirmada contra
# banco real -- estava ERRADA. A desambiguacao correta usa o TEXTO-FONTE:
# presenca de "FOR" entre "OPEN" e o ";" que fecha o statement.
#
# Os tres cenarios abaixo sao os tres obrigatorios do relatorio de
# verificacao independente: (1) fonte com FOR -> DYNAMIC_SQL; (2) fonte
# sem FOR -> NO_DATA_DEP; (3) fonte INDISPONIVEL -> DYNAMIC_SQL opaque
# (regra de desempate -- indecidivel classifica DINAMICO, nunca
# NO_DATA_DEP).


def _open_cache() -> SimpleNamespace:
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG_DYNAMIC_EVALUATOR", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "FN_ABRIR_CURSOR_DINAMICO", "PROCEDURE", "DEFINITION", "SIG_FN"),
    ]
    return SimpleNamespace(body_identifiers=identifiers, body_type="PACKAGE BODY", body_statements=[])


class _OpenSourceExtractor:
    """Fake minimo com SO a capacidade `source` -- mesmo padrao de
    `_SourceExtractor` acima, reusado aqui para alimentar
    `_open_stmt_is_dynamic` com um texto-fonte sintetico controlado."""

    def __init__(self, lines_by_number: Dict[int, str]) -> None:
        self._lines = lines_by_number

    def source(self, owner: str, object_name: str) -> List[extract.FetchSourceRow]:
        return [
            extract.FetchSourceRow(type="PACKAGE BODY", line=line, text=text)
            for line, text in sorted(self._lines.items())
        ]


def test_open_with_for_in_source_is_dynamic_sql():
    # Cenario 1 (obrigatorio): OPEN c FOR v_sql; -- fonte PROVA dinamico.
    statement = Assignment(
        usage_id=3, line=29, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {29: "OPEN v_cursor FOR v_sql;\n"}

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"
    assert edges[0].edge_type != "NO_DATA_DEP"


def test_open_without_for_in_source_is_no_dependency():
    # Cenario 2 (obrigatorio): OPEN c; estatico -- fonte PROVA que fecha
    # sem "FOR", cursor ja declarado com SELECT proprio.
    statement = Assignment(
        usage_id=3, line=29, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {29: "OPEN v_cursor;\n"}

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "NO_DATA_DEP"
    assert edges[0].op == "OPEN"


def test_open_sharing_physical_line_with_prior_statement_is_still_dynamic():
    # Regressao da 3a recorrencia da mesma classe de defeito (verificacao
    # independente, rodada 3): PL/SQL permite mais de um statement na mesma
    # linha FISICA, e `v_sql := '...' || p_x; OPEN c FOR v_sql;` e idioma
    # comum. A versao anterior cortava no PRIMEIRO ";" da linha inteira --
    # o ";" do statement ANTERIOR -- e "provava" estatico um ref cursor
    # dinamico, que sumia de PONTOS CEGOS como NO_DATA_DEP. A varredura
    # agora ancora no token OPEN, entao o ";" procurado e o que fecha o
    # proprio OPEN.
    statement = Assignment(
        usage_id=3, line=42, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {42: "v_sql := 'SELECT 1 FROM dual WHERE x = ' || p_id; OPEN c FOR v_sql;\n"}

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"


def test_open_static_sharing_physical_line_with_prior_statement_stays_static():
    # Contraparte do caso acima: OPEN ESTATICO dividindo a linha com outro
    # statement continua provavel como estatico -- a ancora no token OPEN
    # nao pode transformar todo OPEN em linha compartilhada num falso
    # dinamico (isso inverteria o defeito em vez de corrigi-lo).
    statement = Assignment(
        usage_id=3, line=42, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {42: "v_total := v_total + 1; OPEN cur_pedidos;\n"}

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "NO_DATA_DEP"


def test_open_line_without_open_token_is_indecidable_hence_dynamic():
    # Linha reportada pelo PL/Scope sem nenhum token OPEN no texto (fonte
    # divergente da posicao reportada) -- indecidivel, desempate manda
    # DINAMICO, nunca NO_DATA_DEP.
    statement = Assignment(
        usage_id=3, line=42, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {42: "v_total := v_total + 1;\n"}

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"


def test_open_multiline_for_after_shared_first_line_is_dynamic():
    # OPEN dinamico multi-linha CUJA primeira linha e compartilhada:
    # `...; OPEN c FOR` na linha N e o SELECT na linha N+1. A ancora no
    # OPEN + janela multi-linha precisam cooperar.
    statement = Assignment(
        usage_id=3, line=42, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {
        42: "v_sql := v_sql || p_ord; OPEN c FOR\n",
        43: "    v_sql;\n",
    }

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"


def test_open_without_source_capability_defaults_to_dynamic_opaque():
    # Cenario 3 (obrigatorio): fonte INDISPONIVEL -- regra de desempate,
    # indecidivel classifica DINAMICO (opaque), NUNCA NO_DATA_DEP. A
    # falha e assimetrica: um ponto cego a mais e ruido descartavel; um
    # ponto cego a menos e SQL dinamico invisivel.
    statement = Assignment(
        usage_id=3, line=29, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=None,  # sem capacidade `source` nenhuma
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"
    assert edges[0].confidence == "opaque"
    assert edges[0].edge_type != "NO_DATA_DEP"


def test_open_multiline_statement_with_for_on_second_line_is_dynamic():
    # Statement pode quebrar em mais de uma linha -- a janela de busca
    # (`_OPEN_SOURCE_WINDOW`) cobre isso; "FOR" aparece so na 2a linha,
    # antes do ";" que fecha na 3a.
    statement = Assignment(
        usage_id=3, line=29, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {
        29: "OPEN v_cursor\n",
        30: "  FOR v_sql\n",
        31: "  || p_filtro_extra;\n",
    }

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"


def test_open_statement_that_never_closes_within_window_defaults_to_dynamic():
    # Statement nao fecha (sem ";") dentro da janela de busca -- tambem
    # indecidivel, mesma regra de desempate do cenario 3.
    statement = Assignment(
        usage_id=3, line=29, kind="STMT", target="OPEN", enclosing="FN_ABRIR_CURSOR_DINAMICO"
    )
    source_lines = {ln: "  -- comentario sem fechamento\n" for ln in range(29, 40)}
    source_lines[29] = "OPEN v_cursor\n"

    edges = expand_access(
        owner="GESTAO_OO",
        object_name="PKG_DYNAMIC_EVALUATOR",
        subprogram="FN_ABRIR_CURSOR_DINAMICO",
        statement=statement,
        object_cache=_open_cache(),
        extractor=_OpenSourceExtractor(source_lines),
    )

    assert len(edges) == 1
    assert edges[0].edge_type == "DYNAMIC_SQL"
    assert edges[0].confidence == "opaque"


# --------------------------------------------------------------- entrega 2


def _pkg_state_identifiers() -> List[IdentifierRow]:
    """Fixture sintetica: PKG_STATE com estado de package (G_COUNTER),
    subprograma WRITER (assina) e READER (le), mais um subprograma
    LOCAL_ONLY com variavel LOCAL homonima do TIPO estado (VARIABLE) mas
    declarada dentro de si mesmo -- negativo do contrato (nunca vira
    estado compartilhado)."""
    return [
        IdentifierRow(1, 0, 1, 1, "PKG_STATE", "PACKAGE", "DEFINITION"),
        # G_COUNTER declarado direto sob a raiz (contexto=1=raiz) -- estado.
        IdentifierRow(2, 1, 2, 3, "G_COUNTER", "VARIABLE", "DECLARATION"),
        IdentifierRow(3, 2, 2, 20, "NUMBER", "NUMBER DATATYPE", "REFERENCE"),
        # WRITER: assina G_COUNTER.
        IdentifierRow(10, 1, 5, 1, "WRITER", "PROCEDURE", "DEFINITION", "SIG_WRITER"),
        IdentifierRow(11, 10, 6, 5, "G_COUNTER", "VARIABLE", "ASSIGNMENT"),
        # READER: le G_COUNTER.
        IdentifierRow(20, 1, 9, 1, "READER", "PROCEDURE", "DEFINITION", "SIG_READER"),
        IdentifierRow(21, 20, 10, 5, "G_COUNTER", "VARIABLE", "REFERENCE"),
        # LOCAL_ONLY: V_LOCAL declarada e assinada DENTRO de si mesma --
        # jamais estado, mesmo sendo VARIABLE.
        IdentifierRow(30, 1, 13, 1, "LOCAL_ONLY", "PROCEDURE", "DEFINITION", "SIG_LOCAL_ONLY"),
        IdentifierRow(31, 30, 14, 5, "V_LOCAL", "VARIABLE", "DECLARATION"),
        IdentifierRow(32, 31, 14, 15, "NUMBER", "NUMBER DATATYPE", "REFERENCE"),
        IdentifierRow(33, 30, 15, 5, "V_LOCAL", "VARIABLE", "ASSIGNMENT"),
        # G_UNUSED: declarado sob a raiz, NUNCA referenciado/assinado em
        # lugar nenhum -- prova que DECLARATION sozinha nao gera aresta.
        IdentifierRow(40, 1, 17, 3, "G_UNUSED", "VARIABLE", "DECLARATION"),
    ]


def _pkg_state_cache() -> SimpleNamespace:
    return SimpleNamespace(body_identifiers=_pkg_state_identifiers(), body_type="PACKAGE BODY")


def test_state_write_edge_for_package_variable():
    edges = expand_access(
        owner="GESTAO", object_name="PKG_STATE", subprogram="WRITER", statement=None, object_cache=_pkg_state_cache()
    )
    state_edges = [e for e in edges if e.edge_type in ("STATE_READ", "STATE_WRITE")]
    assert len(state_edges) == 1
    edge = state_edges[0]
    assert edge.edge_type == "STATE_WRITE"
    assert edge.from_ref == "GESTAO.PKG_STATE.WRITER"
    assert edge.to_ref == "GESTAO.PKG_STATE.__STATE__"
    assert edge.line == 6


def test_state_read_edge_for_package_variable():
    edges = expand_access(
        owner="GESTAO", object_name="PKG_STATE", subprogram="READER", statement=None, object_cache=_pkg_state_cache()
    )
    state_edges = [e for e in edges if e.edge_type in ("STATE_READ", "STATE_WRITE")]
    assert len(state_edges) == 1
    edge = state_edges[0]
    assert edge.edge_type == "STATE_READ"
    assert edge.from_ref == "GESTAO.PKG_STATE.READER"
    assert edge.to_ref == "GESTAO.PKG_STATE.__STATE__"


def test_local_variable_never_becomes_state_synthetic():
    # Negativo do contrato: LOCAL_ONLY assina V_LOCAL (mesmo TIPO de
    # estado, VARIABLE), mas a DECLARATION dela esta dentro do proprio
    # LOCAL_ONLY (contexto=30=a DEFINITION do subprograma), nao sob a
    # raiz do PACKAGE -- nunca pode virar STATE_WRITE.
    edges = expand_access(
        owner="GESTAO",
        object_name="PKG_STATE",
        subprogram="LOCAL_ONLY",
        statement=None,
        object_cache=_pkg_state_cache(),
    )
    assert not any(e.edge_type in ("STATE_READ", "STATE_WRITE") for e in edges)


def test_local_variable_never_becomes_state_real_flow_demo():
    # Mesmo negativo, mas contra a arvore REAL (fato provado no plano,
    # secao 2 item 4): V_SQL e local a RUN_DYNAMIC (usage_id=28,
    # contexto=25=a DEFINITION de RUN_DYNAMIC) e e assinada em usage_id=31
    # -- nunca pode gerar STATE_WRITE mesmo sendo VARIABLE.
    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="RUN_DYNAMIC",
        statement=None,
        object_cache=_flow_demo_cache(),
    )
    assert not any(e.edge_type in ("STATE_READ", "STATE_WRITE") for e in edges)


def test_declaration_alone_generates_no_edge():
    # G_UNUSED e declarado sob a raiz do package mas NUNCA
    # referenciado/assinado -- nao ha subprograma nenhum que possa gerar
    # aresta para ele, porque so ASSIGNMENT/REFERENCE viram aresta
    # (DECLARATION sozinha nunca gera). Varrendo todos os subprogramas da
    # fixture, nenhuma aresta aponta para G_UNUSED em lugar nenhum.
    for subprogram in ("WRITER", "READER", "LOCAL_ONLY"):
        edges = expand_access(
            owner="GESTAO",
            object_name="PKG_STATE",
            subprogram=subprogram,
            statement=None,
            object_cache=_pkg_state_cache(),
        )
        assert all("G_UNUSED" not in (e.reason or "") for e in edges)
    # E, mais direto: nenhum STATE_* nasce so da DECLARATION -- a fixture
    # so contem os 2 edges esperados (WRITER->G_COUNTER, READER->G_COUNTER)
    # em todo o objeto, nunca 3.
    all_state_edges = []
    for subprogram in ("WRITER", "READER", "LOCAL_ONLY"):
        all_state_edges.extend(
            e
            for e in expand_access(
                owner="GESTAO",
                object_name="PKG_STATE",
                subprogram=subprogram,
                statement=None,
                object_cache=_pkg_state_cache(),
            )
            if e.edge_type in ("STATE_READ", "STATE_WRITE")
        )
    assert len(all_state_edges) == 2


# --------------------------------------------------------------- entrega 3


class _TriggerRow:
    def __init__(self, table_owner: str, table_name: str, trigger_name: str, triggering_event: str):
        self.table_owner = table_owner
        self.table_name = table_name
        self.trigger_name = trigger_name
        self.triggering_event = triggering_event


def test_write_discovers_trigger_and_returns_subprogram_shaped_edge():
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)

    class _Extractor:
        def triggers(self, owner: str, table_names: List[str]) -> List[_TriggerRow]:
            assert owner == "GESTAO"
            assert table_names == ["FLOW_DEMO_LOG"]
            return [_TriggerRow("GESTAO", "FLOW_DEMO_LOG", "TRG_FLOW_DEMO_LOG", "INSERT")]

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
        extractor=_Extractor(),
    )
    trigger_edges = [e for e in edges if e.edge_type == "TRIGGER_FIRES"]
    assert len(trigger_edges) == 1
    edge = trigger_edges[0]
    assert edge.from_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.to_ref == "GESTAO.TRG_FLOW_DEMO_LOG.TRG_FLOW_DEMO_LOG"  # ref de SUBPROGRAMA, 3 partes
    assert edge.op == "INSERT"


def test_no_trigger_edge_when_extractor_lacks_triggers_method():
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)
    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="LOG_MSG",
        statement=insert_assignment,
        object_cache=_flow_demo_cache(),
        extractor=None,
    )
    assert not any(e.edge_type == "TRIGGER_FIRES" for e in edges)


# ---- integracao completa: ciclo de trigger via build_proc_graph ----


def _run_with_timeout(fn, timeout: float = 5.0):
    """Mesmo arnes de tests/test_procgraph_bfs.py::_run_with_timeout
    (duplicado deliberadamente -- teste nao importa de outro modulo de
    teste): prova de terminacao rodando a travessia numa thread separada.
    O motor NUNCA usa timeout como mecanismo de parada; isto e so o arnes
    do teste."""
    box: Dict[str, Any] = {}

    def _target() -> None:
        box["result"] = fn()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "travessia nao terminou dentro do timeout -- possivel loop infinito"
    return box["result"]


class _TriggerCycleExtractor:
    """Fake de `procgraph.ProcExtractor` (+`triggers`) para o ciclo
    T1 -> TRG1 escreve T2 -> TRG2 escreve T1. `objects` segue o mesmo
    formato de tests/fixtures/procgraph_demo.json (trees por
    object_type)."""

    def __init__(self):
        self.objects: Dict[str, Dict[str, Any]] = {
            "GESTAO.P_START": {
                "trees": [
                    {
                        "object_type": "PACKAGE BODY",
                        "identifiers": [
                            {"usage_id": 1, "usage_context_id": 0, "line": 1, "col": 1, "name": "P_START", "type": "PACKAGE", "usage": "DEFINITION", "signature": None},
                            {"usage_id": 2, "usage_context_id": 1, "line": 3, "col": 1, "name": "START", "type": "PROCEDURE", "usage": "DEFINITION", "signature": "SIG_START"},
                        ],
                        "statements": [
                            {"usage_id": 3, "usage_context_id": 2, "line": 4, "stmt_type": "INSERT"},
                        ],
                        # identificadores filhos do statement 3 (tabela T1) --
                        # anexados via post-processing abaixo (mesmo tree).
                    }
                ],
                "stmt_children": {3: [{"usage_id": 4, "usage_context_id": 3, "line": 4, "col": 10, "name": "T1", "type": "TABLE", "usage": "REFERENCE", "signature": None}]},
            },
            "GESTAO.TRG1": {
                "trees": [
                    {
                        "object_type": "TRIGGER",
                        "identifiers": [
                            {"usage_id": 1, "usage_context_id": 0, "line": 1, "col": 1, "name": "TRG1", "type": "TRIGGER", "usage": "DECLARATION", "signature": "SIG_TRG1"},
                            {"usage_id": 2, "usage_context_id": 1, "line": 1, "col": 1, "name": "TRG1", "type": "TRIGGER", "usage": "DEFINITION", "signature": "SIG_TRG1"},
                        ],
                        "statements": [
                            {"usage_id": 3, "usage_context_id": 2, "line": 2, "stmt_type": "INSERT"},
                        ],
                    }
                ],
                "stmt_children": {3: [{"usage_id": 4, "usage_context_id": 3, "line": 2, "col": 10, "name": "T2", "type": "TABLE", "usage": "REFERENCE", "signature": None}]},
            },
            "GESTAO.TRG2": {
                "trees": [
                    {
                        "object_type": "TRIGGER",
                        "identifiers": [
                            {"usage_id": 1, "usage_context_id": 0, "line": 1, "col": 1, "name": "TRG2", "type": "TRIGGER", "usage": "DECLARATION", "signature": "SIG_TRG2"},
                            {"usage_id": 2, "usage_context_id": 1, "line": 1, "col": 1, "name": "TRG2", "type": "TRIGGER", "usage": "DEFINITION", "signature": "SIG_TRG2"},
                        ],
                        "statements": [
                            {"usage_id": 3, "usage_context_id": 2, "line": 2, "stmt_type": "INSERT"},
                        ],
                    }
                ],
                # TRG2 escreve de volta em T1 -- fecha o ciclo.
                "stmt_children": {3: [{"usage_id": 4, "usage_context_id": 3, "line": 2, "col": 10, "name": "T1", "type": "TABLE", "usage": "REFERENCE", "signature": None}]},
            },
        }
        self.id_calls: Dict[str, int] = {}

    def _ref(self, owner: str, object_name: str) -> str:
        return "{}.{}".format(owner.upper(), object_name.upper())

    def plscope_identifiers(self, owner: str, object_name: str) -> List[Any]:
        ref = self._ref(owner, object_name)
        self.id_calls[ref] = self.id_calls.get(ref, 0) + 1
        obj = self.objects.get(ref)
        if obj is None:
            return []
        rows: List[Any] = []
        for tree in obj["trees"]:
            for row in tree["identifiers"]:
                rows.append(SimpleNamespace(object_type=tree["object_type"], **row))
        for children in obj.get("stmt_children", {}).values():
            for row in children:
                rows.append(SimpleNamespace(object_type=obj["trees"][0]["object_type"], **row))
        return rows

    def plscope_statements(self, owner: str, object_name: str) -> List[Any]:
        ref = self._ref(owner, object_name)
        obj = self.objects.get(ref)
        if obj is None:
            return []
        rows: List[Any] = []
        for tree in obj["trees"]:
            for row in tree["statements"]:
                rows.append(SimpleNamespace(object_type=tree["object_type"], **row))
        return rows

    def resolve_owner(self, signature: str) -> Optional[Tuple[str, str]]:
        return None

    def triggers(self, owner: str, table_names: List[str]) -> List[_TriggerRow]:
        mapping = {"T1": "TRG1", "T2": "TRG2"}
        out = []
        for name in table_names:
            trg = mapping.get(name.upper())
            if trg:
                out.append(_TriggerRow(owner, name.upper(), trg, "INSERT"))
        return out


def test_trigger_cycle_between_two_tables_terminates_and_is_declared():
    # PROVA do contrato: T1 -> TRG1 escreve T2 -> TRG2 escreve T1. A
    # travessia via build_proc_graph (motor real, T-03 + seam de T-05)
    # tem que TERMINAR sob timeout curto e o ciclo tem que aparecer
    # declarado no grafo -- via o visited-set do motor (nunca cap/
    # timeout/contador, regra dura do contrato).
    extractor = _TriggerCycleExtractor()

    result = _run_with_timeout(
        lambda: build_proc_graph(extractor, ("GESTAO", "P_START", "START")), timeout=5.0
    )

    edge_pairs = {(e.from_ref, e.to_ref, e.edge_type) for e in result.edges}
    assert ("GESTAO.T1", "GESTAO.TRG1.TRG1", "TRIGGER_FIRES") in edge_pairs
    assert ("GESTAO.T2", "GESTAO.TRG2.TRG2", "TRIGGER_FIRES") in edge_pairs

    refs = {(n.owner, n.object_name, n.subprogram) for n in result.nodes}
    assert ("GESTAO", "TRG1", "TRG1") in refs
    assert ("GESTAO", "TRG2", "TRG2") in refs

    write_pairs = {(e.from_ref, e.to_ref) for e in result.edges if e.edge_type == "WRITE"}
    assert ("GESTAO.P_START.START", "GESTAO.T1") in write_pairs
    assert ("GESTAO.TRG1.TRG1", "GESTAO.T2") in write_pairs
    assert ("GESTAO.TRG2.TRG2", "GESTAO.T1") in write_pairs

    # Cada trigger e visitado UMA vez so (visited-set) mesmo com o ciclo.
    assert extractor.id_calls["GESTAO.TRG1"] == 1
    assert extractor.id_calls["GESTAO.TRG2"] == 1


# ------------------------------------------------------- entrega 4 (defeito 1)
#
# GESTAO.FLOW_DEMO.RUN_DYNAMIC tem DOIS EXECUTE IMMEDIATE reais (linhas 28
# e 31, confirmados em ALL_STATEMENTS contra o banco dev: usage_id 30 e 33,
# ambos usage_context_id=25=RUN_DYNAMIC). Antes desta correcao,
# `_table_access_edges` devolvia `[]` para qualquer statement que nao fosse
# SELECT/INSERT/UPDATE/DELETE/MERGE -- o SQL dinamico sumia sem aresta, sem
# ponto cego, com o no saindo `leaf: sim`. Os testes abaixo prova que as
# DUAS ocorrencias aparecem, sempre atribuidas a RUN_DYNAMIC (nunca a
# MAIN), com a mesma classificacao que o modo objeto ja produz (L28
# resolved/exact, L31 partial -> FLOW_DEMO_LOG).


def _flow_demo_statements() -> List[StatementRow]:
    fixture = _load_flow_demo()
    return [
        StatementRow(
            usage_id=row["usage_id"],
            usage_context_id=row["usage_context_id"],
            line=row["line"],
            stmt_type=row["stmt_type"],
        )
        for row in fixture["statements"]
    ]


def _flow_demo_cache_with_statements() -> SimpleNamespace:
    fixture = _load_flow_demo()
    return SimpleNamespace(
        body_identifiers=_identifiers_from(fixture["identifiers"]),
        body_type=fixture["object_type"],
        body_statements=_flow_demo_statements(),
    )


def _flow_demo_source_rows() -> List[extract.FetchSourceRow]:
    # Texto-fonte REAL de GESTAO.FLOW_DEMO (banco dev, T-01) -- mesma
    # fixture que tests/test_depgraph_enrich*.py ja usa para o modo
    # objeto, reaproveitada aqui para a comparacao "granular nao pode ser
    # PIOR" ser honesta (mesmo fonte, mesmo resultado esperado).
    payload = json.loads(FLOW_DEMO_SOURCE_FIXTURE.read_text(encoding="utf-8"))
    lines = payload["fetch_source"]["PACKAGE BODY"]
    return [extract.FetchSourceRow(type="PACKAGE BODY", line=i + 1, text=text) for i, text in enumerate(lines)]


class _SourceExtractor:
    """Fake minimo com SO a capacidade `source` (Protocol OPCIONAL de
    `procgraph.ProcExtractor`, correcao do defeito 1)."""

    def __init__(self, rows: List[extract.FetchSourceRow]) -> None:
        self._rows = rows

    def source(self, owner: str, object_name: str) -> List[extract.FetchSourceRow]:
        return self._rows


class _CatalogRow:
    def __init__(self, object_name: str) -> None:
        self.object_name = object_name


class _CatalogDepExtractor:
    """Fake minimo com SO `object_catalog` -- o unico metodo de
    `depgraph.DepExtractor` que `_dynamic_sql_edges` consome (nivel
    "partial")."""

    def __init__(self, names: List[str]) -> None:
        self._names = names

    def object_catalog(self, owner: str) -> List[_CatalogRow]:
        return [_CatalogRow(n) for n in self._names]


def test_dynamic_sql_resolved_exact_edge_attributed_to_run_dynamic():
    fixture = _load_flow_demo()
    exec_l28 = _assignment_for(fixture, usage_id=30)
    assert exec_l28.enclosing == "RUN_DYNAMIC"
    assert exec_l28.kind == "STMT"
    assert exec_l28.line == 28

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="RUN_DYNAMIC",
        statement=exec_l28,
        object_cache=_flow_demo_cache_with_statements(),
        extractor=_SourceExtractor(_flow_demo_source_rows()),
        dep_extractor=_CatalogDepExtractor(["FLOW_DEMO_LOG"]),
    )
    dyn_edges = [e for e in edges if e.edge_type == "DYNAMIC_SQL"]
    assert len(dyn_edges) == 1
    edge = dyn_edges[0]
    assert edge.from_ref == "GESTAO.FLOW_DEMO.RUN_DYNAMIC"
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.confidence == "exact"
    assert edge.line == 28


def test_dynamic_sql_partial_edge_l31_points_to_flow_demo_log():
    fixture = _load_flow_demo()
    exec_l31 = _assignment_for(fixture, usage_id=33)
    assert exec_l31.enclosing == "RUN_DYNAMIC"
    assert exec_l31.line == 31

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="RUN_DYNAMIC",
        statement=exec_l31,
        object_cache=_flow_demo_cache_with_statements(),
        extractor=_SourceExtractor(_flow_demo_source_rows()),
        dep_extractor=_CatalogDepExtractor(["FLOW_DEMO_LOG"]),
    )
    dyn_edges = [e for e in edges if e.edge_type == "DYNAMIC_SQL"]
    assert len(dyn_edges) == 1
    edge = dyn_edges[0]
    assert edge.from_ref == "GESTAO.FLOW_DEMO.RUN_DYNAMIC"
    assert edge.to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert edge.confidence == "partial"
    assert edge.line == 31


def test_dynamic_sql_without_source_capability_becomes_opaque_never_disappears():
    # REGRA INEGOCIAVEL do contrato: sem a capacidade `source` no
    # extractor, o SQL dinamico NUNCA some -- vira marcador opaque
    # declarado.
    fixture = _load_flow_demo()
    exec_l28 = _assignment_for(fixture, usage_id=30)

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="RUN_DYNAMIC",
        statement=exec_l28,
        object_cache=_flow_demo_cache_with_statements(),
        extractor=None,
    )
    dyn_edges = [e for e in edges if e.edge_type == "DYNAMIC_SQL"]
    assert len(dyn_edges) == 1
    assert dyn_edges[0].confidence == "opaque"
    assert dyn_edges[0].to_ref == depgraph_enrich.UNKNOWN_TARGET
    assert dyn_edges[0].from_ref == "GESTAO.FLOW_DEMO.RUN_DYNAMIC"


def test_dynamic_sql_without_extractor_at_all_still_becomes_opaque():
    fixture = _load_flow_demo()
    exec_l31 = _assignment_for(fixture, usage_id=33)

    edges = expand_access(
        owner="GESTAO",
        object_name="FLOW_DEMO",
        subprogram="RUN_DYNAMIC",
        statement=exec_l31,
        object_cache=_flow_demo_cache_with_statements(),
    )
    dyn_edges = [e for e in edges if e.edge_type == "DYNAMIC_SQL"]
    assert len(dyn_edges) == 1
    assert dyn_edges[0].confidence == "opaque"


class FakeExtractorWithSource(FakeExtractor):
    """Mesmo fake de tests/test_procgraph_bfs.py, com a capacidade
    OPCIONAL `source` (defeito 1: SQL dinamico so resolve/classifica
    quando o extractor oferece texto-fonte)."""

    def __init__(self, fixture: Dict[str, Any], source_by_ref: Dict[str, List[Any]]) -> None:
        super().__init__(fixture)
        self._source_by_ref = source_by_ref

    def source(self, owner: str, object_name: str) -> List[Any]:
        return self._source_by_ref.get(self._ref(owner, object_name), [])


def _flow_demo_source_by_ref() -> Dict[str, List[Any]]:
    return {"GESTAO.FLOW_DEMO": _flow_demo_source_rows()}


def test_build_proc_graph_attributes_both_dynamic_sql_lines_to_run_dynamic_never_main():
    # Integracao completa (motor real T-03 + seam T-05): as DUAS
    # ocorrencias de SQL dinamico de RUN_DYNAMIC tem que aparecer no
    # resultado de `build_proc_graph`, atribuidas ao subprograma exato --
    # nunca a MAIN (que so CHAMA run_dynamic, nunca contem SQL dinamico
    # nenhum).
    fixture = _load_fixture()  # tests/fixtures/procgraph_demo.json (T-03)
    extractor = FakeExtractorWithSource(fixture, _flow_demo_source_by_ref())
    dep_extractor = _CatalogDepExtractor(["FLOW_DEMO_LOG", "FLOW_DEMO_AUDIT"])

    result = build_proc_graph(extractor, ("GESTAO", "FLOW_DEMO", "MAIN"), dep_extractor=dep_extractor)

    dyn_edges = [e for e in result.edges if e.edge_type == "DYNAMIC_SQL"]
    by_line = {e.line: e for e in dyn_edges}
    assert set(by_line) == {28, 31}
    assert all(e.from_ref == "GESTAO.FLOW_DEMO.RUN_DYNAMIC" for e in dyn_edges)
    assert not any(e.from_ref == "GESTAO.FLOW_DEMO.MAIN" for e in dyn_edges)

    assert by_line[28].confidence == "exact"
    assert by_line[28].to_ref == "GESTAO.FLOW_DEMO_LOG"
    assert by_line[31].confidence == "partial"
    assert by_line[31].to_ref == "GESTAO.FLOW_DEMO_LOG"

    # Criterio de aceite literal do modo objeto, aplicado ao granular:
    # nenhuma ocorrencia de SQL dinamico fica sem finding.
    assert len(dyn_edges) == 2

    # RUN_DYNAMIC agora tem arestas de saida (nao sai mais totalmente sem
    # rastro) -- is_leaf continua True (semantica inalterada: "nao chama
    # nada", ver test_inter_package_chain_reaches_all_three_objects), mas
    # o no ja nao fica sem NENHUMA aresta.
    run_dynamic_node = next(n for n in result.nodes if n.subprogram == "RUN_DYNAMIC")
    assert run_dynamic_node.is_leaf is True
    run_dynamic_out = [e for e in result.edges if e.from_ref == "GESTAO.FLOW_DEMO.RUN_DYNAMIC"]
    assert len(run_dynamic_out) == 2

    # L31 (irresolvivel) vira ponto cego declarado, mesmo formato do modo
    # objeto ("GESTAO.FLOW_DEMO L31 [partial] -> GESTAO.FLOW_DEMO_LOG").
    blind_text = " ".join(result.blind_spots)
    assert "GESTAO.FLOW_DEMO.RUN_DYNAMIC" in blind_text
    assert "L31" in blind_text
    assert "[partial]" in blind_text
    assert "GESTAO.FLOW_DEMO_LOG" in blind_text

    # As duas assercoes negativas do contrato (test_procgraph_bfs.py)
    # continuam validas aqui tambem: MAIN nunca chama LENGTH nem escreve
    # em FLOW_DEMO_LOG.
    main_edges = [e for e in result.edges if e.from_ref == "GESTAO.FLOW_DEMO.MAIN"]
    assert not any("LENGTH" in e.to_ref for e in main_edges)
    assert not any("FLOW_DEMO_LOG" in e.to_ref for e in main_edges)

    # Cada ocorrencia real detectada tem exatamente uma entrada em
    # statements_read (denominador honesto da COBERTURA, defeito 2) --
    # ambas representadas por aresta.
    run_dynamic_stmts = [
        (ref, line) for ref, line, _stmt_type in result.statements_read if ref == "GESTAO.FLOW_DEMO.RUN_DYNAMIC"
    ]
    assert sorted(run_dynamic_stmts) == [
        ("GESTAO.FLOW_DEMO.RUN_DYNAMIC", 28),
        ("GESTAO.FLOW_DEMO.RUN_DYNAMIC", 31),
    ]


# ------------------------------------------------------- determinismo


def test_expand_access_is_deterministic():
    fixture = _load_flow_demo()
    insert_assignment = _assignment_for(fixture, usage_id=6)

    edges1 = expand_access(
        owner="GESTAO", object_name="FLOW_DEMO", subprogram="LOG_MSG", statement=insert_assignment, object_cache=_flow_demo_cache()
    )
    edges2 = expand_access(
        owner="GESTAO", object_name="FLOW_DEMO", subprogram="LOG_MSG", statement=insert_assignment, object_cache=_flow_demo_cache()
    )
    assert edges1 == edges2
