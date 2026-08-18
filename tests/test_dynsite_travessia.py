"""Testes offline (T-03, contrato dynsql-dossie) para travessia de funcao em
plsqlflow/dynsite_template.py.

Modulo puro sob teste: nenhum destes casos abre conexao, executa o SQL
analisado ou o subprograma que o contem. Cada teste e nomeado pela REGRA do
contrato que ele cobre (docs/backlog-sql-dinamico-estatico.md secao 4.2.1),
na mesma linha de tests/test_dynsite_template.py (T-02).

Regra citada literalmente no backlog (nao reformulada):

    Atravessa se, e somente se, a funcao tem exatamente um RETURN, esse
    RETURN e reconstruivel pelas mesmas regras de 4.2, e nao ha uma segunda
    travessia encadeada. Qualquer condicao que falhe -> para e registra.

Fontes PL/SQL sao construidas inline (listas de string, indice 0 = linha 1,
mesma convencao de dynsql.rows_from_lines / dynsite.classify_dynamic_site /
dynsite_template.reconstruct_template).
"""
from __future__ import annotations

from typing import List

from plsqlflow.dynsite_template import (
    RECONSTRUCAO_COMPLETA,
    RECONSTRUCAO_PARCIAL,
    TemplateGap,
    reconstruct_template,
    resolve_gaps,
    traverse_function_gap,
)


def _gap(ref: str, raw_expr: str, via_funcao: str, line: int = 1) -> TemplateGap:
    return TemplateGap(
        ref=ref, raw_expr=raw_expr, line=line, via_funcao=via_funcao, em_loop=False
    )


def test_return_unico_100_por_cento_literal_atravessa_e_fica_completo() -> None:
    gap = _gap("L0", "pkg_a.fn_montar_fixo()", "pkg_a.fn_montar_fixo")
    funcao_source: List[str] = [
        "FUNCTION fn_montar_fixo RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                        # 2
        "  RETURN 'AND ativo = 1';",                     # 3
        "END fn_montar_fixo;",                           # 4
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is True
    assert result.motivo_nao_atravessado is None
    assert result.gaps == []
    assert len(result.parts) == 1
    assert result.parts[0].kind == "literal"
    assert result.parts[0].text == "AND ativo = 1"

    # regressao integrada: um template pai cuja UNICA lacuna e essa funcao
    # fica COMPLETO depois de resolve_gaps (nao ha mais via_funcao pendente).
    parent_source = [
        "PROCEDURE p1 IS",                                     # 1
        "  v_sql VARCHAR2(200);",                               # 2
        "BEGIN",                                                 # 3
        "  v_sql := 'SELECT * FROM t WHERE ' || pkg_a.fn_montar_fixo();",  # 4
        "  EXECUTE IMMEDIATE v_sql;",                             # 5
        "END p1;",                                                # 6
    ]
    template = reconstruct_template(parent_source, exec_line=5, var_name="v_sql")
    assert template.reconstrucao == RECONSTRUCAO_PARCIAL  # T-02 sozinho: para

    resolved = resolve_gaps(template, {"pkg_a.fn_montar_fixo": funcao_source})
    assert resolved.gaps == []
    assert [p.kind for p in resolved.parts] == ["literal", "literal"]
    assert resolved.parts[0].text == "SELECT * FROM t WHERE "
    assert resolved.parts[1].text == "AND ativo = 1"
    assert resolved.reconstrucao == RECONSTRUCAO_COMPLETA
    assert resolved.reconstrucao_motivo is None


def test_return_unico_com_lacuna_simples_atravessa_e_lacuna_nova_sem_via_funcao() -> None:
    gap = _gap("L0", "pkg_a.fn_identidade(p_valor)", "pkg_a.fn_identidade")
    funcao_source: List[str] = [
        "FUNCTION fn_identidade (p_valor IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                             # 2
        "  RETURN p_valor;",                                                 # 3
        "END fn_identidade;",                                                # 4
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is True
    assert result.motivo_nao_atravessado is None
    assert len(result.parts) == 1
    assert result.parts[0].kind == "lacuna"
    assert len(result.gaps) == 1
    new_gap = result.gaps[0]
    assert new_gap.raw_expr == "p_valor"
    assert new_gap.via_funcao is None
    assert new_gap.ref == result.parts[0].ref


def test_return_unico_encadeado_atravessa_so_1_nivel_e_nao_usa_segunda_fonte() -> None:
    gap = _gap("L0", "pkg_a.fn_delega(p_filtro)", "pkg_a.fn_delega")
    funcao_delega_source: List[str] = [
        "FUNCTION fn_delega (p_filtro IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                          # 2
        "  RETURN pkg_b.fn_outra(p_filtro);",                             # 3
        "END fn_delega;",                                                 # 4
    ]
    funcao_outra_source: List[str] = [
        "FUNCTION fn_outra (p_x IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                    # 2
        "  RETURN 'NAO DEVERIA APARECER: ' || p_x;",                # 3
        "END fn_outra;",                                             # 4
    ]

    result = traverse_function_gap(gap, funcao_delega_source)

    assert result.traversed is True
    assert len(result.gaps) == 1
    chained = result.gaps[0]
    assert chained.via_funcao == "pkg_b.fn_outra"
    assert chained.raw_expr == "pkg_b.fn_outra(p_filtro)"
    # prova que NAO atravessou a segunda camada: o texto do RETURN de
    # fn_outra nunca aparece em nenhuma part produzida por este nivel.
    for part in result.parts:
        if part.text:
            assert "NAO DEVERIA APARECER" not in part.text

    # mesmo passando a fonte da SEGUNDA funcao para resolve_gaps, ela nao e
    # usada -- travessia se limita a 1 nivel a partir do gap original.
    parent_source = [
        "PROCEDURE p3 IS",                                        # 1
        "  v_sql VARCHAR2(200);",                                  # 2
        "BEGIN",                                                    # 3
        "  v_sql := 'SELECT ' || pkg_a.fn_delega(p_filtro);",       # 4
        "  EXECUTE IMMEDIATE v_sql;",                                # 5
        "END p3;",                                                   # 6
    ]
    template = reconstruct_template(parent_source, exec_line=5, var_name="v_sql")
    resolved = resolve_gaps(
        template,
        {
            "pkg_a.fn_delega": funcao_delega_source,
            "pkg_b.fn_outra": funcao_outra_source,
        },
    )
    for part in resolved.parts:
        if part.text:
            assert "NAO DEVERIA APARECER" not in part.text
    remaining_via_funcao = [g.via_funcao for g in resolved.gaps if g.via_funcao]
    assert remaining_via_funcao == ["pkg_b.fn_outra"]
    # a segunda camada continua pendente -- template segue parcial.
    assert resolved.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "pkg_b.fn_outra" in resolved.reconstrucao_motivo


def test_funcao_com_2_returns_distintos_nao_atravessa() -> None:
    gap = _gap("L0", "pkg_a.fn_dois(p_flag)", "pkg_a.fn_dois")
    funcao_source: List[str] = [
        "FUNCTION fn_dois (p_flag IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                       # 2
        "  IF p_flag = 'S' THEN",                                      # 3
        "    RETURN 'AAAA';",                                          # 4
        "  ELSE",                                                       # 5
        "    RETURN 'ZZZZ';",                                          # 6
        "  END IF;",                                                    # 7
        "END fn_dois;",                                                 # 8
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is False
    assert result.parts is None
    assert result.gaps is None
    assert result.motivo_nao_atravessado == "2 RETURN distintos"


def test_funcao_com_3_returns_distintos_nao_atravessa_nao_e_so_limite_de_2() -> None:
    gap = _gap("L0", "pkg_a.fn_tres(p_flag)", "pkg_a.fn_tres")
    funcao_source: List[str] = [
        "FUNCTION fn_tres (p_flag IN NUMBER) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                    # 2
        "  IF p_flag = 1 THEN",                                     # 3
        "    RETURN 'UM';",                                         # 4
        "  ELSIF p_flag = 2 THEN",                                  # 5
        "    RETURN 'DOIS';",                                       # 6
        "  ELSE",                                                    # 7
        "    RETURN 'TRES';",                                       # 8
        "  END IF;",                                                 # 9
        "END fn_tres;",                                              # 10
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is False
    assert result.parts is None
    assert result.gaps is None
    assert result.motivo_nao_atravessado == "3 RETURN distintos"


def test_funcao_sem_nenhum_return_nao_atravessa() -> None:
    gap = _gap("L0", "pkg_a.fn_sem_return()", "pkg_a.fn_sem_return")
    funcao_source: List[str] = [
        "FUNCTION fn_sem_return RETURN VARCHAR2 IS",  # 1
        "  v_resultado VARCHAR2(10);",                 # 2
        "BEGIN",                                        # 3
        "  v_resultado := 'x';",                        # 4
        "  NULL;",                                       # 5
        "END fn_sem_return;",                            # 6
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is False
    assert result.parts is None
    assert result.gaps is None
    assert result.motivo_nao_atravessado == "funcao sem RETURN encontrado"


def test_funcao_ausente_de_function_sources_nao_atravessa_via_resolve_gaps() -> None:
    parent_source = [
        "PROCEDURE p7 IS",                                            # 1
        "  v_sql VARCHAR2(200);",                                      # 2
        "BEGIN",                                                        # 3
        "  v_sql := 'SELECT ' || pkg_a.fn_desconhecida(p_x);",          # 4
        "  EXECUTE IMMEDIATE v_sql;",                                    # 5
        "END p7;",                                                       # 6
    ]
    template = reconstruct_template(parent_source, exec_line=5, var_name="v_sql")
    assert template.gaps[0].via_funcao == "pkg_a.fn_desconhecida"

    # function_sources NAO contem a chave "pkg_a.fn_desconhecida" -- fonte
    # indisponivel, nunca atravessada (a distincao do resto do dicionario
    # prova que a ausencia especifica desta chave e o que importa).
    resolved = resolve_gaps(template, {"outra.funcao": ["FUNCTION x IS BEGIN NULL; END;"]})

    assert resolved.reconstrucao == RECONSTRUCAO_PARCIAL
    assert [g.via_funcao for g in resolved.gaps] == ["pkg_a.fn_desconhecida"]
    assert "codigo-fonte da funcao nao disponivel" in resolved.reconstrucao_motivo
    assert "pkg_a.fn_desconhecida" in resolved.reconstrucao_motivo


def test_caso_backlog_fn_montar_where_atravessa_com_uma_lacuna_nova() -> None:
    """Caso citado no backlog (docs/backlog-sql-dinamico-estatico.md secao
    4.2.1): fn_montar_where(p_filtro) com RETURN unico
    'AND (' || p_filtro || ')' -- atravessa, resultado tem 1 nova lacuna
    (p_filtro)."""
    gap = _gap("L0", "pkg_a.fn_montar_where(p_filtro)", "pkg_a.fn_montar_where")
    funcao_source: List[str] = [
        "FUNCTION fn_montar_where (p_filtro IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                                # 2
        "  RETURN 'AND (' || p_filtro || ')';",                                 # 3
        "END fn_montar_where;",                                                 # 4
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is True
    assert result.motivo_nao_atravessado is None
    assert [p.kind for p in result.parts] == ["literal", "lacuna", "literal"]
    assert result.parts[0].text == "AND ("
    assert result.parts[2].text == ")"
    assert len(result.gaps) == 1
    assert result.gaps[0].raw_expr == "p_filtro"
    assert result.gaps[0].via_funcao is None


def test_regressao_reconstruct_template_mais_resolve_gaps_motivo_final_cita_so_a_nao_atravessada() -> None:
    gap_ok_source: List[str] = [
        "FUNCTION fn_um_retorno (p_id IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                          # 2
        "  RETURN 'id=' || p_id;",                                        # 3
        "END fn_um_retorno;",                                             # 4
    ]
    gap_falha_source: List[str] = [
        "FUNCTION fn_multi (p_extra IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                        # 2
        "  IF p_extra IS NOT NULL THEN",                                # 3
        "    RETURN 'AND extra = 1';",                                  # 4
        "  END IF;",                                                    # 5
        "  RETURN 'AND extra = 0';",                                    # 6
        "END fn_multi;",                                                # 7
    ]
    parent_source = [
        "PROCEDURE p9 (p_id IN NUMBER, p_extra IN VARCHAR2) IS",         # 1
        "  v_sql VARCHAR2(400);",                                         # 2
        "BEGIN",                                                           # 3
        "  v_sql := 'SELECT * FROM t WHERE id = ' || pkg_a.fn_um_retorno(p_id) "
        "|| ' ' || pkg_b.fn_multi(p_extra);",                             # 4
        "  EXECUTE IMMEDIATE v_sql;",                                      # 5
        "END p9;",                                                         # 6
    ]
    template = reconstruct_template(parent_source, exec_line=5, var_name="v_sql")
    assert len(template.gaps) == 2
    assert template.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "pkg_a.fn_um_retorno" in template.reconstrucao_motivo
    assert "pkg_b.fn_multi" in template.reconstrucao_motivo

    resolved = resolve_gaps(
        template,
        {"pkg_a.fn_um_retorno": gap_ok_source, "pkg_b.fn_multi": gap_falha_source},
    )

    assert resolved.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "pkg_a.fn_um_retorno" not in resolved.reconstrucao_motivo
    assert "pkg_b.fn_multi" in resolved.reconstrucao_motivo
    assert "2 RETURN distintos" in resolved.reconstrucao_motivo
    # a lacuna atravessada some da lista final de gaps; a que falhou permanece.
    assert not any(g.raw_expr == "pkg_a.fn_um_retorno(p_id)" for g in resolved.gaps)
    assert any(g.raw_expr == "pkg_b.fn_multi(p_extra)" for g in resolved.gaps)


def test_return_com_is_as_interno_nao_e_confundido_com_assinatura_e_nao_atravessa() -> None:
    """Reproducao exata do achado da revisao cega (T-03, defeito
    BLOQUEANTE): funcao com 2 RETURNs distintos, um deles contendo `IS`/`AS`
    dentro da propria expressao (`RETURN (SELECT column_name AS x FROM
    dual);`). A heuristica antiga confundia o `AS` interno com o `IS/AS` da
    assinatura -- se o `;` do OUTRO RETURN aparecesse depois, o RETURN
    genuino era descartado da contagem, e a funcao era erroneamente vista
    como tendo 1 RETURN so, atravessando quando deveria parar."""
    gap = _gap("L0", "pkg_a.fn_dois(p_flag)", "pkg_a.fn_dois")
    funcao_source: List[str] = [
        "FUNCTION fn_dois (p_flag IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                      # 2
        "  IF p_flag = 'S' THEN",                                     # 3
        "    RETURN (SELECT column_name AS x FROM dual);",            # 4
        "  ELSE",                                                      # 5
        "    RETURN 'ZZZZ';",                                          # 6
        "  END IF;",                                                   # 7
        "END fn_dois;",                                                # 8
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is False
    assert result.parts is None
    assert result.gaps is None
    assert result.motivo_nao_atravessado == "2 RETURN distintos"


def test_return_unico_com_is_not_null_interno_atravessa_normalmente() -> None:
    """Prova que a correcao nao super-corrige: um UNICO RETURN legitimo cuja
    expressao contem `IS` (`IS NOT NULL`) nao pode ser rejeitado so por
    causa disso -- a funcao tem exatamente 1 RETURN de verdade e a travessia
    deve acontecer normalmente."""
    gap = _gap("L0", "pkg_a.fn_checa(p_x)", "pkg_a.fn_checa")
    funcao_source: List[str] = [
        "FUNCTION fn_checa (p_x IN VARCHAR2) RETURN BOOLEAN IS",  # 1
        "BEGIN",                                                    # 2
        "  RETURN p_x IS NOT NULL;",                                # 3
        "END fn_checa;",                                             # 4
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is True
    assert result.motivo_nao_atravessado is None
    assert result.gaps is not None
    assert len(result.parts) >= 1


def test_return_unico_com_is_of_interno_atravessa_normalmente() -> None:
    """Mesma prova do teste anterior, variante sintatica diferente (`IS OF`,
    checagem de tipo de object type) -- unico RETURN da funcao, sem
    ambiguidade, deve atravessar."""
    gap = _gap("L0", "pkg_a.fn_tipo(p_v)", "pkg_a.fn_tipo")
    funcao_source: List[str] = [
        "FUNCTION fn_tipo (p_v IN ANYDATA) RETURN BOOLEAN IS",  # 1
        "BEGIN",                                                  # 2
        "  RETURN p_v IS OF (VARCHAR2);",                         # 3
        "END fn_tipo;",                                            # 4
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is True
    assert result.motivo_nao_atravessado is None
    assert result.gaps is not None
    assert len(result.parts) >= 1


def test_assinatura_com_parametros_nao_e_contada_como_return_do_corpo() -> None:
    """Confirma que o caso ORIGINAL (ja coberto indiretamente por outros
    testes deste arquivo) continua correto apos a correcao: assinatura
    `FUNCTION f(...) RETURN <tipo> IS` seguida de UM `RETURN <expr>;` no
    corpo continua contada como exatamente 1 RETURN -- a correcao (span
    estrutural do cabecalho) nao pode voltar a contar a assinatura junto."""
    gap = _gap("L0", "pkg_a.fn_um(p_id)", "pkg_a.fn_um")
    funcao_source: List[str] = [
        "FUNCTION fn_um (p_id IN NUMBER, p_flag IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "  v_out VARCHAR2(100);",                                                   # 2
        "BEGIN",                                                                     # 3
        "  RETURN 'id=' || p_id;",                                                   # 4
        "END fn_um;",                                                                # 5
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is True
    assert result.motivo_nao_atravessado is None
    assert result.gaps is not None


def test_nunca_escolhe_entre_multiplos_return_nenhum_texto_vaza() -> None:
    """Teste negativo explicito: prova que nao existe logica de 'escolher
    entre RETURNs'. Os dois RETURNs tem textos completamente diferentes
    ('PRIMEIRO_TEXTO_NUNCA_USAR' e 'SEGUNDO_TEXTO_NUNCA_USAR') -- nenhum dos
    dois pode aparecer em lugar nenhum do resultado, provando que a funcao
    nao "usa o primeiro" nem "usa o ultimo": ela simplesmente para."""
    gap = _gap("L0", "pkg_a.fn_ambigua(p_x)", "pkg_a.fn_ambigua")
    funcao_source: List[str] = [
        "FUNCTION fn_ambigua (p_x IN VARCHAR2) RETURN VARCHAR2 IS",  # 1
        "BEGIN",                                                       # 2
        "  IF p_x = 'A' THEN",                                         # 3
        "    RETURN 'PRIMEIRO_TEXTO_NUNCA_USAR';",                     # 4
        "  END IF;",                                                    # 5
        "  RETURN 'SEGUNDO_TEXTO_NUNCA_USAR';",                        # 6
        "END fn_ambigua;",                                              # 7
    ]

    result = traverse_function_gap(gap, funcao_source)

    assert result.traversed is False
    assert result.parts is None
    assert result.gaps is None
    assert "PRIMEIRO_TEXTO_NUNCA_USAR" not in (result.motivo_nao_atravessado or "")
    assert "SEGUNDO_TEXTO_NUNCA_USAR" not in (result.motivo_nao_atravessado or "")
    assert result.motivo_nao_atravessado == "2 RETURN distintos"

    # via resolve_gaps tambem: a lacuna original fica intacta (raw_expr
    # preservado), nenhum literal dos RETURNs vaza para o template pai.
    parent_source = [
        "PROCEDURE p10 IS",                                       # 1
        "  v_sql VARCHAR2(200);",                                  # 2
        "BEGIN",                                                    # 3
        "  v_sql := 'SELECT ' || pkg_a.fn_ambigua(p_x);",           # 4
        "  EXECUTE IMMEDIATE v_sql;",                                # 5
        "END p10;",                                                  # 6
    ]
    template = reconstruct_template(parent_source, exec_line=5, var_name="v_sql")
    resolved = resolve_gaps(template, {"pkg_a.fn_ambigua": funcao_source})
    for part in resolved.parts:
        if part.text:
            assert "NUNCA_USAR" not in part.text
    assert resolved.gaps[0].raw_expr == "pkg_a.fn_ambigua(p_x)"
    assert resolved.gaps[0].via_funcao == "pkg_a.fn_ambigua"
