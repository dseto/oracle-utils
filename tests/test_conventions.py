"""Testes de convencao do toolkit oracle-utils.

Validam as regras do CLAUDE.md que sao verificaveis estaticamente:
- todo .sql da biblioteca documenta seus binds (ou declara 'Binds: nenhum');
- nenhum .ps1 contem bytes fora de ASCII;
- links relativos [x.sql](...) nas SKILL.md apontam para arquivos existentes.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SQL_DIR = REPO / "sql"
SKILLS_DIR = REPO / ".claude" / "skills"
SCRIPTS_DIR = REPO / "scripts"

BIND_PATTERN = re.compile(r"--.*[Bb]inds?\s*:")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+\.sql)\)")


def sql_files():
    return sorted(SQL_DIR.rglob("*.sql"))


def test_sql_library_not_empty():
    assert sql_files(), "biblioteca sql/ vazia"


def test_every_sql_documents_binds():
    missing = []
    for f in sql_files():
        head = "\n".join(f.read_text(encoding="utf-8").splitlines()[:15])
        if not BIND_PATTERN.search(head):
            missing.append(str(f.relative_to(REPO)))
    assert not missing, f"sem comentario de binds no topo: {missing}"


def test_ps1_scripts_are_ascii_only():
    offenders = []
    for f in SCRIPTS_DIR.rglob("*.ps1"):
        data = f.read_bytes()
        bad = [i for i, b in enumerate(data) if b > 127]
        if bad:
            offenders.append(f"{f.name} (primeiro byte >127 no offset {bad[0]})")
    assert not offenders, f"scripts .ps1 com bytes nao-ASCII: {offenders}"


def test_skill_links_resolve():
    broken = []
    for skill_md in SKILLS_DIR.rglob("SKILL.md"):
        text = skill_md.read_text(encoding="utf-8")
        for target in LINK_PATTERN.findall(text):
            resolved = (skill_md.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{skill_md.parent.name}: {target}")
    assert not broken, f"links quebrados em SKILL.md: {broken}"
