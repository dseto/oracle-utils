"""Testes offline (T-01, contrato dotenv-conn) para a carga lazy de .env em
plsqlflow/db.py.

Cobre: .env do diretorio corrente do consumidor e lido; ambiente real ganha
do arquivo; chamador que injeta `env` explicito fica imune ao dotenv; alias
tambem se beneficia do .env (PLSQLFLOW_PWD_<ALIAS>); ausencia de .env nao
quebra nada. Nao abre conexao real -- so bind de PLSQLFLOW_* em .env
temporario (tmp_path) e monkeypatch.chdir, sem tocar no .env real do repo.
"""
from __future__ import annotations

import json

import pytest

from plsqlflow import db

# Variaveis que o dotenv/ambiente real podem preencher -- limpas antes de
# cada caso que exige ambiente vazio, porque a maquina que roda a suite tem
# essas variaveis definidas de verdade (fora do controle do teste) e um
# teste que assume ambiente vazio sem limpar passa ou falha por acidente.
PLSQLFLOW_VARS = (
    "PLSQLFLOW_USER",
    "PLSQLFLOW_PWD",
    "PLSQLFLOW_DSN",
    "PLSQLFLOW_PWD_DEV",
)


def _clean_env(monkeypatch):
    for var in PLSQLFLOW_VARS:
        monkeypatch.delenv(var, raising=False)


def test_dotenv_in_cwd_is_read(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    (tmp_path / ".env").write_text(
        "PLSQLFLOW_USER=gestao\n"
        "PLSQLFLOW_PWD=from_dotenv\n"
        "PLSQLFLOW_DSN=localhost:1521/XEPDB1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    params = db.resolve_connection_params(alias=None, env=None)

    assert params.user == "gestao"
    assert params.password == "from_dotenv"
    assert params.dsn == "localhost:1521/XEPDB1"


def test_real_env_wins_over_dotenv_value(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    (tmp_path / ".env").write_text(
        "PLSQLFLOW_USER=gestao\n"
        "PLSQLFLOW_PWD=from_dotenv\n"
        "PLSQLFLOW_DSN=localhost:1521/XEPDB1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # variavel ja definida no ambiente real -- deve ganhar do arquivo
    monkeypatch.setenv("PLSQLFLOW_PWD", "from_real_env")

    params = db.resolve_connection_params(alias=None, env=None)

    assert params.password == "from_real_env"


def test_injected_env_never_reads_dotenv(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    (tmp_path / ".env").write_text(
        "PLSQLFLOW_USER=gestao\n"
        "PLSQLFLOW_PWD=from_dotenv\n"
        "PLSQLFLOW_DSN=localhost:1521/XEPDB1\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    # chamador injeta env explicito (vazio) -- nao deve ler o .env valido
    # que esta no cwd, mesmo que ele resolveria a conexao com sucesso.
    with pytest.raises(db.ConnectionConfigError, match="Configuracao de conexao ausente"):
        db.resolve_connection_params(alias=None, env={})

    # prova adicional de hermeticidade: nada vazou pro ambiente real
    import os

    assert "PLSQLFLOW_USER" not in os.environ
    assert "PLSQLFLOW_PWD" not in os.environ
    assert "PLSQLFLOW_DSN" not in os.environ


def test_alias_password_from_dotenv(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    (tmp_path / ".env").write_text("PLSQLFLOW_PWD_DEV=from_dotenv_alias\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config_path = tmp_path / "flow-connections.json"
    config_path.write_text(
        json.dumps({"dev": {"user": "gestao", "dsn": "localhost:1521/XEPDB1"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(db, "CONFIG_PATH", config_path)

    params = db.resolve_connection_params(alias="dev", env=None)

    assert params.user == "gestao"
    assert params.dsn == "localhost:1521/XEPDB1"
    assert params.password == "from_dotenv_alias"


def test_no_dotenv_present_does_not_break(monkeypatch, tmp_path):
    _clean_env(monkeypatch)
    monkeypatch.chdir(tmp_path)  # tmp_path sem .env nenhum

    with pytest.raises(db.ConnectionConfigError, match="Configuracao de conexao ausente"):
        db.resolve_connection_params(alias=None, env=None)
