"""Testes de `plsqlflow.dynsite_render` (T-06, contrato dynsql-dossie).

T-06 e a MONTAGEM FINAL: junta T-01 (`dynsite.DynSiteForm`), T-02+T-03
(`dynsite_template.ReconstructedTemplate`) e T-04+T-05
(`dynsite_origin.LacunaOrigin`) num `DynSiteRecord` por sitio, e sabe
renderizar uma lista desses registros em `dynamic_sql.jsonl` (canonico) e
`SQL-DINAMICO.md` (legivel, DERIVADO do canonico -- nunca dado proprio).

Cobre os 11 casos obrigatorios do Plans.md/[T-06]. A maioria constroi
`DynSiteForm`/`ReconstructedTemplate`/`LacunaOrigin` diretamente (dataclasses
publicos dos modulos anteriores) para isolar o que T-06 faz; o caso 10 e o
UNICO que roda `classify_dynamic_site` + `reconstruct_template` +
`classify_gap_origin` de verdade, ponta a ponta, sem mock nenhum -- prova de
integracao real entre os cinco modulos do contrato.
"""
from __future__ import annotations

import json
from typing import List

from plsqlflow.dynsite import (
    CATEGORIA_QUERY_LINHA_UNICA,
    DynSiteForm,
    EXEC_KIND_EXECUTE_IMMEDIATE,
    classify_dynamic_site,
)
from plsqlflow.dynsite_origin import (
    CHAMADORES_FORA_FONTE,
    DOMINIO_NOME_DE_OBJETO,
    FormalParam,
    LacunaOrigin,
    ORIGEM_PARAMETRO_FORMAL,
    classify_gap_origin,
)
from plsqlflow.dynsite_render import (
    DynSiteRecord,
    build_record,
    render_jsonl,
    render_markdown,
)
from plsqlflow.dynsite_template import (
    RECONSTRUCAO_COMPLETA,
    RECONSTRUCAO_PARCIAL,
    ReconstructedTemplate,
    TemplateGap,
    TemplatePart,
    reconstruct_template,
)


# ===========================================================================
# helpers de fixture -- constroem DynSiteForm/ReconstructedTemplate/
# LacunaOrigin sinteticos, sem passar pelos modulos anteriores (exceto onde
# o proprio teste diz o contrario).
# ===========================================================================


def _form(
    line: int = 54,
    categoria_provada: str = CATEGORIA_QUERY_LINHA_UNICA,
    categoria_prova: str = "clausula INTO de EXECUTE IMMEDIATE sem BULK COLLECT so aceita query de linha unica",
    pode_invocar_procedure: bool = False,
    into_arity: int = 3,
    em_loop: bool = False,
) -> DynSiteForm:
    return DynSiteForm(
        line=line,
        exec_kind=EXEC_KIND_EXECUTE_IMMEDIATE,
        categoria_provada=categoria_provada,
        categoria_prova=categoria_prova,
        pode_invocar_procedure=pode_invocar_procedure,
        pode_invocar_procedure_determinavel=True,
        into_arity=into_arity,
        em_loop=em_loop,
    )


def _template_literal_then_lacuna(
    literal_text: str = "SELECT COUNT(*), NVL(AVG(t.valor), 0), NVL(MAX(t.peso), 0) FROM ",
    reconstrucao: str = RECONSTRUCAO_PARCIAL,
    reconstrucao_motivo: str = "lacuna L0 vem de coluna de tabela",
) -> ReconstructedTemplate:
    parts = [
        TemplatePart(kind="literal", text=literal_text, ref=None, line=43, condicional=None),
        TemplatePart(kind="lacuna", text=None, ref="L0", line=46, condicional=None),
    ]
    gaps = [TemplateGap(ref="L0", raw_expr="P_TABELA", line=46, via_funcao=None, em_loop=False)]
    return ReconstructedTemplate(
        variavel="V_SQL", parts=parts, gaps=gaps,
        reconstrucao=reconstrucao, reconstrucao_motivo=reconstrucao_motivo,
    )


def _origin_parametro_formal(
    ref: str = "L0", nome: str = "P_TABELA", tipo: str = "VARCHAR2",
    dominio: str = DOMINIO_NOME_DE_OBJETO,
    dominio_prova: str = "DBMS_ASSERT.ENQUOTE_NAME na linha 46",
    com_call_sites: bool = True,
) -> LacunaOrigin:
    detalhe = {"nome": nome, "tipo": tipo, "posicao": 1}
    if com_call_sites:
        detalhe.update(
            {
                "call_sites_no_fechamento": ["APP.PKG_BATCH.SP_RODAR#118"],
                "chamadores_fora_do_fechamento": True,
                "chamadores_fora_fonte": CHAMADORES_FORA_FONTE,
            }
        )
    return LacunaOrigin(
        ref=ref, origem=ORIGEM_PARAMETRO_FORMAL, motivo=None,
        dominio=dominio, dominio_prova=dominio_prova, detalhe=detalhe,
    )


_SOURCE_LINES = [
    "PROCEDURE SP_TOTALIZAR (p_tabela IN VARCHAR2) IS",
    "  v_sql VARCHAR2(4000);",
    "BEGIN",
    "  v_sql := 'SELECT COUNT(*), NVL(AVG(t.valor), 0), NVL(MAX(t.peso), 0) FROM ' || p_tabela;",
    "  EXECUTE IMMEDIATE v_sql INTO v_a, v_b, v_c;",
    "END SP_TOTALIZAR;",
]


# ===========================================================================
# Caso 1: build_record monta registro completo, todos os campos conferidos.
# ===========================================================================


def test_build_record_monta_registro_completo() -> None:
    form = _form()
    template = _template_literal_then_lacuna()
    origins = {"L0": _origin_parametro_formal()}

    record = build_record(
        "APP", "PKG_RELATORIO", "PACKAGE BODY", "SP_TOTALIZAR", _SOURCE_LINES, form, template, origins
    )

    assert isinstance(record, DynSiteRecord)
    assert record.site_id == "APP.PKG_RELATORIO.SP_TOTALIZAR#54"
    assert record.owner == "APP"
    assert record.object_name == "PKG_RELATORIO"
    assert record.object_type == "PACKAGE BODY"
    assert record.subprogram == "SP_TOTALIZAR"
    assert record.line == 54
    assert record.exec_form == EXEC_KIND_EXECUTE_IMMEDIATE
    assert record.categoria_provada == CATEGORIA_QUERY_LINHA_UNICA
    assert record.categoria_prova == form.categoria_prova
    assert record.pode_invocar_procedure is False
    assert record.into_arity == 3
    assert record.em_loop is False
    assert record.variavel_montada == "V_SQL"
    assert record.reconstrucao == RECONSTRUCAO_PARCIAL
    assert record.reconstrucao_motivo == "lacuna L0 vem de coluna de tabela"

    # template serializado -- nomes em portugues, literal sem "ref", lacuna
    # sem "texto".
    assert record.template == [
        {
            "tipo": "literal",
            "texto": "SELECT COUNT(*), NVL(AVG(t.valor), 0), NVL(MAX(t.peso), 0) FROM ",
            "linha": 43,
        },
        {"tipo": "lacuna", "ref": "L0", "linha": 46},
    ]

    # lacunas -- todos os campos do contrato, valores corretos.
    assert len(record.lacunas) == 1
    lacuna = record.lacunas[0]
    assert lacuna["ref"] == "L0"
    assert lacuna["nome"] == "P_TABELA"  # == raw_expr do TemplateGap
    assert lacuna["origem"] == ORIGEM_PARAMETRO_FORMAL
    assert lacuna["tipo"] == "VARCHAR2"
    assert lacuna["dominio"] == DOMINIO_NOME_DE_OBJETO
    assert lacuna["dominio_prova"] == "DBMS_ASSERT.ENQUOTE_NAME na linha 46"
    assert lacuna["call_sites_no_fechamento"] == ["APP.PKG_BATCH.SP_RODAR#118"]
    assert lacuna["chamadores_fora_do_fechamento"] is True
    assert lacuna["chamadores_fora_fonte"] == CHAMADORES_FORA_FONTE
    assert lacuna["via_funcao"] is None
    assert lacuna["em_loop"] is False
    assert lacuna["motivo"] is None

    assert record.chave_correlacao == (
        "SELECT COUNT(*), NVL(AVG(t.valor), 0), NVL(MAX(t.peso), 0) FROM %"
    )
    assert record.source_fingerprint.startswith("sha256:")
    assert record.sanitizacao_ausente == []


# ===========================================================================
# Caso 2: chave_correlacao correta quando o template comeca com literal.
# ===========================================================================


def test_chave_correlacao_prefixo_literal_do_inicio() -> None:
    form = _form()
    template = _template_literal_then_lacuna(literal_text="SELECT 1 FROM ")
    origins = {"L0": _origin_parametro_formal()}

    record = build_record("APP", "PKG_X", "PACKAGE BODY", "SP_Y", _SOURCE_LINES, form, template, origins)

    assert record.chave_correlacao == "SELECT 1 FROM %"


# ===========================================================================
# Caso 3: chave_correlacao e None quando o template comeca com lacuna.
# ===========================================================================


def test_chave_correlacao_none_quando_template_comeca_em_lacuna() -> None:
    form = _form()
    parts = [TemplatePart(kind="lacuna", text=None, ref="L0", line=10, condicional=None)]
    gaps = [TemplateGap(ref="L0", raw_expr="V_QUALQUER", line=10, via_funcao=None, em_loop=False)]
    template = ReconstructedTemplate(
        variavel="V_SQL", parts=parts, gaps=gaps,
        reconstrucao=RECONSTRUCAO_PARCIAL, reconstrucao_motivo="lacuna sem origem provada",
    )
    origins = {"L0": _origin_parametro_formal(com_call_sites=False)}

    record = build_record("APP", "PKG_X", "PACKAGE BODY", "SP_Y", _SOURCE_LINES, form, template, origins)

    assert record.chave_correlacao is None


def test_chave_correlacao_none_quando_unico_literal_inicial_e_vazio() -> None:
    """Nunca inventa prefixo generico: literal inicial vazio produziria
    `"%"` sozinho (casaria com qualquer SQL) -- o contrato proibe isso."""
    form = _form()
    parts = [
        TemplatePart(kind="literal", text="", ref=None, line=10, condicional=None),
        TemplatePart(kind="lacuna", text=None, ref="L0", line=10, condicional=None),
    ]
    gaps = [TemplateGap(ref="L0", raw_expr="V_QUALQUER", line=10, via_funcao=None, em_loop=False)]
    template = ReconstructedTemplate(
        variavel="V_SQL", parts=parts, gaps=gaps,
        reconstrucao=RECONSTRUCAO_COMPLETA, reconstrucao_motivo=None,
    )
    origins = {"L0": _origin_parametro_formal(com_call_sites=False)}

    record = build_record("APP", "PKG_X", "PACKAGE BODY", "SP_Y", _SOURCE_LINES, form, template, origins)

    assert record.chave_correlacao is None


# ===========================================================================
# Caso 4: source_fingerprint deterministico e sensivel a mudanca de linha.
# ===========================================================================


def test_source_fingerprint_deterministico_e_muda_com_o_fonte() -> None:
    form = _form()
    template = _template_literal_then_lacuna()
    origins = {"L0": _origin_parametro_formal()}

    record_1 = build_record(
        "APP", "PKG_X", "PACKAGE BODY", "SP_Y", _SOURCE_LINES, form, template, origins
    )
    record_2 = build_record(
        "APP", "PKG_X", "PACKAGE BODY", "SP_Y", _SOURCE_LINES, form, template, origins
    )
    assert record_1.source_fingerprint == record_2.source_fingerprint

    changed_source = list(_SOURCE_LINES)
    changed_source[1] = "  v_sql VARCHAR2(8000);  -- linha alterada"
    record_3 = build_record(
        "APP", "PKG_X", "PACKAGE BODY", "SP_Y", changed_source, form, template, origins
    )
    assert record_3.source_fingerprint != record_1.source_fingerprint


# ===========================================================================
# Caso 5: render_jsonl com lista vazia -> string vazia, sem erro, sem None.
# ===========================================================================


def test_render_jsonl_lista_vazia_string_vazia_sem_erro() -> None:
    result = render_jsonl([])
    assert result == ""
    assert result is not None
    assert isinstance(result, str)


# ===========================================================================
# Caso 6: render_jsonl com registros -> uma linha JSON valida por registro.
# ===========================================================================


def test_render_jsonl_uma_linha_json_valida_por_registro() -> None:
    form = _form()
    template = _template_literal_then_lacuna()
    origins = {"L0": _origin_parametro_formal()}
    record_a = build_record("APP", "PKG_A", "PACKAGE BODY", "SP_A", _SOURCE_LINES, form, template, origins)
    record_b = build_record(
        "APP", "PKG_B", "PACKAGE BODY", "SP_B", _SOURCE_LINES, _form(line=99), template, origins
    )

    text = render_jsonl([record_a, record_b])
    lines = text.splitlines()

    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["site_id"] == "APP.PKG_A.SP_A#54"
    assert parsed[1]["site_id"] == "APP.PKG_B.SP_B#99"
    # ordem de chaves do exemplo do backlog (secao 5) -- primeiras chaves.
    assert list(parsed[0].keys())[:6] == [
        "site_id", "owner", "object_name", "object_type", "subprogram", "line",
    ]
    assert text.endswith("\n")


# ===========================================================================
# Caso 7: render_markdown com lista vazia -> aviso explicito, nunca mudo.
# ===========================================================================


def test_render_markdown_lista_vazia_aviso_explicito() -> None:
    text = render_markdown([])
    assert "Nenhum sitio de SQL dinamico neste fechamento." in text
    assert text.strip() != ""


# ===========================================================================
# Caso 8: render_markdown ordena por pode_invocar_procedure desc, depois
# reconstrucao (parcial primeiro) -- 4 registros embaralhados na entrada.
# ===========================================================================


def _record_for_order(site_suffix: str, pode_invocar_procedure: bool, reconstrucao: str) -> DynSiteRecord:
    form = _form(pode_invocar_procedure=pode_invocar_procedure)
    template = _template_literal_then_lacuna(reconstrucao=reconstrucao, reconstrucao_motivo="motivo x")
    origins = {"L0": _origin_parametro_formal(com_call_sites=False)}
    return build_record(
        "APP", "PKG_{}".format(site_suffix), "PACKAGE BODY", "SP_{}".format(site_suffix),
        _SOURCE_LINES, form, template, origins,
    )


def test_render_markdown_ordena_por_procedure_desc_depois_parcial_primeiro() -> None:
    rec_false_completa = _record_for_order("A", False, RECONSTRUCAO_COMPLETA)
    rec_true_completa = _record_for_order("B", True, RECONSTRUCAO_COMPLETA)
    rec_true_parcial = _record_for_order("C", True, RECONSTRUCAO_PARCIAL)
    rec_false_parcial = _record_for_order("D", False, RECONSTRUCAO_PARCIAL)

    # entrada embaralhada, de proposito, fora da ordem esperada de saida.
    text = render_markdown([rec_false_completa, rec_true_completa, rec_true_parcial, rec_false_parcial])

    expected_order = [
        rec_true_parcial.site_id,    # True + parcial
        rec_true_completa.site_id,   # True + completa
        rec_false_parcial.site_id,   # False + parcial
        rec_false_completa.site_id,  # False + completa
    ]
    positions = [text.index("## {}".format(site_id)) for site_id in expected_order]
    assert positions == sorted(positions), "ordem de saida nao bate com o esperado: {}".format(positions)


# ===========================================================================
# Caso 9: reconciliacao -- todo site_id do jsonl aparece no md, nenhuma
# categoria/contagem de lacuna diverge.
# ===========================================================================


def test_reconciliacao_todo_site_id_do_jsonl_aparece_no_md_sem_divergencia() -> None:
    rec_1 = _record_for_order("A", True, RECONSTRUCAO_PARCIAL)
    rec_2 = _record_for_order("B", False, RECONSTRUCAO_COMPLETA)
    records = [rec_1, rec_2]

    jsonl_text = render_jsonl(records)
    md_text = render_markdown(records)

    parsed_jsonl = [json.loads(line) for line in jsonl_text.splitlines()]
    assert len(parsed_jsonl) == len(records)

    for payload in parsed_jsonl:
        site_id = payload["site_id"]
        assert "## {}".format(site_id) in md_text, "site_id {} ausente do md".format(site_id)

        # a categoria provada citada no md tem que bater com a do jsonl --
        # nunca diverge porque o md e derivado do mesmo DynSiteRecord.
        assert payload["categoria_provada"] in md_text

        # a contagem de lacunas do md ("### Lacunas (N)") bate com len(lacunas)
        # do registro canonico.
        expected_marker = "### Lacunas ({})".format(len(payload["lacunas"]))
        assert expected_marker in md_text


# ===========================================================================
# Caso 10: integracao real, ponta a ponta -- sitio L54 do
# GESTAO_OO.PKG_DYNAMIC_EVALUATOR, sem mock, usando classify_dynamic_site +
# reconstruct_template + classify_gap_origin de verdade.
# ===========================================================================


def _gestao_oo_source() -> List[str]:
    """Mesma reconstrucao sintetica em volta do texto REAL citado no
    backlog (docs/backlog-sql-dinamico-estatico.md secao 4.2, L43-54 de
    GESTAO_OO.PKG_DYNAMIC_EVALUATOR.SP_EXTRAIR_METRICAS_TABELA) usada em
    tests/test_dynsite_template.py -- duplicada aqui de proposito (mesmo
    padrao de tests/test_dynsite_origem.py, que tambem constroi sua propria
    copia): cada modulo de teste de dynsql-dossie e independente, nenhum
    importa fixture de outro."""
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
    assert lines[53].lstrip().startswith("EXECUTE IMMEDIATE")
    return lines


def test_integracao_real_gestao_oo_l54_ponta_a_ponta_sem_mock() -> None:
    source = _gestao_oo_source()

    # T-01 de verdade.
    form = classify_dynamic_site(source, line=54)
    # T-02 (+T-03 nao entra aqui -- gaps ficam via_funcao, DBMS_ASSERT nao
    # e uma funcao "helper" que faz sentido atravessar, e o proprio T-02 ja
    # marca a reconstrucao como parcial por isso).
    template = reconstruct_template(source, exec_line=54, var_name="v_sql")
    # T-04 de verdade.
    formal_params = [FormalParam("P_TABELA_NOME", "VARCHAR2"), FormalParam("P_FILTRO_TIPO", "VARCHAR2")]
    origins = {gap.ref: classify_gap_origin(gap, formal_params, [], []) for gap in template.gaps}

    # T-06: montagem final.
    record = build_record(
        "GESTAO_OO", "PKG_DYNAMIC_EVALUATOR", "PACKAGE BODY", "SP_EXTRAIR_METRICAS_TABELA",
        source, form, template, origins,
    )

    assert record.site_id == "GESTAO_OO.PKG_DYNAMIC_EVALUATOR.SP_EXTRAIR_METRICAS_TABELA#54"
    assert record.categoria_provada == CATEGORIA_QUERY_LINHA_UNICA
    assert record.pode_invocar_procedure is False
    assert record.into_arity == 3
    assert record.reconstrucao == RECONSTRUCAO_PARCIAL
    assert "funcao" in record.reconstrucao_motivo

    assert record.chave_correlacao == (
        "SELECT COUNT(*), NVL(AVG(x.duracao_estimada), 0), NVL(MAX(x.peso), 0) FROM %"
    )

    assert len(record.lacunas) == 2
    for lacuna in record.lacunas:
        assert lacuna["origem"] == ORIGEM_PARAMETRO_FORMAL
        assert lacuna["dominio"] == DOMINIO_NOME_DE_OBJETO
        assert "DBMS_ASSERT.ENQUOTE_NAME" in lacuna["dominio_prova"]
        assert lacuna["via_funcao"] == "DBMS_ASSERT.ENQUOTE_NAME"
        # DBMS_ASSERT nao esta em call_sites -- so PARAMETRO_FORMAL puro
        # (sem wrapper) ganha esses campos SE attach_call_sites(T-05) rodar;
        # este teste nao chama attach_call_sites, entao ficam ausentes.
        assert "call_sites_no_fechamento" not in lacuna

    nomes = {lacuna["nome"] for lacuna in record.lacunas}
    assert any("p_tabela_nome" in nome for nome in nomes)
    assert any("p_filtro_tipo" in nome for nome in nomes)

    assert record.source_fingerprint.startswith("sha256:")

    # renderizacoes tambem funcionam sobre o registro real, sem excecao.
    jsonl_text = render_jsonl([record])
    parsed = json.loads(jsonl_text.strip())
    assert parsed["site_id"] == record.site_id

    md_text = render_markdown([record])
    assert "## {}".format(record.site_id) in md_text
    assert record.categoria_provada in md_text


# ===========================================================================
# Caso 11: sanitizacao_ausente sempre [] nesta tarefa, documentado.
# ===========================================================================


def test_sanitizacao_ausente_sempre_lista_vazia() -> None:
    form = _form()
    template = _template_literal_then_lacuna()
    origins = {"L0": _origin_parametro_formal()}

    record = build_record("APP", "PKG_X", "PACKAGE BODY", "SP_Y", _SOURCE_LINES, form, template, origins)

    assert record.sanitizacao_ausente == []
    assert isinstance(record.sanitizacao_ausente, list)
