"""Testes de documentacao da skill /oracle-dependency-graph (T-06) -- 100%
offline, sem banco. Espelha o padrao de tests/test_plsqlflow_skill.py
(convencao estatica) e tests/test_conventions.py (links relativos).

Cobre a Entrega 3 do contrato oracle-depgraph:
- frontmatter valido (name/description) na SKILL.md nova
- secoes obrigatorias, incluindo honestidade/pontos-cegos e grep
- links relativos resolvem
- padroes de grep documentados usam os nomes de campo REAIS de edges.jsonl
  (cross-check contra tests/fixtures/depgraph_golden/edges.jsonl)
- flags e exit codes documentados existem de fato em plsqlflow/cli.py
- nenhuma senha/DSN em linha de comando
- cross-links adicionados nas 4 skills/agentes existentes
- oracle-graph/ no .gitignore
- a correcao sobre lexical.py em plsql-flow/SKILL.md e coerente
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "oracle-dependency-graph" / "SKILL.md"
DEP_GRAPH_SKILL = REPO / ".claude" / "skills" / "dep-graph" / "SKILL.md"
PLSQL_FLOW_SKILL = REPO / ".claude" / "skills" / "plsql-flow" / "SKILL.md"
PLSQL_REVIEW_SKILL = REPO / ".claude" / "skills" / "plsql-review" / "SKILL.md"
ORACLE_DBA_AGENT = REPO / ".claude" / "agents" / "oracle-dba.md"
GITIGNORE = REPO / ".gitignore"
GOLDEN_EDGES = REPO / "tests" / "fixtures" / "depgraph_golden" / "edges.jsonl"

LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

REQUIRED_SECTIONS = [
    "PONTOS CEGOS",
    "chain_hash",
    "edges.jsonl",
    "INDEX.md",
    "recompile.sql",
    "meta.json",
    "to_ref",
    "confidence",
    "partial",
    "opaque",
    "grep",
    "run-query.ps1",
    "fallback",
    "nodes/",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------- frontmatter


def test_skill_doc_exists():
    assert SKILL.exists(), "SKILL.md da skill oracle-dependency-graph ausente"


def test_skill_doc_frontmatter():
    text = read(SKILL)
    assert text.startswith("---"), "frontmatter YAML ausente"
    assert "name: oracle-dependency-graph" in text, "frontmatter sem name correto"
    assert "description:" in text, "frontmatter sem description"
    header = text.split("---", 2)[1]
    assert "usar quando" in header.lower(), (
        "description precisa descrever QUANDO usar (gatilho em linguagem natural)"
    )


# ---------------------------------------------------------- secoes obrigatorias


def test_skill_doc_covers_required_sections():
    text = read(SKILL)
    lower = text.lower()
    missing = [s for s in REQUIRED_SECTIONS if s.lower() not in lower]
    assert not missing, f"secoes/temas ausentes na SKILL.md nova: {missing}"


def test_skill_doc_has_honesty_rule_section():
    text = read(SKILL)
    lower = text.lower()
    assert "honestidade" in lower, "SKILL.md sem regra de honestidade explicita"
    assert "PONTOS CEGOS" in text, "SKILL.md nao referencia a secao PONTOS CEGOS do INDEX.md"
    assert "inferência" in lower or "inferencia" in lower, (
        "SKILL.md precisa proibir inferir dependencia ausente sem declarar que e fora do grafo"
    )


def test_skill_doc_has_grep_patterns_section():
    text = read(SKILL)
    lower = text.lower()
    assert "padrões de grep" in lower or "padroes de grep" in lower, (
        "SKILL.md sem secao dedicada de padroes de grep de consumo"
    )
    assert "grep " in text, "SKILL.md sem comando de grep concreto (nao so prosa)"


def test_skill_doc_documents_when_to_regenerate():
    lower = read(SKILL).lower()
    assert "chain_hash" in lower
    assert "regenerar" in lower


def test_skill_doc_documents_fallback_without_python():
    lower = read(SKILL).lower()
    assert "run-query.ps1" in lower
    assert "parcial" in lower


# ------------------------------------------------------------------- links


def test_skill_doc_links_resolve():
    text = read(SKILL)
    broken = []
    for target in LINK_PATTERN.findall(text):
        if target.startswith("http://") or target.startswith("https://"):
            continue
        resolved = (SKILL.parent / target).resolve()
        if not resolved.exists():
            broken.append(target)
    assert not broken, f"links quebrados em oracle-dependency-graph/SKILL.md: {broken}"


# ---------------------------------------------------- campos reais de edges.jsonl


def _real_edge_keys():
    line = read(GOLDEN_EDGES).splitlines()[0]
    return set(json.loads(line).keys())


def test_golden_edges_fixture_has_expected_real_keys():
    real_keys = _real_edge_keys()
    assert {"from_ref", "to_ref", "edge_type", "line", "op", "cols", "confidence"} <= real_keys


def test_skill_doc_grep_patterns_use_real_edge_field_names():
    text = read(SKILL)
    assert '"to_ref"' in text, "SKILL.md nao documenta o campo real to_ref"
    assert '"edge_type"' in text, "SKILL.md nao documenta o campo real edge_type"
    assert '"op"' in text, "SKILL.md nao documenta o campo real op"

    # nomes ERRADOS que a spec pede para nunca aparecer como campo JSON
    assert re.search(r'"to":\s*"', text) is None, (
        'SKILL.md usa o campo errado "to": -- o campo real e "to_ref"'
    )
    assert re.search(r'"from":\s*"', text) is None, (
        'SKILL.md usa o campo errado "from": -- o campo real e "from_ref"'
    )


def test_skill_doc_grep_examples_match_golden_edges_separator():
    """json.dumps(..., sort_keys=True) sem `separators` grava ': ' (com
    espaco) depois da chave -- os comandos de grep documentados tem que
    casar contra o formato REAL gravado em disco (golden fixture), nao
    contra um formato compacto inventado (`"to_ref":"..."`)."""
    golden_line = read(GOLDEN_EDGES).splitlines()[0]
    assert '"to_ref": "' in golden_line, "fixture golden mudou de formato -- reconferir separador"
    text = read(SKILL)
    assert '"to_ref": "' in text, (
        "grep de exemplo tem que usar o separador real ': ' (com espaco), "
        "senao o comando documentado nao bate em disco de verdade"
    )


# ---------------------------------------------------- flags e exit codes reais


def test_skill_doc_documents_real_cli_flags():
    from plsqlflow import cli

    parser = cli.build_depgraph_arg_parser()
    option_strings = set()
    for action in parser._actions:
        option_strings.update(action.option_strings)

    text = read(SKILL)
    for flag in (
        "--conn",
        "--output",
        "--stop-schemas",
        "--dynamic-window",
        "--max-objects",
        "--max-depth",
    ):
        assert flag in option_strings, f"flag {flag} nao existe de fato em cli.py"
        assert flag in text, f"SKILL.md nao documenta a flag real {flag}"

    assert "python -m plsqlflow depgraph" in text
    assert cli.DEPGRAPH_SUBCOMMAND in text


def test_skill_doc_documents_real_exit_codes():
    from plsqlflow import cli

    codes = {
        cli.EXIT_OK,
        cli.EXIT_NEEDS_RECOMPILE,
        cli.EXIT_INVALID_ROOT,
        cli.EXIT_CONNECTION_ERROR,
    }
    assert codes == {0, 3, 4, 5}, "exit codes de cli.py mudaram -- reconferir plano secao 4.3"

    text = read(SKILL)
    for code in codes:
        assert str(code) in text, f"SKILL.md nao documenta o exit code real {code}"


def test_skill_doc_never_puts_password_in_command_line():
    lower = read(SKILL).lower()
    assert "nunca" in lower and "senha" in lower, (
        "SKILL.md precisa deixar explicito que a senha nunca vai em argumento"
    )
    assert "plsqlflow_pwd" in lower, "SKILL.md nao documenta a variavel de ambiente de senha"
    assert "--password" not in lower
    assert "--pwd" not in lower
    assert "--dsn" not in lower


# --------------------------------------------------------- cross-links (Entrega 2)


def test_dep_graph_skill_cross_links_to_new_skill():
    text = read(DEP_GRAPH_SKILL)
    assert "oracle-dependency-graph" in text, (
        "dep-graph/SKILL.md sem cross-link para /oracle-dependency-graph"
    )


def test_plsql_flow_skill_cross_links_to_new_skill():
    text = read(PLSQL_FLOW_SKILL)
    assert "oracle-dependency-graph" in text, (
        "plsql-flow/SKILL.md sem cross-link para /oracle-dependency-graph"
    )


def test_plsql_review_skill_points_to_graph_before_db():
    text = read(PLSQL_REVIEW_SKILL).lower()
    assert "oracle-graph" in text, (
        "plsql-review/SKILL.md sem instrucao para grepar oracle-graph/ antes do banco"
    )


def test_oracle_dba_agent_points_to_graph_before_db():
    text = read(ORACLE_DBA_AGENT).lower()
    assert "oracle-graph" in text, (
        "oracle-dba.md sem instrucao para grepar oracle-graph/ antes do banco"
    )


# ------------------------------------------------------------------- .gitignore


def test_gitignore_has_oracle_graph():
    text = read(GITIGNORE)
    assert "oracle-graph/" in text, ".gitignore nao ignora oracle-graph/"


# ------------------------------------------------ correcao da afirmacao lexical.py


def test_plsql_flow_skill_lexical_claim_updated():
    text = read(PLSQL_FLOW_SKILL)
    assert "find_object_reference_candidates" in text, (
        "plsql-flow/SKILL.md nao foi atualizado com o modo fragmento-SQL novo de lexical.py"
    )
    assert "find_call_candidates" in text, (
        "plsql-flow/SKILL.md precisa continuar precisos sobre find_call_candidates "
        "(esse, sim, continua sem uso automatico neste script)"
    )
    assert "oracle-dependency-graph" in text or "oracle-depgraph" in text, (
        "plsql-flow/SKILL.md nao referencia o contrato/skill que ligou a camada B"
    )


def test_plsql_flow_skill_still_covers_baseline_sections():
    """Nao pode ter quebrado as secoes ja cobertas por
    tests/test_plsqlflow_skill.py::test_skill_doc_covers_required_sections
    ao editar o paragrafo da Camada B."""
    lower = read(PLSQL_FLOW_SKILL).lower()
    for token in ("pl/scope", "all_source", "execute immediate", "lexical", "heuristic"):
        assert token in lower, f"edicao removeu o token esperado: {token}"


# ------------------------------------------------------- evidencia live (T-06)
# Gated: so roda com PLSQLFLOW_USER/PWD/DSN no ambiente (tests/conftest.py os
# carrega de .harness/scratch/dev_creds.json quando existir). Prova o caminho
# que nenhum teste offline cobre: SQL real batendo no dicionario do Oracle --
# foi assim que o contrato anterior descobriu um ORA-00933 invisivel no mock.

import os
import shutil

import pytest

LIVE_ENV = ("PLSQLFLOW_USER", "PLSQLFLOW_PWD", "PLSQLFLOW_DSN")
EVIDENCE = REPO / ".harness" / "scratch" / "depgraph-evidence.md"


@pytest.mark.skipif(
    not all(os.environ.get(v) for v in LIVE_ENV),
    reason="credenciais de dev nao disponiveis nesta sessao (PLSQLFLOW_USER/PLSQLFLOW_PWD/PLSQLFLOW_DSN)",
)
def test_live_depgraph_against_dev(tmp_path):
    from plsqlflow import cli

    out = tmp_path / "oracle-graph"
    code = cli.main(["depgraph", "GESTAO.FLOW_DEMO", "--output", str(out)])
    assert code in (0, 3), "exit inesperado do depgraph contra o dev: {}".format(code)

    graph_dir = out / "GESTAO.FLOW_DEMO"
    index = (graph_dir / "INDEX.md").read_text(encoding="utf-8")
    edges = (graph_dir / "edges.jsonl").read_text(encoding="utf-8")
    nodes = sorted(p.name for p in (graph_dir / "nodes").glob("*.md"))

    # Fatos verificaveis da fixture FLOW_DEMO no banco real.
    assert "GESTAO.FLOW_DEMO.md" in nodes
    assert "GESTAO.FLOW_DEMO_LOG.md" in nodes
    assert "GESTAO.TRG_FLOW_DEMO_LOG.md" in nodes, "trigger nao entrou via Fase 4"
    assert "GESTAO.FLOW_DEMO_AUDIT.md" in nodes, "tabela alcancada so pelo trigger nao entrou"
    assert '"edge_type": "TRIGGER_FIRES"' in edges
    assert "PONTOS CEGOS" in index

    # Idempotencia contra banco real: regerar produz os mesmos bytes.
    first = {p.relative_to(graph_dir): p.read_bytes() for p in graph_dir.rglob("*") if p.is_file()}
    shutil.rmtree(str(graph_dir))
    assert cli.main(["depgraph", "GESTAO.FLOW_DEMO", "--output", str(out)]) in (0, 3)
    second = {p.relative_to(graph_dir): p.read_bytes() for p in graph_dir.rglob("*") if p.is_file()}
    assert first == second, "grafo nao e byte-identico ao regerar contra o mesmo banco"

    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(
        "# Evidencia e2e -- /oracle-dependency-graph contra dev (XE 21c local)\n\n"
        + "Alvo: GESTAO.FLOW_DEMO | exit={}\n\n".format(code)
        + "Nos gerados: {}\n\n".format(", ".join(nodes))
        + "## INDEX.md\n\n```markdown\n"
        + index
        + "\n```\n\n## edges.jsonl\n\n```json\n"
        + edges
        + "\n```\n",
        encoding="utf-8",
        newline="\n",
    )
