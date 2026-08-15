from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import db, queries


@dataclass
class ResolveTargetRow:
    object_name: str
    procedure_name: Optional[str]
    subprogram_id: Optional[int]
    overload: Optional[str]
    position: Optional[int]
    argument_name: Optional[str]
    in_out: Optional[str]
    data_type: Optional[str]
    type_owner: Optional[str]
    type_name: Optional[str]


@dataclass
class PlscopeCheckRow:
    owner: str
    name: str
    type: str
    plscope_settings: Optional[str]


@dataclass
class PlscopeCallRow:
    line: int
    col: int
    called_name: str
    calling_object: str
    decl_owner: str
    decl_object: str
    decl_object_type: str
    decl_type: str
    decl_line: int


@dataclass
class PlscopeStatementRow:
    line: int
    stmt_type: str
    sql_id: Optional[str]
    has_into_record: Optional[str]
    text: Optional[str]


@dataclass
class FetchSourceRow:
    type: str
    line: int
    text: str


@dataclass
class DepsDirectRow:
    referenced_owner: str
    referenced_name: str
    referenced_type: str
    dependency_type: str


@dataclass
class TriggerRow:
    table_owner: str
    table_name: str
    trigger_name: str
    trigger_type: str
    triggering_event: str
    when_clause: Optional[str]
    status: str


@dataclass
class FkCascadeRow:
    child_owner: str
    child_table: str
    constraint_name: str
    delete_rule: str


@dataclass
class TypeHierarchyRow:
    type_name: str
    supertype_name: Optional[str]
    final: Optional[str]
    instantiable: Optional[str]
    method_name: Optional[str]
    method_no: Optional[int]
    method_type: Optional[str]
    overriding: Optional[str]
    inherited: Optional[str]


@dataclass
class SynonymRow:
    synonym_owner: str
    synonym_name: str
    base_owner: Optional[str]
    base_name: Optional[str]
    db_link: Optional[str]


@dataclass
class ObjectCatalogRow:
    owner: str
    object_name: str
    object_type: str
    status: str
    last_ddl_time: Any


@dataclass
class TabColumnRow:
    owner: str
    table_name: str
    column_name: str
    column_id: int
    data_type: str
    nullable: str
    data_default: Optional[str]
    # Precisao do tipo: sem eles o no de tabela renderiza "VARCHAR2" em vez de
    # "VARCHAR2(200)" -- mesma informacao que sql/viz/erd_tables.sql ja traz.
    data_length: Optional[int] = None
    data_precision: Optional[int] = None
    data_scale: Optional[int] = None
    char_used: Optional[str] = None


def _to_kwargs(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key.lower(): value for key, value in row.items()}


def fetch_resolve_target(
    conn, owner: str, object_name: str, subprogram: Optional[str] = None
) -> List[ResolveTargetRow]:
    binds = {"owner": owner, "object_name": object_name, "subprogram": subprogram}
    rows = db.run_query(conn, queries.QUERY_TEXT["resolve_target.sql"], binds)
    return [ResolveTargetRow(**_to_kwargs(row)) for row in rows]


def fetch_plscope_check(
    conn, owner: str, object_list: Optional[str] = None
) -> List[PlscopeCheckRow]:
    binds = {"owner": owner, "object_list": object_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["plscope_check.sql"], binds)
    return [PlscopeCheckRow(**_to_kwargs(row)) for row in rows]


def fetch_plscope_calls(conn, owner: str, object_name: str) -> List[PlscopeCallRow]:
    binds = {"owner": owner, "object_name": object_name}
    rows = db.run_query(conn, queries.QUERY_TEXT["plscope_calls.sql"], binds)
    return [PlscopeCallRow(**_to_kwargs(row)) for row in rows]


def fetch_plscope_statements(conn, owner: str, object_name: str) -> List[PlscopeStatementRow]:
    binds = {"owner": owner, "object_name": object_name}
    rows = db.run_query(conn, queries.QUERY_TEXT["plscope_statements.sql"], binds)
    return [PlscopeStatementRow(**_to_kwargs(row)) for row in rows]


def fetch_source(
    conn, owner: str, object_name: str, object_type: Optional[str] = None
) -> List[FetchSourceRow]:
    binds = {"owner": owner, "object_name": object_name, "object_type": object_type}
    rows = db.run_query(conn, queries.QUERY_TEXT["fetch_source.sql"], binds)
    return [FetchSourceRow(**_to_kwargs(row)) for row in rows]


def fetch_deps_direct(conn, owner: str, object_name: str) -> List[DepsDirectRow]:
    binds = {"owner": owner, "object_name": object_name}
    rows = db.run_query(conn, queries.QUERY_TEXT["deps_direct.sql"], binds)
    return [DepsDirectRow(**_to_kwargs(row)) for row in rows]


def fetch_triggers_for_tables(conn, owner: str, table_list: str) -> List[TriggerRow]:
    binds = {"owner": owner, "table_list": table_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["triggers_for_tables.sql"], binds)
    return [TriggerRow(**_to_kwargs(row)) for row in rows]


def fetch_triggers_any_status(conn, owner: str, table_list: str) -> List[TriggerRow]:
    """Mesma forma de `fetch_triggers_for_tables`, mas sem o filtro
    `status = 'ENABLED'` -- usada pelo `plsqlflow depgraph` (T-05), que
    precisa registrar triggers desabilitados como no do grafo (dependencia
    estrutural real), diferente do modo flow (grao subprograma), que so
    quer o que dispara em runtime."""
    binds = {"owner": owner, "table_list": table_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["triggers_any_status.sql"], binds)
    return [TriggerRow(**_to_kwargs(row)) for row in rows]


def fetch_fk_cascade(conn, owner: str, table_name: str) -> List[FkCascadeRow]:
    binds = {"owner": owner, "table_name": table_name}
    rows = db.run_query(conn, queries.QUERY_TEXT["fk_cascade.sql"], binds)
    return [FkCascadeRow(**_to_kwargs(row)) for row in rows]


def fetch_type_hierarchy(conn, owner: str, type_name: str) -> List[TypeHierarchyRow]:
    binds = {"owner": owner, "type_name": type_name}
    rows = db.run_query(conn, queries.QUERY_TEXT["type_hierarchy.sql"], binds)
    return [TypeHierarchyRow(**_to_kwargs(row)) for row in rows]


def fetch_resolve_synonym(conn, owner: str, name: str) -> List[SynonymRow]:
    binds = {"owner": owner, "name": name}
    rows = db.run_query(conn, queries.QUERY_TEXT["resolve_synonym.sql"], binds)
    return [SynonymRow(**_to_kwargs(row)) for row in rows]


def fetch_object_catalog(
    conn, owner: str, object_list: Optional[str] = None
) -> List[ObjectCatalogRow]:
    binds = {"owner": owner, "object_list": object_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["object_catalog.sql"], binds)
    return [ObjectCatalogRow(**_to_kwargs(row)) for row in rows]


def fetch_tab_columns(conn, owner: str, table_list: str) -> List[TabColumnRow]:
    binds = {"owner": owner, "table_list": table_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["tab_columns.sql"], binds)
    return [TabColumnRow(**_to_kwargs(row)) for row in rows]
