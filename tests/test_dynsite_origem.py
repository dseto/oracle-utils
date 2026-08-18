"""Testes de `plsqlflow.dynsite_origin` (T-04, contrato dynsql-dossie).

Cobre os 12 casos obrigatorios do Plans.md/[T-04] mais a invariante do
contrato: nenhuma saida `NAO_DETERMINADO` sai sem `motivo`. Constroi
`TemplateGap` (T-02) diretamente para a maioria dos casos -- unico caso que
passa por `reconstruct_template` de verdade e o de regressao (caso 12),
para provar que o modulo funciona sobre o formato real que T-02 produz, nao
so sobre um dataclass montado a mao.
"""
from __future__ import annotations

from typing import List, Optional

from plsqlflow.dynsite_origin import (
    DOMINIO_LITERAL_DE_TEXTO,
    DOMINIO_NOME_DE_OBJETO,
    DOMINIO_NOME_DE_SCHEMA,
    FormalParam,
    LacunaOrigin,
    ORIGEM_NAO_DETERMINADO,
    ORIGEM_OUTRO_DINAMICO,
    ORIGEM_PARAMETRO_FORMAL,
    ORIGEM_RETORNO_DE_FUNCAO,
    ORIGEM_VARIAVEL_DE_PACOTE,
    PackageVarOrigin,
    classify_gap_origin,
)
from plsqlflow.dynsite_template import TemplateGap, reconstruct_template


def _gap(
    ref: str = "L0",
    raw_expr: str = "P_ALGO",
    line: int = 10,
    via_funcao: Optional[str] = None,
    em_loop: bool = False,
) -> TemplateGap:
    return TemplateGap(ref=ref, raw_expr=raw_expr, line=line, via_funcao=via_funcao, em_loop=em_loop)


# ---------------------------------------------------------------------------
# Caso 1: raw_expr bate com parametro formal conhecido
# ---------------------------------------------------------------------------


def test_raw_expr_bate_com_parametro_formal() -> None:
    gap = _gap(raw_expr="p_tabela_nome", line=20)
    formal_params = [FormalParam("P_TABELA_NOME", "VARCHAR2"), FormalParam("P_OUTRO")]

    origin = classify_gap_origin(gap, formal_params, [], [])

    assert origin.origem == ORIGEM_PARAMETRO_FORMAL
    assert origin.detalhe["nome"] == "P_TABELA_NOME"
    assert origin.detalhe["tipo"] == "VARCHAR2"
    assert origin.detalhe["posicao"] == 1
    assert origin.dominio is None
    assert origin.dominio_prova is None


# ---------------------------------------------------------------------------
# Caso 2: raw_expr bate com variavel de pacote conhecida
# ---------------------------------------------------------------------------


def test_raw_expr_bate_com_variavel_de_pacote() -> None:
    gap = _gap(raw_expr="g_filtro_padrao", line=21)
    package_vars = [
        PackageVarOrigin("G_FILTRO_PADRAO", ["SP_INICIALIZAR", "SP_RECARREGAR"])
    ]

    origin = classify_gap_origin(gap, [], package_vars, [])

    assert origin.origem == ORIGEM_VARIAVEL_DE_PACOTE
    assert origin.detalhe["nome"] == "G_FILTRO_PADRAO"
    assert origin.detalhe["subprogramas_que_atribuem"] == ["SP_INICIALIZAR", "SP_RECARREGAR"]


# ---------------------------------------------------------------------------
# Caso 3: via_funcao preenchido, lacuna nao atravessada -> RETORNO_DE_FUNCAO
# ---------------------------------------------------------------------------


def test_via_funcao_preenchido_vira_retorno_de_funcao() -> None:
    gap = _gap(
        raw_expr="PKG_HELPER.FN_MONTAR_WHERE(p_filtro)",
        line=22,
        via_funcao="PKG_HELPER.FN_MONTAR_WHERE",
    )

    origin = classify_gap_origin(gap, [], [], [])

    assert origin.origem == ORIGEM_RETORNO_DE_FUNCAO
    assert origin.detalhe["funcao"] == "PKG_HELPER.FN_MONTAR_WHERE"
    assert origin.motivo is not None and origin.motivo.strip() != ""


# ---------------------------------------------------------------------------
# Caso 4: raw_expr bate com known_site_ids -> OUTRO_DINAMICO
# ---------------------------------------------------------------------------


def test_raw_expr_bate_com_known_site_id() -> None:
    gap = _gap(raw_expr="v_outro_sql", line=23)

    origin = classify_gap_origin(gap, [], [], ["V_OUTRO_SQL"])

    assert origin.origem == ORIGEM_OUTRO_DINAMICO
    assert origin.detalhe["site_id"] == "V_OUTRO_SQL"


# ---------------------------------------------------------------------------
# Caso 5: nao reconhecida por nenhuma lista -> NAO_DETERMINADO com motivo
# ---------------------------------------------------------------------------


def test_nao_reconhecida_vira_nao_determinado_com_motivo() -> None:
    gap = _gap(raw_expr="v_completo_desconhecido", line=24)

    origin = classify_gap_origin(gap, [], [], [])

    assert origin.origem == ORIGEM_NAO_DETERMINADO
    assert origin.motivo is not None
    assert origin.motivo.strip() != ""
    assert "v_completo_desconhecido" in origin.motivo.lower() or "V_COMPLETO_DESCONHECIDO" in origin.motivo


# ---------------------------------------------------------------------------
# Casos 6-8: DBMS_ASSERT prova dominio; origem classificada e a do ARGUMENTO
# ---------------------------------------------------------------------------


def test_dbms_assert_enquote_name_prova_dominio_e_classifica_argumento() -> None:
    gap = _gap(
        raw_expr="DBMS_ASSERT.ENQUOTE_NAME(p_tabela_nome)",
        line=46,
        via_funcao="DBMS_ASSERT.ENQUOTE_NAME",
    )
    formal_params = [FormalParam("P_TABELA_NOME", "VARCHAR2")]

    origin = classify_gap_origin(gap, formal_params, [], [])

    assert origin.dominio == DOMINIO_NOME_DE_OBJETO
    assert origin.dominio_prova == "DBMS_ASSERT.ENQUOTE_NAME na linha 46"
    # a origem classificada e a do ARGUMENTO (p_tabela_nome), nao do wrapper
    assert origin.origem == ORIGEM_PARAMETRO_FORMAL
    assert origin.detalhe["nome"] == "P_TABELA_NOME"


def test_dbms_assert_schema_name_prova_dominio_nome_de_schema() -> None:
    gap = _gap(
        raw_expr="DBMS_ASSERT.SCHEMA_NAME(p_schema)",
        line=30,
        via_funcao="DBMS_ASSERT.SCHEMA_NAME",
    )
    formal_params = [FormalParam("P_SCHEMA")]

    origin = classify_gap_origin(gap, formal_params, [], [])

    assert origin.dominio == DOMINIO_NOME_DE_SCHEMA
    assert origin.dominio_prova == "DBMS_ASSERT.SCHEMA_NAME na linha 30"
    assert origin.origem == ORIGEM_PARAMETRO_FORMAL


def test_dbms_assert_enquote_literal_prova_dominio_literal_de_texto() -> None:
    gap = _gap(
        raw_expr="DBMS_ASSERT.ENQUOTE_LITERAL(p_valor)",
        line=31,
        via_funcao="DBMS_ASSERT.ENQUOTE_LITERAL",
    )
    formal_params = [FormalParam("P_VALOR")]

    origin = classify_gap_origin(gap, formal_params, [], [])

    assert origin.dominio == DOMINIO_LITERAL_DE_TEXTO
    assert origin.dominio_prova == "DBMS_ASSERT.ENQUOTE_LITERAL na linha 31"
    assert origin.origem == ORIGEM_PARAMETRO_FORMAL


# ---------------------------------------------------------------------------
# Caso 9: DBMS_ASSERT com argumento complexo -> dominio preenchido, mas
# origem do argumento sai NAO_DETERMINADO (prova que sao independentes)
# ---------------------------------------------------------------------------


def test_dbms_assert_com_argumento_complexo_dominio_independente_de_origem() -> None:
    gap = _gap(
        raw_expr="DBMS_ASSERT.ENQUOTE_NAME(p_prefixo || p_sufixo)",
        line=32,
        via_funcao="DBMS_ASSERT.ENQUOTE_NAME",
    )
    formal_params = [FormalParam("P_PREFIXO"), FormalParam("P_SUFIXO")]

    origin = classify_gap_origin(gap, formal_params, [], [])

    # dominio do wrapper continua provado normalmente
    assert origin.dominio == DOMINIO_NOME_DE_OBJETO
    assert origin.dominio_prova == "DBMS_ASSERT.ENQUOTE_NAME na linha 32"
    # mas o argumento e uma expressao, nao um identificador simples
    assert origin.origem == ORIGEM_NAO_DETERMINADO
    assert origin.motivo is not None and origin.motivo.strip() != ""
    assert "identificador simples" in origin.motivo


# ---------------------------------------------------------------------------
# Caso 10: sem wrapper, identificador cru que nao bate em nenhuma lista
# ---------------------------------------------------------------------------


def test_sem_wrapper_identificador_cru_desconhecido() -> None:
    gap = _gap(raw_expr="v_qualquer_coisa", line=33)

    origin = classify_gap_origin(gap, [], [], [])

    assert origin.dominio is None
    assert origin.dominio_prova is None
    assert origin.origem == ORIGEM_NAO_DETERMINADO
    assert origin.motivo is not None and origin.motivo.strip() != ""


# ---------------------------------------------------------------------------
# Caso 11: invariante -- NENHUM NAO_DETERMINADO sai com motivo None/vazio
# ---------------------------------------------------------------------------


def _todas_as_origens_do_arquivo() -> List[LacunaOrigin]:
    """Reconstroi as saidas de todos os cenarios exercitados neste arquivo
    (inclusive os que produzem NAO_DETERMINADO por caminhos diferentes) para
    varrer a invariante numa unica lista, sem depender de outros testes
    terem rodado antes."""
    cenarios = []

    cenarios.append(
        classify_gap_origin(
            _gap(raw_expr="p_tabela_nome", line=20),
            [FormalParam("P_TABELA_NOME", "VARCHAR2")],
            [],
            [],
        )
    )
    cenarios.append(
        classify_gap_origin(
            _gap(raw_expr="g_filtro_padrao", line=21),
            [],
            [PackageVarOrigin("G_FILTRO_PADRAO", ["SP_X"])],
            [],
        )
    )
    cenarios.append(
        classify_gap_origin(
            _gap(
                raw_expr="PKG_HELPER.FN_MONTAR_WHERE(p_filtro)",
                line=22,
                via_funcao="PKG_HELPER.FN_MONTAR_WHERE",
            ),
            [],
            [],
            [],
        )
    )
    cenarios.append(
        classify_gap_origin(_gap(raw_expr="v_outro_sql", line=23), [], [], ["V_OUTRO_SQL"])
    )
    cenarios.append(
        classify_gap_origin(_gap(raw_expr="v_completo_desconhecido", line=24), [], [], [])
    )
    cenarios.append(
        classify_gap_origin(
            _gap(
                raw_expr="DBMS_ASSERT.ENQUOTE_NAME(p_tabela_nome)",
                line=46,
                via_funcao="DBMS_ASSERT.ENQUOTE_NAME",
            ),
            [FormalParam("P_TABELA_NOME", "VARCHAR2")],
            [],
            [],
        )
    )
    cenarios.append(
        classify_gap_origin(
            _gap(
                raw_expr="DBMS_ASSERT.ENQUOTE_NAME(p_prefixo || p_sufixo)",
                line=32,
                via_funcao="DBMS_ASSERT.ENQUOTE_NAME",
            ),
            [FormalParam("P_PREFIXO"), FormalParam("P_SUFIXO")],
            [],
            [],
        )
    )
    cenarios.append(
        classify_gap_origin(_gap(raw_expr="v_qualquer_coisa", line=33), [], [], [])
    )
    # wrapper DBMS_ASSERT com funcao nao mapeada na tabela de dominio, mas
    # ainda assim reconhecido como forma DBMS_ASSERT.<FUNC>(<arg>)
    cenarios.append(
        classify_gap_origin(
            _gap(
                raw_expr="DBMS_ASSERT.NOOP_FUTURA(p_desconhecido)",
                line=34,
                via_funcao="DBMS_ASSERT.NOOP_FUTURA",
            ),
            [],
            [],
            [],
        )
    )
    return cenarios


def test_nao_determinado_sempre_tem_motivo() -> None:
    for origin in _todas_as_origens_do_arquivo():
        if origin.origem == ORIGEM_NAO_DETERMINADO:
            assert origin.motivo is not None, "NAO_DETERMINADO sem motivo: {}".format(origin)
            assert origin.motivo.strip() != "", "NAO_DETERMINADO com motivo vazio: {}".format(origin)


# ---------------------------------------------------------------------------
# Caso 12: regressao com o caso real do backlog -- GESTAO_OO.
# PKG_DYNAMIC_EVALUATOR, DBMS_ASSERT.ENQUOTE_NAME(p_tabela_nome) e
# DBMS_ASSERT.ENQUOTE_NAME(p_filtro_tipo), linhas 46 e 49 (texto exato
# citado no backlog secao 4.2/4.4).
# ---------------------------------------------------------------------------


def _pkg_dynamic_evaluator_source() -> List[str]:
    """Fonte sintetica minima ao redor do texto REAL citado no backlog
    (docs/backlog-sql-dinamico-estatico.md secoes 4.2 e 4.4), posicionada
    para que as duas chamadas DBMS_ASSERT.ENQUOTE_NAME caiam exatamente nas
    linhas 46 e 49 -- as mesmas citadas no contrato T-04 para este caso de
    regressao. Linhas de preenchimento (filler) existem so para acertar a
    numeracao; nao sao codigo real do objeto."""
    lines: List[str] = ["PACKAGE BODY PKG_DYNAMIC_EVALUATOR IS"]  # 1
    lines += [""] * 40  # linhas 2-41 (filler)
    lines.append(  # linha 42
        "  PROCEDURE SP_EXTRAIR_METRICAS_TABELA "
        "(p_tabela_nome IN VARCHAR2, p_filtro_tipo IN VARCHAR2) IS"
    )
    lines.append("    v_sql VARCHAR2(4000);")  # 43
    lines.append("    v_count NUMBER; v_avg NUMBER; v_max NUMBER;")  # 44
    lines.append("  BEGIN")  # 45
    lines.append(  # linha 46
        "    v_sql := 'SELECT COUNT(*) FROM ' || "
        "DBMS_ASSERT.ENQUOTE_NAME(p_tabela_nome) || ' x ';"
    )
    lines.append("    IF p_filtro_tipo IS NOT NULL THEN")  # 47
    lines.append("")  # 48 (filler)
    lines.append(  # linha 49
        "      v_sql := v_sql || 'WHERE VALUE(x) IS OF (' || "
        "DBMS_ASSERT.ENQUOTE_NAME(p_filtro_tipo) || ')';"
    )
    lines.append("    END IF;")  # 50
    lines.append("    EXECUTE IMMEDIATE v_sql INTO v_count, v_avg, v_max;")  # 51
    lines.append("  END SP_EXTRAIR_METRICAS_TABELA;")  # 52
    lines.append("END PKG_DYNAMIC_EVALUATOR;")  # 53
    assert len(lines) == 53
    assert lines[45].lstrip().startswith("v_sql := 'SELECT")
    assert lines[48].lstrip().startswith("v_sql := v_sql ||")
    return lines


def _find_gap(gaps, needle: str) -> TemplateGap:
    for gap in gaps:
        if needle.upper() in gap.raw_expr.upper():
            return gap
    raise AssertionError("lacuna contendo {!r} nao encontrada".format(needle))


def test_regressao_pkg_dynamic_evaluator_linhas_46_e_49() -> None:
    source = _pkg_dynamic_evaluator_source()
    template = reconstruct_template(source, exec_line=51, var_name="V_SQL")

    gap_tabela = _find_gap(template.gaps, "p_tabela_nome")
    gap_filtro = _find_gap(template.gaps, "p_filtro_tipo")
    assert gap_tabela.line == 46
    assert gap_filtro.line == 49

    formal_params = [FormalParam("P_TABELA_NOME", "VARCHAR2"), FormalParam("P_FILTRO_TIPO", "VARCHAR2")]

    origin_tabela = classify_gap_origin(gap_tabela, formal_params, [], [])
    origin_filtro = classify_gap_origin(gap_filtro, formal_params, [], [])

    assert origin_tabela.dominio == DOMINIO_NOME_DE_OBJETO
    assert origin_tabela.dominio_prova == "DBMS_ASSERT.ENQUOTE_NAME na linha 46"
    assert origin_tabela.origem == ORIGEM_PARAMETRO_FORMAL
    assert origin_tabela.detalhe["nome"] == "P_TABELA_NOME"

    assert origin_filtro.dominio == DOMINIO_NOME_DE_OBJETO
    assert origin_filtro.dominio_prova == "DBMS_ASSERT.ENQUOTE_NAME na linha 49"
    assert origin_filtro.origem == ORIGEM_PARAMETRO_FORMAL
    assert origin_filtro.detalhe["nome"] == "P_FILTRO_TIPO"
