"""Testes offline (T-01, contrato depgraph-granular) para plsqlflow/attribute.py.

Modulo puro sob teste: nenhum destes casos abre conexao nem depende de rede/
banco. O golden principal reproduz as 11 atribuicoes de
docs/plano-depgraph-granular.md (secao 2), tiradas de GESTAO.FLOW_DEMO
(fixture em tests/fixtures/plscope_tree.json). Os casos adicionais (aninhado,
__INIT__, __SPEC__, CALL nao resolvida, determinismo) usam fixtures sinteticas
pequenas construidas inline, no mesmo formato de linha (mesmo padrao dos
outros testes do pacote -- ver tests/test_depgraph_unit.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from plsqlflow.attribute import (
    Assignment,
    IdentifierRow,
    ResolvedCall,
    StatementRow,
    UnresolvedCall,
    assign_context,
    build_definition_index,
    extract_definitions,
    resolve_call_target,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO / "tests" / "fixtures" / "plscope_tree.json"


def _load_fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


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


def _statements_from(rows: List[Dict[str, Any]]) -> List[StatementRow]:
    return [
        StatementRow(
            usage_id=row["usage_id"],
            usage_context_id=row["usage_context_id"],
            line=row["line"],
            stmt_type=row["stmt_type"],
        )
        for row in rows
    ]


# --------------------------------------------------------------- golden


def test_golden_flow_demo_11_assignments():
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])
    statements = _statements_from(fixture["statements"])

    assignments = assign_context(identifiers, statements, fixture["object_type"])

    got = {a.usage_id: (a.line, a.kind, a.target, a.enclosing) for a in assignments}
    expected = {
        row["usage_id"]: (row["line"], row["kind"], row["target"], row["enclosing"])
        for row in fixture["golden"]
    }
    assert got == expected, "atribuicao nao bate com o golden do plano"


def test_golden_covers_only_calls_and_statements():
    # Regra: para CADA CALL e CADA statement -- nem mais (ASSIGNMENT/
    # REFERENCE nao viram Assignment) nem menos (as 4 CALLs + 3 STMT do
    # golden mais as outras 4 CALLs fora do golden nao existem no fixture --
    # so ha exatamente as CALLs listadas). Conferimos as contagens batem
    # com o numero de linhas usage=='CALL' e o numero de statements.
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])
    statements = _statements_from(fixture["statements"])

    assignments = assign_context(identifiers, statements, fixture["object_type"])

    n_calls = sum(1 for row in fixture["identifiers"] if row["usage"] == "CALL")
    assert sum(1 for a in assignments if a.kind == "CALL") == n_calls
    assert sum(1 for a in assignments if a.kind == "STMT") == len(fixture["statements"])
    assert len(assignments) == n_calls + len(fixture["statements"])


def test_golden_overload_signatures_differ_and_resolve_to_different_targets():
    # A razao de existir a resolucao por signature (regra 5): as duas CALLs
    # de CALC_OVERLOAD em MAIN (usage_id 58 e 61) tem que resolver para
    # DESTINOS DIFERENTES (a definicao #1 vs a #2), apesar de terem o mesmo
    # called_name.
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])

    definitions = extract_definitions(identifiers, owner=fixture["owner"], object_name=fixture["object_name"])
    index = build_definition_index(definitions)

    by_id = {row.usage_id: row for row in identifiers}
    call_58 = by_id[58]
    call_61 = by_id[61]
    assert call_58.signature != call_61.signature

    resolved_58 = resolve_call_target(call_58, index)
    resolved_61 = resolve_call_target(call_61, index)

    assert isinstance(resolved_58, ResolvedCall)
    assert isinstance(resolved_61, ResolvedCall)
    assert resolved_58.subprogram == "CALC_OVERLOAD#1"
    assert resolved_61.subprogram == "CALC_OVERLOAD#2"
    assert resolved_58.subprogram != resolved_61.subprogram
    assert resolved_58.owner == fixture["owner"]
    assert resolved_58.object_name == fixture["object_name"]


def test_golden_call_target_names_match_definition_positions():
    # CALC_OVERLOAD#1 e a definicao da linha 34 (a mais cedo); #2 e a da
    # linha 39. Confere a regra 3 (posicao = ordem de linha de declaracao).
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])
    definitions = {d.signature: d for d in extract_definitions(identifiers, "GESTAO", "FLOW_DEMO")}

    def_line_34 = next(row for row in fixture["identifiers"] if row["usage_id"] == 35)
    def_line_39 = next(row for row in fixture["identifiers"] if row["usage_id"] == 40)
    assert definitions[def_line_34["signature"]].subprogram == "CALC_OVERLOAD#1"
    assert definitions[def_line_39["signature"]].subprogram == "CALC_OVERLOAD#2"


# --------------------------------------------------------------- aninhado


def test_nested_subprogram_gets_full_path():
    # PROCEDURE OUTER dentro de um PACKAGE BODY, com uma PROCEDURE INNER
    # declarada e definida DENTRO do corpo de OUTER, e uma CALL dentro de
    # INNER. O subprograma envolvente da CALL tem que ser "OUTER.INNER" --
    # nunca fundido no pai.
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "OUTER", "PROCEDURE", "DEFINITION"),
        # INNER declarada e definida dentro do corpo de OUTER (contexto=2)
        IdentifierRow(3, 2, 4, 3, "INNER", "PROCEDURE", "DEFINITION"),
        # a CALL mora dentro de INNER (contexto=3)
        IdentifierRow(4, 3, 5, 5, "HELPER", "PROCEDURE", "CALL", signature="SIGHELPER"),
    ]
    assignments = assign_context(identifiers, [], object_type="PACKAGE BODY")
    call = next(a for a in assignments if a.usage_id == 4)
    assert call.enclosing == "OUTER.INNER"


def test_deeply_nested_subprogram_three_levels():
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "A", "PROCEDURE", "DEFINITION"),
        IdentifierRow(3, 2, 4, 3, "B", "PROCEDURE", "DEFINITION"),
        IdentifierRow(4, 3, 5, 5, "C", "PROCEDURE", "DEFINITION"),
        IdentifierRow(5, 4, 6, 7, "LEAF_CALL", "PROCEDURE", "CALL"),
    ]
    assignments = assign_context(identifiers, [], object_type="PACKAGE BODY")
    call = next(a for a in assignments if a.usage_id == 5)
    assert call.enclosing == "A.B.C"


# --------------------------------------------------------------- sinteticos


def test_init_block_for_package_body():
    # Statement fora de qualquer subprogram, no corpo do PACKAGE BODY (o
    # bloco BEGIN...END do package) -> __INIT__.
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 3, 1, "SOME_PROC", "PROCEDURE", "DEFINITION"),
    ]
    statements = [StatementRow(usage_id=10, usage_context_id=1, line=8, stmt_type="INSERT")]
    assignments = assign_context(identifiers, statements, object_type="PACKAGE BODY")
    stmt = next(a for a in assignments if a.usage_id == 10)
    assert stmt.enclosing == "__INIT__"


def test_spec_level_declaration_for_package():
    # Uma CALL "solta" no nivel de pacote (ex.: expressao default de uma
    # constante de spec) num objeto object_type=PACKAGE (a spec, sem corpo
    # executavel) -> __SPEC__.
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 2, 3, "SOME_FUNC", "FUNCTION", "CALL"),
    ]
    assignments = assign_context(identifiers, [], object_type="PACKAGE")
    call = next(a for a in assignments if a.usage_id == 2)
    assert call.enclosing == "__SPEC__"


def test_init_and_spec_never_collide_same_tree_shape():
    # Mesma forma de arvore (CALL direto sob a raiz), objeto diferente ->
    # sintetico diferente. Prova que a decisao usa object_type, nao a forma
    # da arvore.
    identifiers = [
        IdentifierRow(1, 0, 1, 1, "PKG", "PACKAGE", "DEFINITION"),
        IdentifierRow(2, 1, 2, 3, "X", "PROCEDURE", "CALL"),
    ]
    as_spec = assign_context(identifiers, [], object_type="PACKAGE")
    as_body = assign_context(identifiers, [], object_type="PACKAGE BODY")
    assert as_spec[0].enclosing == "__SPEC__"
    assert as_body[0].enclosing == "__INIT__"


# --------------------------------------------------------------- nao resolvido


def test_unresolved_call_preserves_signature():
    # LENGTH e SYS.STANDARD -- nao esta definido em FLOW_DEMO, entao o
    # indice construido so a partir da arvore de FLOW_DEMO nao tem a
    # signature dela. resolve_call_target tem que devolver UnresolvedCall
    # com a signature original preservada, nunca inventar destino.
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])
    definitions = extract_definitions(identifiers, owner=fixture["owner"], object_name=fixture["object_name"])
    index = build_definition_index(definitions)

    length_call = next(row for row in identifiers if row.usage_id == 44)
    assert length_call.name == "LENGTH"

    result = resolve_call_target(length_call, index)
    assert isinstance(result, UnresolvedCall)
    assert result.signature == length_call.signature
    assert result.called_name == "LENGTH"


def test_unresolved_call_with_no_signature_at_all():
    call = IdentifierRow(99, 1, 10, 1, "MYSTERY_PROC", "PROCEDURE", "CALL", signature=None)
    result = resolve_call_target(call, index={})
    assert isinstance(result, UnresolvedCall)
    assert result.signature is None
    assert result.called_name == "MYSTERY_PROC"


def test_empty_index_never_invents_a_destination():
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])
    proc_a_call = next(row for row in identifiers if row.usage_id == 22)
    result = resolve_call_target(proc_a_call, index={})
    assert isinstance(result, UnresolvedCall)
    assert result.signature == proc_a_call.signature


# --------------------------------------------------------------- determinismo


def test_determinism_same_input_same_output():
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])
    statements = _statements_from(fixture["statements"])

    first = assign_context(identifiers, statements, fixture["object_type"])
    second = assign_context(identifiers, statements, fixture["object_type"])
    assert first == second

    definitions_first = extract_definitions(identifiers, "GESTAO", "FLOW_DEMO")
    definitions_second = extract_definitions(identifiers, "GESTAO", "FLOW_DEMO")
    assert definitions_first == definitions_second

    index_first = build_definition_index(definitions_first)
    index_second = build_definition_index(definitions_second)
    assert index_first == index_second


def test_determinism_independent_of_input_row_order():
    # Embaralhar a ordem das linhas de entrada nao pode mudar o resultado --
    # regra 6 e sobre iteracao de set nao ordenada, mas vale a pena provar
    # tambem que a saida nao depende de ordem de insercao das linhas.
    fixture = _load_fixture()
    identifiers = _identifiers_from(fixture["identifiers"])
    statements = _statements_from(fixture["statements"])

    forward = assign_context(identifiers, statements, fixture["object_type"])
    reversed_result = assign_context(list(reversed(identifiers)), list(reversed(statements)), fixture["object_type"])
    assert forward == reversed_result


# --------------------------------------------------------------- tipos basicos


def test_assignment_is_a_plain_frozen_dataclass():
    a = Assignment(usage_id=1, line=1, kind="CALL", target="X", enclosing="Y", signature="SIG")
    b = Assignment(usage_id=1, line=1, kind="CALL", target="X", enclosing="Y", signature="SIG")
    assert a == b
