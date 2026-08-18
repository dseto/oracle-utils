"""Testes offline (T-01, contrato dynsql-dossie) para plsqlflow/dynsite.py.

Modulo puro sob teste: nenhum destes casos abre conexao nem executa o SQL
analisado. Cada teste nomeado carrega UM caso de
tests/fixtures/dynsite_formas.json (chave = nome do caso, mesmo espirito do
nome do teste) -- nao um loop generico sobre a fixture, para que o nome do
teste que falhar diga exatamente qual FORMA quebrou (backlog secao 4.1/4.6).

Cobertura: as 12 formas listadas no contrato T-01 (Plans.md), incluindo as
que a amostra real (GESTAO_OO.PKG_DYNAMIC_EVALUATOR) NAO tem -- e um caso de
regressao ligado ao texto real da amostra (L73).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from plsqlflow.dynsite import DynSiteForm, classify_dynamic_site

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "dynsite_formas.json"


def _load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(name: str) -> Dict[str, Any]:
    fixture = _load_fixture()
    return fixture[name]


def _classify(name: str) -> DynSiteForm:
    case = _case(name)
    return classify_dynamic_site(case["source"], case["line"])


def _assert_matches_expect(result: DynSiteForm, expect: Dict[str, Any]) -> None:
    assert result.exec_kind == expect["exec_kind"]
    assert result.categoria_provada == expect["categoria_provada"]
    assert result.pode_invocar_procedure == expect["pode_invocar_procedure"]
    assert result.pode_invocar_procedure_determinavel == expect["pode_invocar_procedure_determinavel"]
    assert result.into_arity == expect["into_arity"]
    assert result.em_loop == expect["em_loop"]


# 1. OPEN cur FOR v_sql -- QUERY
def test_open_for_is_query() -> None:
    case = _case("open_for_query")
    result = _classify("open_for_query")
    _assert_matches_expect(result, case["expect"])
    assert result.categoria_prova  # prova nao-vazia


# 2. EXECUTE IMMEDIATE v_sql INTO a, b, c -- QUERY_LINHA_UNICA, into_arity=3
def test_execute_immediate_into_is_query_linha_unica_com_aridade_3() -> None:
    case = _case("execute_immediate_into_query_linha_unica")
    result = _classify("execute_immediate_into_query_linha_unica")
    _assert_matches_expect(result, case["expect"])
    assert result.into_arity == 3
    assert result.categoria_prova


# 3. EXECUTE IMMEDIATE v_sql BULK COLLECT INTO v_tab -- QUERY_MULTI_LINHA
def test_execute_immediate_bulk_collect_into_is_query_multi_linha() -> None:
    case = _case("execute_immediate_bulk_collect_into_query_multi_linha")
    result = _classify("execute_immediate_bulk_collect_into_query_multi_linha")
    _assert_matches_expect(result, case["expect"])
    assert result.categoria_prova


# 4. EXECUTE IMMEDIATE v_sql USING v_id, OUT v_result -- BLOCO_OU_RETURNING
def test_execute_immediate_using_out_is_bloco_ou_returning() -> None:
    case = _case("execute_immediate_using_out_bloco_ou_returning")
    result = _classify("execute_immediate_using_out_bloco_ou_returning")
    _assert_matches_expect(result, case["expect"])
    assert result.categoria_prova


# 5. EXECUTE IMMEDIATE v_sql USING IN v_id -- NAO_DETERMINAVEL (bind IN nao decide)
def test_execute_immediate_using_somente_in_is_nao_determinavel() -> None:
    case = _case("execute_immediate_using_in_nao_determinavel")
    result = _classify("execute_immediate_using_in_nao_determinavel")
    _assert_matches_expect(result, case["expect"])


# 6. EXECUTE IMMEDIATE v_sql; (nu, sem INTO/USING) -- NAO_DETERMINAVEL
def test_execute_immediate_nu_sem_into_using_is_nao_determinavel() -> None:
    case = _case("execute_immediate_bare_nao_determinavel")
    result = _classify("execute_immediate_bare_nao_determinavel")
    _assert_matches_expect(result, case["expect"])


# 7. DBMS_SQL.PARSE(...) -- NAO_DETERMINAVEL
def test_dbms_sql_parse_is_nao_determinavel() -> None:
    case = _case("dbms_sql_parse_nao_determinavel")
    result = _classify("dbms_sql_parse_nao_determinavel")
    _assert_matches_expect(result, case["expect"])


# 8. sitio dentro de LOOP ... END LOOP -- em_loop=True
def test_sitio_dentro_de_loop_marca_em_loop_true() -> None:
    case = _case("site_em_loop")
    result = _classify("site_em_loop")
    _assert_matches_expect(result, case["expect"])
    assert result.em_loop is True


# 9. sitio fora de qualquer loop -- em_loop=False
def test_sitio_fora_de_loop_marca_em_loop_false() -> None:
    case = _case("site_fora_de_loop")
    result = _classify("site_fora_de_loop")
    _assert_matches_expect(result, case["expect"])
    assert result.em_loop is False


# 10. EXECUTE IMMEDIATE 'COMMIT' -- literal 100% estatico, ainda NAO_DETERMINAVEL
def test_execute_immediate_literal_estatico_ainda_e_nao_determinavel_pela_forma() -> None:
    case = _case("execute_immediate_literal_estatico_commit")
    result = _classify("execute_immediate_literal_estatico_commit")
    _assert_matches_expect(result, case["expect"])


# 11. EXECUTE IMMEDIATE v_sql USING OUT v_r1, OUT v_r2 -- multiplos OUT, BLOCO_OU_RETURNING
def test_execute_immediate_multiplos_binds_out_is_bloco_ou_returning() -> None:
    case = _case("execute_immediate_multiplos_out")
    result = _classify("execute_immediate_multiplos_out")
    _assert_matches_expect(result, case["expect"])


# 12. Regressao ligada a evidencia real: GESTAO_OO.PKG_DYNAMIC_EVALUATOR L73
def test_regressao_pkg_dynamic_evaluator_l73_using_out_in() -> None:
    case = _case("regressao_pkg_dynamic_evaluator_l73")
    result = _classify("regressao_pkg_dynamic_evaluator_l73")
    _assert_matches_expect(result, case["expect"])
    assert result.line == 73


def test_toda_categoria_diferente_de_nao_determinavel_tem_prova_nao_vazia() -> None:
    fixture = _load_fixture()
    for name, case in fixture.items():
        if name.startswith("_"):
            continue
        result = classify_dynamic_site(case["source"], case["line"])
        if result.categoria_provada != "NAO_DETERMINAVEL":
            assert result.categoria_prova, "caso {} sem prova".format(name)


def test_regra_de_desempate_nunca_vira_query_ou_bloco_por_acidente() -> None:
    """Nenhuma forma ambigua do dataset de teste (as que o proprio fixture
    marca como NAO_DETERMINAVEL) pode sair como QUERY, QUERY_LINHA_UNICA,
    QUERY_MULTI_LINHA ou BLOCO_OU_RETURNING -- regra de desempate SEM
    excecao (contrato): ambiguo -> NAO_DETERMINAVEL,
    pode_invocar_procedure=True."""
    fixture = _load_fixture()
    ambiguous_names = [
        name
        for name, case in fixture.items()
        if not name.startswith("_") and case["expect"]["categoria_provada"] == "NAO_DETERMINAVEL"
    ]
    assert ambiguous_names, "fixture precisa ter pelo menos um caso NAO_DETERMINAVEL"
    for name in ambiguous_names:
        case = fixture[name]
        result = classify_dynamic_site(case["source"], case["line"])
        assert result.categoria_provada == "NAO_DETERMINAVEL", name
        assert result.pode_invocar_procedure is True, name
        assert result.pode_invocar_procedure_determinavel is False, name
