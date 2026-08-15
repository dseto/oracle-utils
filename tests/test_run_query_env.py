"""Testes de scripts/run-query.ps1 -Connection env.

Invocam o .ps1 de verdade via powershell.exe -DryRun (sem SQLcl, sem banco).
Skipa o modulo inteiro se powershell.exe nao existir no PATH (ex.: CI Linux).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run-query.ps1"

POWERSHELL = shutil.which("powershell.exe")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="powershell.exe nao encontrado no PATH"
)

SENTINEL_PWD = "s3ntinel_pwd_87c2f1_nao_deve_vazar"


def run_script(args, cwd, env=None):
    cmd = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
    ] + args
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def base_env(extra=None):
    """Ambiente de processo limpo das 3 chaves PLSQLFLOW_*, com overrides."""
    e = os.environ.copy()
    for k in ("PLSQLFLOW_USER", "PLSQLFLOW_PWD", "PLSQLFLOW_DSN"):
        e.pop(k, None)
    if extra:
        e.update(extra)
    return e


def write_dotenv(path: Path, text: str):
    (path / ".env").write_text(text, encoding="utf-8")


def dummy_sqlfile(path: Path) -> str:
    return str(path / "does_not_exist.sql")


def test_dotenv_three_keys_dry_run(tmp_path):
    write_dotenv(
        tmp_path,
        "PLSQLFLOW_USER=gestao\n"
        "PLSQLFLOW_PWD=" + SENTINEL_PWD + "\n"
        "PLSQLFLOW_DSN=localhost:1521/XEPDB1\n",
    )
    result = run_script(
        ["-Connection", "env", "-SqlFile", dummy_sqlfile(tmp_path), "-DryRun"],
        cwd=tmp_path,
        env=base_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "User: gestao" in result.stdout
    assert "DSN: localhost:1521/XEPDB1" in result.stdout
    assert SENTINEL_PWD not in result.stdout
    assert SENTINEL_PWD not in result.stderr


def test_process_env_wins_over_dotenv(tmp_path):
    write_dotenv(
        tmp_path,
        "PLSQLFLOW_USER=file_user\n"
        "PLSQLFLOW_PWD=" + SENTINEL_PWD + "\n"
        "PLSQLFLOW_DSN=file_dsn:1521/FILEPDB\n",
    )
    result = run_script(
        ["-Connection", "env", "-SqlFile", dummy_sqlfile(tmp_path), "-DryRun"],
        cwd=tmp_path,
        env=base_env({"PLSQLFLOW_USER": "env_user"}),
    )
    assert result.returncode == 0, result.stderr
    assert "User: env_user" in result.stdout
    assert "User: file_user" not in result.stdout
    # DSN nao foi sobrescrita no ambiente, deve vir do .env
    assert "DSN: file_dsn:1521/FILEPDB" in result.stdout
    assert SENTINEL_PWD not in result.stdout
    assert SENTINEL_PWD not in result.stderr


def test_password_never_leaks_when_fully_resolved_from_env_vars(tmp_path):
    result = run_script(
        ["-Connection", "env", "-SqlFile", dummy_sqlfile(tmp_path), "-DryRun"],
        cwd=tmp_path,
        env=base_env(
            {
                "PLSQLFLOW_USER": "gestao",
                "PLSQLFLOW_PWD": SENTINEL_PWD,
                "PLSQLFLOW_DSN": "localhost:1521/XEPDB1",
            }
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "User: gestao" in result.stdout
    assert SENTINEL_PWD not in result.stdout
    assert SENTINEL_PWD not in result.stderr


def test_dotenv_format_quirks(tmp_path):
    write_dotenv(
        tmp_path,
        "# comentario no topo\n"
        "\n"
        "  export PLSQLFLOW_USER = 'gestao'  \n"
        'PLSQLFLOW_PWD="' + SENTINEL_PWD + '"\n'
        "# outro comentario\n"
        "PLSQLFLOW_DSN=localhost:1521/XEPDB1?extra=1\n",
    )
    result = run_script(
        ["-Connection", "env", "-SqlFile", dummy_sqlfile(tmp_path), "-DryRun"],
        cwd=tmp_path,
        env=base_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "User: gestao" in result.stdout
    assert "DSN: localhost:1521/XEPDB1?extra=1" in result.stdout
    assert SENTINEL_PWD not in result.stdout
    assert SENTINEL_PWD not in result.stderr


def test_missing_variable_reports_which_and_fails(tmp_path):
    write_dotenv(
        tmp_path,
        "PLSQLFLOW_USER=gestao\n"
        "PLSQLFLOW_PWD=" + SENTINEL_PWD + "\n",
        # PLSQLFLOW_DSN ausente de proposito
    )
    result = run_script(
        ["-Connection", "env", "-SqlFile", dummy_sqlfile(tmp_path), "-DryRun"],
        cwd=tmp_path,
        env=base_env(),
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "PLSQLFLOW_DSN" in combined
    assert SENTINEL_PWD not in result.stdout
    assert SENTINEL_PWD not in result.stderr


def test_missing_all_three_reports_all(tmp_path):
    result = run_script(
        ["-Connection", "env", "-SqlFile", dummy_sqlfile(tmp_path), "-DryRun"],
        cwd=tmp_path,
        env=base_env(),
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "PLSQLFLOW_USER" in combined
    assert "PLSQLFLOW_PWD" in combined
    assert "PLSQLFLOW_DSN" in combined


def test_non_env_connection_skips_resolution_and_preserves_old_behavior(tmp_path):
    # Nenhuma das 3 chaves definida em lugar nenhum -- se a resolucao por
    # ambiente disparasse por engano, o script falharia com erro de variavel
    # faltando. Como -Connection nao e "env", deve so ecoar o -DryRun antigo.
    result = run_script(
        ["-Connection", "minha_conexao_salva", "-SqlFile", dummy_sqlfile(tmp_path), "-DryRun"],
        cwd=tmp_path,
        env=base_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "Connection: minha_conexao_salva" in result.stdout
    assert SENTINEL_PWD not in result.stdout
    assert SENTINEL_PWD not in result.stderr
