"""Testes offline (T-02, contrato dynsql-dossie) para
plsqlflow/dynsite_template.py.

Modulo puro sob teste: nenhum destes casos abre conexao nem executa o SQL
analisado. Cada teste e nomeado pela REGRA do contrato que ele cobre
(docs/backlog-sql-dinamico-estatico.md secao 4.2/4.2.1), na mesma linha de
tests/test_dynsite_categoria.py (T-01): o nome do teste que falhar diz
exatamente qual regra quebrou.

Fontes PL/SQL sao construidas inline (listas de string, indice 0 = linha 1,
mesma convencao de dynsql.rows_from_lines / dynsite.classify_dynamic_site) --
T-02 nao tem arquivo de fixture dedicado (Plans.md [T-02] so lista
plsqlflow/dynsite_template.py e tests/test_dynsite_template.py).
"""
from __future__ import annotations

from typing import List

from plsqlflow.dynsite_template import (
    RECONSTRUCAO_COMPLETA,
    RECONSTRUCAO_PARCIAL,
    ReconstructedTemplate,
    reconstruct_template,
)


def test_atribuicao_unica_100_por_cento_literal_e_completa_sem_lacunas() -> None:
    source = [
        "PROCEDURE p1 IS",                            # 1
        "  v_sql VARCHAR2(200);",                      # 2
        "BEGIN",                                        # 3
        "  v_sql := 'SELECT 1 FROM DUAL';",             # 4
        "  EXECUTE IMMEDIATE v_sql;",                    # 5
        "END p1;",                                       # 6
    ]
    result = reconstruct_template(source, exec_line=5, var_name="v_sql")

    assert result.variavel == "v_sql"
    assert result.gaps == []
    assert len(result.parts) == 1
    assert result.parts[0].kind == "literal"
    assert result.parts[0].text == "SELECT 1 FROM DUAL"
    assert result.parts[0].ref is None
    assert result.parts[0].condicional is None
    assert result.reconstrucao == RECONSTRUCAO_COMPLETA
    assert result.reconstrucao_motivo is None


def test_concatenacao_com_lacuna_simples_nao_forca_parcial() -> None:
    source = [
        "PROCEDURE p2 (p_id IN NUMBER) IS",                              # 1
        "  v_sql VARCHAR2(200);",                                        # 2
        "BEGIN",                                                          # 3
        "  v_sql := 'SELECT * FROM t WHERE id = ' || p_id;",             # 4
        "  EXECUTE IMMEDIATE v_sql;",                                     # 5
        "END p2;",                                                        # 6
    ]
    result = reconstruct_template(source, exec_line=5, var_name="v_sql")

    assert [p.kind for p in result.parts] == ["literal", "lacuna"]
    assert result.parts[0].text == "SELECT * FROM t WHERE id = "
    assert result.parts[1].ref == "L0"
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.ref == "L0"
    assert gap.raw_expr == "p_id"
    assert gap.line == 4
    assert gap.via_funcao is None
    assert gap.em_loop is False
    # T-02 nao classifica origem (isso e T-04) -- uma lacuna simples, por si
    # so, nunca torna a reconstrucao parcial.
    assert result.reconstrucao == RECONSTRUCAO_COMPLETA
    assert result.reconstrucao_motivo is None


def test_multiplas_atribuicoes_encadeadas_remontam_em_ordem() -> None:
    source = [
        "PROCEDURE p3 (p_ativo IN VARCHAR2) IS",                # 1
        "  v_sql VARCHAR2(400);",                                # 2
        "BEGIN",                                                  # 3
        "  v_sql := 'SELECT * FROM t';",                          # 4
        "  IF p_ativo THEN",                                      # 5
        "    v_sql := v_sql || ' WHERE ativo = 1';",              # 6
        "  END IF;",                                              # 7
        "  EXECUTE IMMEDIATE v_sql;",                              # 8
        "END p3;",                                                 # 9
    ]
    result = reconstruct_template(source, exec_line=8, var_name="v_sql")

    assert result.gaps == []
    assert [p.kind for p in result.parts] == ["literal", "literal"]
    assert result.parts[0].text == "SELECT * FROM t"
    assert result.parts[0].line == 4
    assert result.parts[1].text == " WHERE ativo = 1"
    assert result.parts[1].line == 6
    assert result.reconstrucao == RECONSTRUCAO_COMPLETA


def test_trecho_condicional_marca_condicional_em_literal_e_lacuna() -> None:
    source = [
        "PROCEDURE p4 (p_tipo IN VARCHAR2) IS",                        # 1
        "  v_sql VARCHAR2(400);",                                       # 2
        "BEGIN",                                                         # 3
        "  v_sql := 'SELECT * FROM t';",                                 # 4
        "  IF p_tipo IS NOT NULL THEN",                                  # 5
        "    v_sql := v_sql || ' WHERE tipo = ' || p_tipo;",             # 6
        "  END IF;",                                                     # 7
        "  EXECUTE IMMEDIATE v_sql;",                                     # 8
        "END p4;",                                                        # 9
    ]
    result = reconstruct_template(source, exec_line=8, var_name="v_sql")

    assert result.parts[0].condicional is None  # fora do IF
    literal_wrapped, gap_wrapped = result.parts[1], result.parts[2]
    assert literal_wrapped.text == " WHERE tipo = "
    assert literal_wrapped.condicional == "p_tipo IS NOT NULL"
    assert gap_wrapped.kind == "lacuna"
    assert gap_wrapped.condicional == "p_tipo IS NOT NULL"
    assert result.gaps[0].raw_expr == "p_tipo"
    # o trecho condicional, isolado, nao e um limite estrutural do T-02 --
    # so registra o fato (o SQL efetivo varia entre execucoes).
    assert result.reconstrucao == RECONSTRUCAO_COMPLETA


def test_lacuna_dentro_de_loop_marca_em_loop_true_e_forca_parcial() -> None:
    source = [
        "PROCEDURE p5 IS",                                              # 1
        "  v_sql VARCHAR2(400);",                                       # 2
        "BEGIN",                                                         # 3
        "  FOR i IN 1..10 LOOP",                                         # 4
        "    v_sql := 'SELECT * FROM t WHERE id = ' || i;",              # 5
        "    EXECUTE IMMEDIATE v_sql;",                                  # 6
        "  END LOOP;",                                                   # 7
        "END p5;",                                                       # 8
    ]
    result = reconstruct_template(source, exec_line=6, var_name="v_sql")

    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.raw_expr == "i"
    assert gap.em_loop is True
    assert result.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "LOOP" in result.reconstrucao_motivo


def test_lacuna_via_funcao_registra_nome_sem_atravessar_e_forca_parcial() -> None:
    source = [
        "PROCEDURE p6 (p_filtro IN VARCHAR2) IS",                       # 1
        "  v_sql VARCHAR2(400);",                                        # 2
        "BEGIN",                                                          # 3
        "  v_sql := pkg_helper.fn_montar_where(p_filtro);",              # 4
        "  EXECUTE IMMEDIATE v_sql;",                                     # 5
        "END p6;",                                                        # 6
    ]
    result = reconstruct_template(source, exec_line=5, var_name="v_sql")

    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.ref == "L0"
    assert gap.raw_expr == "pkg_helper.fn_montar_where(p_filtro)"
    assert gap.via_funcao == "pkg_helper.fn_montar_where"
    assert gap.em_loop is False
    # 4.2.1: a cadeia bateu numa funcao -- T-02 registra, NAO atravessa.
    assert result.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "funcao" in result.reconstrucao_motivo
    assert "L0" in result.reconstrucao_motivo


def test_regra_de_desempate_auto_referencia_sem_origem_forca_parcial() -> None:
    """A primeira atribuicao encontrada dentro do subprograma ja e
    auto-referencia (`v_sql := v_sql || ...`) -- a origem real esta fora do
    alcance da varredura (outro subprograma, ou inicializacao de pacote).
    Mesmo o restante sendo 100% literal, a regra de desempate do contrato
    (backlog secao 6: na duvida entre completa e parcial, e parcial) exige
    que isto saia parcial -- nunca completo por engano."""
    source = [
        "PROCEDURE p7 IS",                                    # 1
        "BEGIN",                                                # 2
        "  v_sql := v_sql || ' AND x = 1';",                    # 3
        "  EXECUTE IMMEDIATE v_sql;",                            # 4
        "END p7;",                                               # 5
    ]
    result = reconstruct_template(source, exec_line=4, var_name="v_sql")

    assert result.gaps == []
    assert len(result.parts) == 1
    assert result.parts[0].text == " AND x = 1"
    assert result.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "auto-referencia" in result.reconstrucao_motivo


def test_nenhuma_atribuicao_encontrada_forca_parcial_com_motivo() -> None:
    source = [
        "PROCEDURE p8 IS",                    # 1
        "BEGIN",                               # 2
        "  EXECUTE IMMEDIATE v_sql;",           # 3
        "END p8;",                              # 4
    ]
    result = reconstruct_template(source, exec_line=3, var_name="v_sql")

    assert result.parts == []
    assert result.gaps == []
    assert result.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "nenhuma atribuicao" in result.reconstrucao_motivo


def _gestao_oo_source() -> List[str]:
    """Reconstrucao sintetica em volta do texto REAL citado no backlog
    (docs/backlog-sql-dinamico-estatico.md secao 4.2, template extraido de
    GESTAO_OO.PKG_DYNAMIC_EVALUATOR.SP_EXTRAIR_METRICAS_TABELA, L43-54):
    literais e chamadas DBMS_ASSERT.ENQUOTE_NAME preservados exatamente como
    citados no backlog; linhas de preenchimento (blank filler, comentadas
    como tal no proprio T-01/tests/fixtures/dynsite_formas.json) sao so
    para posicionar o statement nas linhas certas -- nao sao codigo real do
    objeto."""
    lines: List[str] = ["PACKAGE BODY PKG_DYNAMIC_EVALUATOR IS"]  # 1
    lines += [""] * 41  # linhas 2-42 (filler)
    lines.append(  # linha 43
        "  PROCEDURE SP_EXTRAIR_METRICAS_TABELA "
        "(p_tabela_nome IN VARCHAR2, p_filtro_tipo IN VARCHAR2) IS"
    )
    lines.append("    v_sql VARCHAR2(4000);")  # 44
    lines.append("    v_count NUMBER; v_avg NUMBER; v_max NUMBER;")  # 45
    lines.append("  BEGIN")  # 46
    lines.append(  # 47
        "    v_sql := 'SELECT COUNT(*), NVL(AVG(x.duracao_estimada), 0), "
        "NVL(MAX(x.peso), 0) FROM ' || DBMS_ASSERT.ENQUOTE_NAME(p_tabela_nome) "
        "|| ' x ';"
    )
    lines.append("    IF p_filtro_tipo IS NOT NULL THEN")  # 48
    lines.append(  # 49
        "      v_sql := v_sql || 'WHERE VALUE(x) IS OF (' || "
        "DBMS_ASSERT.ENQUOTE_NAME(p_filtro_tipo) || ')';"
    )
    lines.append("    END IF;")  # 50
    lines += [""] * 3  # linhas 51-53 (filler)
    lines.append("    EXECUTE IMMEDIATE v_sql INTO v_count, v_avg, v_max;")  # 54
    lines.append("  END SP_EXTRAIR_METRICAS_TABELA;")  # 55
    lines.append("END PKG_DYNAMIC_EVALUATOR;")  # 56
    assert len(lines) == 56
    assert lines[42].lstrip().startswith("PROCEDURE SP_EXTRAIR_METRICAS_TABELA")
    assert lines[53].lstrip().startswith("EXECUTE IMMEDIATE")
    return lines


def _find_gap(result: ReconstructedTemplate, raw_expr_contains: str):
    matches = [gap for gap in result.gaps if raw_expr_contains in gap.raw_expr]
    assert len(matches) == 1, "esperava 1 lacuna contendo {!r}, achou {}".format(
        raw_expr_contains, len(matches)
    )
    return matches[0]


def test_regressao_gestao_oo_sp_extrair_metricas_tabela() -> None:
    source = _gestao_oo_source()
    result = reconstruct_template(source, exec_line=54, var_name="v_sql")

    assert result.variavel == "v_sql"
    # chave de correlacao: prefixo literal identico ao citado no backlog.
    assert result.parts[0].kind == "literal"
    assert result.parts[0].text == (
        "SELECT COUNT(*), NVL(AVG(x.duracao_estimada), 0), "
        "NVL(MAX(x.peso), 0) FROM "
    )
    assert result.parts[0].condicional is None

    gap_tabela = _find_gap(result, "p_tabela_nome")
    assert gap_tabela.raw_expr == "DBMS_ASSERT.ENQUOTE_NAME(p_tabela_nome)"
    assert gap_tabela.via_funcao == "DBMS_ASSERT.ENQUOTE_NAME"
    assert gap_tabela.em_loop is False

    gap_filtro = _find_gap(result, "p_filtro_tipo")
    assert gap_filtro.raw_expr == "DBMS_ASSERT.ENQUOTE_NAME(p_filtro_tipo)"
    assert gap_filtro.via_funcao == "DBMS_ASSERT.ENQUOTE_NAME"

    # o trecho da segunda atribuicao inteiro (literal E lacuna) esta
    # marcado condicional -- backlog: "[condicional: IF p_filtro_tipo IS
    # NOT NULL]".
    condicional_parts = [p for p in result.parts if p.condicional]
    assert condicional_parts, "nenhuma parte marcada como condicional"
    for part in condicional_parts:
        assert part.condicional == "p_filtro_tipo IS NOT NULL"

    # backlog: "e o exemplo dado no backlog com reconstrucao parcial" --
    # aqui, sob as regras do proprio T-02, o motivo e a chamada de funcao
    # (DBMS_ASSERT.ENQUOTE_NAME) nao atravessada, nunca a origem da lacuna
    # (isso e T-04).
    assert result.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "funcao" in result.reconstrucao_motivo
    assert "DBMS_ASSERT.ENQUOTE_NAME" in result.reconstrucao_motivo


def test_toda_lacuna_tem_raw_expr_e_line_preenchidos() -> None:
    """Invariante do contrato: nenhuma lacuna sai do dossie sem `raw_expr`
    e `line` -- varre todos os casos acima que produzem gaps."""
    casos = [
        (
            [
                "PROCEDURE p2 (p_id IN NUMBER) IS",
                "  v_sql VARCHAR2(200);",
                "BEGIN",
                "  v_sql := 'SELECT * FROM t WHERE id = ' || p_id;",
                "  EXECUTE IMMEDIATE v_sql;",
                "END p2;",
            ],
            5,
        ),
        (_gestao_oo_source(), 54),
    ]
    for source, exec_line in casos:
        result = reconstruct_template(source, exec_line=exec_line, var_name="v_sql")
        for gap in result.gaps:
            assert gap.raw_expr, "gap {} sem raw_expr".format(gap.ref)
            assert gap.line > 0, "gap {} sem line valida".format(gap.ref)
        for part in result.parts:
            assert part.line > 0, "part sem line valida"
            if part.kind == "lacuna":
                assert part.ref, "part lacuna sem ref"
            else:
                assert part.text is not None, "part literal sem text"
