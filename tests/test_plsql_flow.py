"""Testes do contrato plsql-flow (T-01..T-05).

Grupos por -k:
  queries      -> T-01 biblioteca sql/flow/
  skill_doc    -> T-02 SKILL.md da skill
  fixture      -> T-03 script da fixture FLOW_DEMO
  hprof        -> T-04 modo dinamico DBMS_HPROF
  e2e_evidence -> T-05 evidencia da validacao end-to-end
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLOW_DIR = REPO / "sql" / "flow"
SKILL = REPO / ".claude" / "skills" / "plsql-flow" / "SKILL.md"
FIXTURE = FLOW_DIR / "fixture_flow_demo.sql"
EVIDENCE = REPO / ".harness" / "scratch" / "plsql-flow-evidence.md"

BIND_PATTERN = re.compile(r"--.*[Bb]inds?\s*:")

FLOW_QUERIES = [
    "resolve_target.sql",
    "plscope_check.sql",
    "plscope_calls.sql",
    "plscope_statements.sql",
    "fetch_source.sql",
    "deps_direct.sql",
    "triggers_for_tables.sql",
    "fk_cascade.sql",
    "type_hierarchy.sql",
    "resolve_synonym.sql",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- T-01 queries

def test_queries_all_present():
    missing = [q for q in FLOW_QUERIES if not (FLOW_DIR / q).exists()]
    assert not missing, f"queries ausentes em sql/flow/: {missing}"


def test_queries_document_binds():
    offenders = []
    for q in FLOW_QUERIES:
        f = FLOW_DIR / q
        if not f.exists():
            continue
        head = "\n".join(read(f).splitlines()[:15])
        if not BIND_PATTERN.search(head):
            offenders.append(q)
    assert not offenders, f"sem comentario de binds: {offenders}"


def test_queries_are_ascii():
    offenders = []
    for q in FLOW_QUERIES:
        f = FLOW_DIR / q
        if f.exists() and any(b > 127 for b in f.read_bytes()):
            offenders.append(q)
    assert not offenders, f"bytes nao-ASCII: {offenders}"


def test_queries_key_dictionary_views():
    """Cada query usa a view de dicionario que a justifica no plano."""
    expectations = {
        "resolve_target.sql": "all_arguments",
        "plscope_check.sql": "all_plsql_object_settings",
        "plscope_calls.sql": "all_identifiers",
        "plscope_statements.sql": "all_statements",
        "fetch_source.sql": "all_source",
        "deps_direct.sql": "all_dependencies",
        "triggers_for_tables.sql": "all_triggers",
        "fk_cascade.sql": "all_constraints",
        "type_hierarchy.sql": "all_type",
        "resolve_synonym.sql": "all_synonyms",
    }
    offenders = []
    for q, view in expectations.items():
        f = FLOW_DIR / q
        if f.exists() and view not in read(f).lower():
            offenders.append(f"{q} nao referencia {view}")
    assert not offenders, offenders


# ------------------------------------------------------------- T-02 skill_doc

REQUIRED_SKILL_SECTIONS = [
    "PL/Scope",            # camada A
    "ALL_SOURCE",          # camada B lexica
    "EXECUTE IMMEDIATE",   # SQL dinamico
    "OVERRIDING",          # OO dispatch
    "trigger",             # triggers
    "CASCADE",             # cascata FK
    "visited",             # anti-loop: visited set
    "max_depth",           # anti-loop: profundidade
    "mermaid",             # saida
    "legenda",             # legenda no diagrama
    "DBMS_HPROF",          # modo dinamico
    "wrapped",             # codigo ofuscado
    "DB link",             # objetos remotos
    "ALL_ARGUMENTS",       # overloads
]


def test_skill_doc_exists():
    assert SKILL.exists(), "SKILL.md da skill plsql-flow ausente"


def test_skill_doc_covers_required_sections():
    text = read(SKILL)
    lower = text.lower()
    missing = [s for s in REQUIRED_SKILL_SECTIONS if s.lower() not in lower]
    assert not missing, f"secoes/temas ausentes na SKILL.md: {missing}"


def test_skill_doc_covers_synonyms():
    lower = read(SKILL).lower()
    assert "sinonim" in lower or "sinônim" in lower, (
        "SKILL.md precisa cobrir resolucao de sinonimos"
    )


def test_skill_doc_links_resolve():
    text = read(SKILL)
    broken = []
    for target in re.findall(r"\[[^\]]+\]\(([^)]+\.sql)\)", text):
        if not (SKILL.parent / target).resolve().exists():
            broken.append(target)
    assert not broken, f"links quebrados: {broken}"


def test_skill_doc_frontmatter():
    text = read(SKILL)
    assert text.startswith("---"), "frontmatter YAML ausente"
    assert "name: plsql-flow" in text, "frontmatter sem name: plsql-flow"
    assert "description:" in text, "frontmatter sem description"


# --------------------------------------------------------------- T-03 fixture

def test_fixture_exists():
    assert FIXTURE.exists(), "fixture_flow_demo.sql ausente"


def test_fixture_covers_required_cases():
    text = read(FIXTURE).upper()
    checks = {
        "recursao mutua (PROC_A)": "PROC_A" in text,
        "recursao mutua (PROC_B)": "PROC_B" in text,
        "execute immediate": "EXECUTE IMMEDIATE" in text,
        "trigger": "CREATE OR REPLACE TRIGGER" in text,
        "overload (>=2 assinaturas)": text.count("PROCEDURE CALC_OVERLOAD") >= 2
        or text.count("FUNCTION CALC_OVERLOAD") >= 2,
        "plscope na compilacao": "PLSCOPE_SETTINGS" in text,
    }
    missing = [k for k, ok in checks.items() if not ok]
    assert not missing, f"fixture sem casos obrigatorios: {missing}"


def test_fixture_has_dynamic_unresolvable():
    """Pelo menos um EXECUTE IMMEDIATE montado com variavel (irresoluvel)."""
    text = read(FIXTURE).upper()
    assert re.search(r"EXECUTE IMMEDIATE\s+[A-Z_]?V", text) or "|| V_" in text, (
        "fixture precisa de EXECUTE IMMEDIATE com string montada em variavel"
    )


# ----------------------------------------------------------------- T-04 hprof

def test_hprof_scripts_exist():
    assert (FLOW_DIR / "hprof_setup.sql").exists(), "hprof_setup.sql ausente"
    assert (FLOW_DIR / "hprof_report.sql").exists(), "hprof_report.sql ausente"


def test_hprof_setup_content():
    text = read(FLOW_DIR / "hprof_setup.sql").upper()
    assert "DBMS_HPROF" in text, "setup sem DBMS_HPROF"


def test_hprof_report_content():
    text = read(FLOW_DIR / "hprof_report.sql").upper()
    assert "DBMSHP_" in text, "report nao consulta tabelas DBMSHP_*"


def test_hprof_skill_warns_side_effects():
    text = read(SKILL).lower()
    assert "efeito" in text, "SKILL.md sem aviso de efeitos do modo dinamico"
    assert "colatera" in text, "SKILL.md sem aviso de efeitos colaterais do modo dinamico"


# ---------------------------------------------------------- T-05 e2e_evidence

def test_e2e_evidence_exists():
    assert EVIDENCE.exists(), "evidencia .harness/scratch/plsql-flow-evidence.md ausente"


def test_e2e_evidence_content():
    text = read(EVIDENCE)
    lower = text.lower()
    checks = {
        "bloco mermaid": "```mermaid" in text,
        "ciclo de recursao marcado": "recursao" in lower,
        "trigger no grafo": "trg_flow_demo" in lower,
        "no de SQL dinamico irresoluvel": "dinamico" in lower,
        "overload resolvido": "overload" in lower,
        "package alvo": "flow_demo" in lower,
    }
    missing = [k for k, ok in checks.items() if not ok]
    assert not missing, f"evidencia incompleta: {missing}"
