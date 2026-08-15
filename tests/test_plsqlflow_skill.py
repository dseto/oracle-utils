"""Testes da SKILL.md v2 (T-05): convencao de doc (sempre roda, sem banco) e
evidencia e2e contra o dev real (gated por env vars, skip gracioso sem elas).

Grupos por -k:
  skill_doc -> convencao estatica da SKILL.md v2 (script primeiro, LLM residual)
  live      -> test_live_flow_demo_against_dev, roda so com PLSQLFLOW_USER/
               PLSQLFLOW_PWD/PLSQLFLOW_DSN setadas
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pytest

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "plsql-flow" / "SKILL.md"
EVIDENCE = REPO / ".harness" / "scratch" / "plsqlflow-py-evidence.md"

LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+\.sql)\)")

# Temas que a v1 (100% LLM, tests/test_plsql_flow.py) ja cobria e que ainda
# fazem sentido na v2 -- agora a maioria descreve o que o SCRIPT Python faz,
# nao mais o que o LLM tem que derivar manualmente.
REQUIRED_SKILL_SECTIONS = [
    "PL/Scope",             # camada A
    "ALL_SOURCE",           # camada B lexica (fallback)
    "EXECUTE IMMEDIATE",    # SQL dinamico
    "OVERRIDING",           # OO dispatch
    "trigger",              # triggers
    "CASCADE",              # cascata FK
    "visited",              # anti-loop: visited set
    "max_depth",            # anti-loop: profundidade
    "mermaid",              # saida
    "legenda",              # legenda no diagrama
    "DBMS_HPROF",           # modo dinamico
    "wrapped",              # codigo ofuscado
    "DB link",              # objetos remotos
    "ALL_ARGUMENTS",        # overloads
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------- skill_doc (T-05)


def test_skill_doc_exists():
    assert SKILL.exists(), "SKILL.md da skill plsql-flow ausente"


def test_skill_doc_frontmatter():
    text = read(SKILL)
    assert text.startswith("---"), "frontmatter YAML ausente"
    assert "name: plsql-flow" in text, "frontmatter sem name: plsql-flow"
    assert "description:" in text, "frontmatter sem description"


def test_skill_doc_covers_required_sections():
    text = read(SKILL)
    lower = text.lower()
    missing = [s for s in REQUIRED_SKILL_SECTIONS if s.lower() not in lower]
    assert not missing, f"secoes/temas ausentes na SKILL.md v2: {missing}"


def test_skill_doc_covers_synonyms():
    lower = read(SKILL).lower()
    assert "sinonim" in lower or "sinônim" in lower, (
        "SKILL.md precisa cobrir resolucao de sinonimos"
    )


def test_skill_doc_links_resolve():
    text = read(SKILL)
    broken = []
    for target in LINK_PATTERN.findall(text):
        if not (SKILL.parent / target).resolve().exists():
            broken.append(target)
    assert not broken, f"links quebrados: {broken}"


def test_skill_doc_warns_dynamic_mode_side_effects():
    lower = read(SKILL).lower()
    assert "efeito" in lower, "SKILL.md sem aviso de efeitos do modo dinamico"
    assert "colatera" in lower, "SKILL.md sem aviso de efeitos colaterais do modo dinamico"


def test_skill_doc_documents_script_first_step():
    """Passo 1 tem que documentar o comando exato do CLI, com --conn e --json."""
    text = read(SKILL)
    assert "python -m plsqlflow" in text, "SKILL.md nao documenta `python -m plsqlflow` como primeiro passo"
    assert "--conn" in text
    assert "--json" in text


def test_skill_doc_never_puts_password_in_command_line():
    lower = read(SKILL).lower()
    assert "nunca" in lower and "senha" in lower, (
        "SKILL.md precisa deixar explicito que --conn nunca leva senha na linha de comando"
    )
    assert "plsqlflow_pwd" in lower, "SKILL.md nao documenta a variavel de ambiente de senha"


def test_skill_doc_documents_residual_blind_spot_cases():
    """Os 3 casos residuais do Passo 2: dynsql_unresolved, override_open e
    confianca lexical/heuristic (sem PL/Scope) tem que aparecer nominalmente."""
    lower = read(SKILL).lower()
    assert "dynsql_unresolved" in lower, "SKILL.md nao menciona o tipo dynsql_unresolved"
    assert "override_open" in lower, "SKILL.md nao menciona o tipo override_open"
    assert "lexical" in lower, "SKILL.md nao menciona o nivel de confianca lexical"
    assert "heuristic" in lower, "SKILL.md nao menciona o nivel de confianca heuristic"


def test_skill_doc_documents_blind_spots_field():
    text = read(SKILL)
    assert "blind_spots" in text, "SKILL.md nao documenta o campo blind_spots do JSON"


def test_skill_doc_states_zero_llm_interpretation_on_clean_graph():
    lower = read(SKILL).lower()
    assert "zero" in lower, (
        "SKILL.md precisa deixar explicito que o grafo sem pontos cegos nao passa por interpretacao de LLM"
    )


# ------------------------------------------------------- cli.py (funcoes puras)
#
# Achado do blind review do contrato plsqlflow-py: resolve_synonym_chain e o
# gate de plscope_check existiam como biblioteca testada mas nunca eram
# chamados pelo pipeline real (cli.py). Corrigido ligando as duas em cli.py;
# os testes abaixo cobrem as funcoes PURAS que fazem a decisao (sem precisar
# de conexao real -- cli.py monta as linhas via extract.fetch_* com uma conn
# de verdade, essas funcoes so recebem o resultado ja extraido).


def test_synonym_fallback_target_resolves_to_base_object():
    from plsqlflow import extract
    from plsqlflow.cli import synonym_fallback_target
    from plsqlflow.graph import RootTarget

    target = RootTarget(owner="GESTAO", object_name="FLOW_DEMO_SYN", subprogram="MAIN")
    chain = [
        extract.SynonymRow(
            synonym_owner="GESTAO",
            synonym_name="FLOW_DEMO_SYN",
            base_owner="GESTAO",
            base_name="FLOW_DEMO",
            db_link=None,
        )
    ]
    resolved = synonym_fallback_target(chain, target)
    assert resolved.owner == "GESTAO"
    assert resolved.object_name == "FLOW_DEMO"
    assert resolved.subprogram == "MAIN"


def test_synonym_fallback_target_keeps_original_on_db_link():
    from plsqlflow import extract
    from plsqlflow.cli import synonym_fallback_target
    from plsqlflow.graph import RootTarget

    target = RootTarget(owner="GESTAO", object_name="REMOTE_SYN")
    chain = [
        extract.SynonymRow(
            synonym_owner="GESTAO",
            synonym_name="REMOTE_SYN",
            base_owner="REMOTE",
            base_name="REMOTE_PKG",
            db_link="REMOTE_LINK",
        )
    ]
    resolved = synonym_fallback_target(chain, target)
    assert resolved is target


def test_synonym_fallback_target_keeps_original_on_empty_chain():
    from plsqlflow.cli import synonym_fallback_target
    from plsqlflow.graph import RootTarget

    target = RootTarget(owner="GESTAO", object_name="FLOW_DEMO")
    assert synonym_fallback_target([], target) is target


def test_plscope_available_true_when_identifiers_all_present():
    from plsqlflow import extract
    from plsqlflow.cli import plscope_available

    checks = [
        extract.PlscopeCheckRow(
            owner="GESTAO", name="FLOW_DEMO", type="PACKAGE BODY",
            plscope_settings="IDENTIFIERS:ALL, STATEMENTS:ALL",
        )
    ]
    assert plscope_available(checks, "flow_demo") is True


def test_plscope_available_false_when_missing_or_disabled():
    from plsqlflow import extract
    from plsqlflow.cli import plscope_available

    assert plscope_available([], "FLOW_DEMO") is False
    checks = [
        extract.PlscopeCheckRow(
            owner="GESTAO", name="FLOW_DEMO", type="PACKAGE BODY", plscope_settings=None,
        )
    ]
    assert plscope_available(checks, "FLOW_DEMO") is False


def test_ensure_plscope_raises_clear_error_without_identifiers_all(monkeypatch):
    from plsqlflow import cli, extract
    from plsqlflow.graph import RootTarget

    monkeypatch.setattr(extract, "fetch_plscope_check", lambda conn, owner: [])
    target = RootTarget(owner="GESTAO", object_name="NO_PLSCOPE_PKG")
    with pytest.raises(RuntimeError, match="PLSCOPE_SETTINGS"):
        cli._ensure_plscope(conn=None, target=target)


def test_cli_conn_argument_is_optional():
    from plsqlflow.cli import build_arg_parser

    args = build_arg_parser().parse_args(["GESTAO.FLOW_DEMO.MAIN"])
    assert args.conn is None


# -------------------------------------------------------------------- live e2e

LIVE_ENV_VARS = ("PLSQLFLOW_USER", "PLSQLFLOW_PWD", "PLSQLFLOW_DSN")


def _live_creds_available() -> bool:
    return all(os.environ.get(name) for name in LIVE_ENV_VARS)


def _write_evidence(result: Dict[str, object]) -> None:
    nodes: List[Dict[str, object]] = result["nodes"]  # type: ignore[assignment]
    edges: List[Dict[str, object]] = result["edges"]  # type: ignore[assignment]
    blind_spots: List[Dict[str, object]] = result["blind_spots"]  # type: ignore[assignment]

    lines = [
        "# Evidencia e2e -- /plsql-flow contra dev (GESTAO.FLOW_DEMO.MAIN)",
        "",
        "Gerada por `pytest tests/test_plsqlflow_skill.py::test_live_flow_demo_against_dev`.",
        "",
        "- Timestamp da conexao (UTC): {}".format(datetime.now(timezone.utc).isoformat()),
        "- Nos: {}".format(len(nodes)),
        "- Arestas: {}".format(len(edges)),
        "- Pontos cegos: {}".format(len(blind_spots)),
        "",
        "## Pontos cegos",
        "",
    ]
    if blind_spots:
        for spot in blind_spots:
            lines.append("- `{}` @ {}".format(spot["type"], spot["at"]))
    else:
        lines.append("(nenhum)")
    lines.append("")
    lines.append("## Mermaid")
    lines.append("")
    lines.append("```mermaid")
    lines.append(str(result["mermaid"]).rstrip("\n"))
    lines.append("```")
    lines.append("")

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.skipif(
    not _live_creds_available(),
    reason="credenciais de dev nao disponiveis nesta sessao (PLSQLFLOW_USER/PLSQLFLOW_PWD/PLSQLFLOW_DSN)",
)
def test_live_flow_demo_against_dev():
    from plsqlflow import db, report
    from plsqlflow.cli import DbExtractor
    from plsqlflow.graph import RootTarget

    conn = db.connect()
    try:
        root = RootTarget(owner="GESTAO", object_name="FLOW_DEMO", subprogram="MAIN")
        extractor = DbExtractor(conn)
        result = report.build_report(extractor, root)
    finally:
        conn.close()

    nodes = result["nodes"]
    edges = result["edges"]

    recursion_edges = [e for e in edges if e["kind"] == "recursion"]
    assert any(
        ("PROC_A" in e["from"] and "PROC_B" in e["to"])
        or ("PROC_B" in e["from"] and "PROC_A" in e["to"])
        for e in recursion_edges
    ), "esperava pelo menos uma aresta de recursao entre PROC_A e PROC_B"

    dynsql_nodes = [n for n in nodes if n["kind"] == "dynsql"]
    assert any(n["confidence"] == "lexical" for n in dynsql_nodes), (
        "esperava pelo menos um no dynsql resolvido (confidence=lexical)"
    )
    assert any(n["confidence"] == "heuristic" for n in dynsql_nodes), (
        "esperava pelo menos um no dynsql irresolvivel (confidence=heuristic)"
    )

    assert any(e["kind"] == "trigger" for e in edges), "esperava pelo menos uma aresta de trigger"

    overload_node_ids = {n["id"] for n in nodes if n.get("subprogram") == "CALC_OVERLOAD"}
    assert len(overload_node_ids) >= 2, (
        "esperava 2 nos distintos para CALC_OVERLOAD (overloads), achou {}".format(overload_node_ids)
    )

    _write_evidence(result)
