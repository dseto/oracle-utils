from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import oracledb
from dotenv import find_dotenv, load_dotenv

CONFIG_PATH = Path(__file__).resolve().parent.parent / "tools" / "flow-connections.json"

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$#]+$")


class ConnectionConfigError(RuntimeError):
    pass


@dataclass
class ConnectionParams:
    user: str
    password: str
    dsn: str


def _validate_identifier(value: Optional[str], label: str) -> str:
    if not value or not IDENTIFIER_RE.match(value):
        raise ValueError(
            "{} invalido: {!r} (esperado apenas letras, numeros, _, $ ou #)".format(label, value)
        )
    return value


def _resolve_from_alias(alias: str, env: Dict[str, str]) -> ConnectionParams:
    _validate_identifier(alias, "alias")
    if not CONFIG_PATH.exists():
        raise ConnectionConfigError(
            "Config de conexao ausente: {}. Formato esperado: ".format(CONFIG_PATH)
            + '{"dev": {"user": "gestao", "dsn": "host:port/service"}}'
        )
    try:
        config: Dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConnectionConfigError(
            "Config de conexao invalida (JSON malformado): {}".format(CONFIG_PATH)
        ) from exc

    entry = config.get(alias)
    if entry is None:
        raise ConnectionConfigError(
            "Alias {!r} nao encontrado em {}. Aliases disponiveis: {}".format(
                alias, CONFIG_PATH, sorted(config)
            )
        )

    user = entry.get("user")
    dsn = entry.get("dsn")
    if not user or not dsn:
        raise ConnectionConfigError(
            "Config de conexao para alias {!r} incompleta (precisa de 'user' e 'dsn')".format(alias)
        )
    _validate_identifier(user, "user")

    pwd_var = "PLSQLFLOW_PWD_{}".format(alias.upper())
    password = env.get(pwd_var)
    if not password:
        raise ConnectionConfigError(
            "Variavel de ambiente {} nao definida (senha do alias {!r})".format(pwd_var, alias)
        )

    return ConnectionParams(user=user, password=password, dsn=dsn)


def _resolve_from_env(env: Dict[str, str]) -> Optional[ConnectionParams]:
    user = env.get("PLSQLFLOW_USER")
    password = env.get("PLSQLFLOW_PWD")
    dsn = env.get("PLSQLFLOW_DSN")
    if user and password and dsn:
        _validate_identifier(user, "user")
        return ConnectionParams(user=user, password=password, dsn=dsn)
    return None


def resolve_connection_params(
    alias: Optional[str] = None, env: Optional[Dict[str, str]] = None
) -> ConnectionParams:
    if env is None:
        # carga lazy: so mexe no processo quando o chamador NAO injetou env
        # explicito. Chamador que injeta dict (toda a suite hermetica) fica
        # 100% imune -- nenhum arquivo lido, nenhum os.environ tocado.
        # usecwd=True busca o .env a partir do diretorio corrente de quem
        # invocou o CLI, subindo -- nao relativo a este pacote. override=
        # False: variavel ja definida no ambiente real sempre ganha do
        # arquivo. Roda antes do dispatch por alias/direto para que
        # PLSQLFLOW_PWD_<ALIAS> vindo do .env tambem funcione.
        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            load_dotenv(dotenv_path, override=False)
        env = os.environ

    if alias:
        return _resolve_from_alias(alias, env)

    params = _resolve_from_env(env)
    if params is not None:
        return params

    raise ConnectionConfigError(
        "Configuracao de conexao ausente. Use um alias (tools/flow-connections.json + "
        "PLSQLFLOW_PWD_<ALIAS>) ou defina PLSQLFLOW_USER/PLSQLFLOW_PWD/PLSQLFLOW_DSN."
    )


def connect(alias: Optional[str] = None, env: Optional[Dict[str, str]] = None):
    params = resolve_connection_params(alias=alias, env=env)
    return oracledb.connect(user=params.user, password=params.password, dsn=params.dsn)


def _first_statement_word(sql_text: str) -> str:
    for line in sql_text.splitlines():
        code = line.split("--", 1)[0].strip()
        if code:
            return code.upper().split()[0]
    return ""


def run_query(conn, sql_text: str, binds: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    if _first_statement_word(sql_text) not in ("SELECT", "WITH"):
        raise ValueError("run_query so aceita SELECT/WITH (somente leitura)")

    binds = binds or {}
    # sql/flow/*.sql segue convencao SQLcl/SQL*Plus (";" final) -- o driver
    # python-oracledb rejeita esse ";" num SELECT solto com ORA-00933.
    sql_text = sql_text.rstrip()
    if sql_text.endswith(";"):
        sql_text = sql_text[:-1]
    cursor = conn.cursor()
    try:
        cursor.execute(sql_text, binds)
        columns = [col[0] for col in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        cursor.close()
