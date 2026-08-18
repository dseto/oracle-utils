from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

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
class PlscopeStatementBatchRow:
    # Irma em lote de PlscopeStatementRow (contrato oracle-depgraph-scale,
    # T-01): mesmos campos, mais object_name para reagrupar por objeto de
    # origem depois do fetch em lote (plscope_statements_batch.sql).
    object_name: str
    line: int
    stmt_type: str
    sql_id: Optional[str]
    has_into_record: Optional[str]
    text: Optional[str]
    # Campos novos (contrato depgraph-granular, T-02) -- acrescentados no
    # FIM, todos com default, de proposito: nenhum consumidor existente
    # constroi esta dataclass posicionalmente (todos os usos em cli.py e nos
    # testes sao por keyword), entao um campo novo com default nao quebra
    # nada que ja compila. usage_id/usage_context_id sao o elo desta linha
    # com a arvore de PlscopeTreeRow (mesmo usage_id/usage_context_id,
    # arvore unica); object_type distingue PACKAGE de PACKAGE BODY, que
    # compartilham o mesmo espaco de usage_id.
    object_type: Optional[str] = None
    usage_id: Optional[int] = None
    usage_context_id: Optional[int] = None


@dataclass
class PlscopeTreeRow:
    # Arvore crua de ALL_IDENTIFIERS em lote (contrato depgraph-granular,
    # T-02, plscope_tree_batch.sql): fundacao da atribuicao por subprograma
    # de plsqlflow/attribute.py (T-01). object_type e obrigatorio (nao so
    # metadado): PACKAGE e PACKAGE BODY sao arvores separadas que
    # compartilham o mesmo espaco de usage_id.
    object_name: str
    object_type: str
    usage_id: int
    usage_context_id: Optional[int]
    line: int
    col: int
    name: str
    type: str
    usage: str
    signature: Optional[str]


@dataclass
class FetchSourceRow:
    type: str
    line: int
    text: str


@dataclass
class FetchSourceBatchRow:
    # Irma em lote de FetchSourceRow: mesmos campos, mais name (fetch_source_batch.sql).
    name: str
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
class DepsDirectBatchRow:
    # Irma em lote de DepsDirectRow: mesmos campos, mais name (o objeto de
    # origem de cada linha -- deps_direct_batch.sql).
    name: str
    referenced_owner: str
    referenced_name: str
    referenced_type: str
    dependency_type: str


@dataclass
class DependentesBatchRow:
    # Inverso de DepsDirectBatchRow (contrato dynsql-dossie, T-05,
    # dependentes_batch.sql): owner/name aqui sao o DEPENDENTE, nao a
    # origem -- referenced_name e qual objeto do lote (:object_list) ele
    # referencia. Grao OBJETO: ALL_DEPENDENCIES nao tem granularidade de
    # subprograma, entao esta linha nao diz QUAL subprograma do dependente
    # chama, so QUE o objeto dependente existe e aponta pra ca.
    owner: str
    name: str
    type: str
    referenced_name: str
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


# ---------------------------------------------------------- queries batch
#
# As tres queries batch (contrato oracle-depgraph-scale, T-01) sao carregadas
# pelo MESMO registro central de todas as outras (queries.QUERY_NAMES /
# QUERY_TEXT) -- nada de loader paralelo aqui: um segundo caminho de
# carregamento significaria duas regras de validacao, e uma query com nome
# errado falharia so em runtime em vez de na importacao do modulo.

# Bind :object_list e VARCHAR2 -- uma lista de nomes longa demais estoura o
# limite do bind. 200 nomes fica bem folgado abaixo disso (nome de objeto
# Oracle raramente passa de umas poucas dezenas de bytes) e mantem a
# consulta com um plano de execucao plano e estavel -- uma string enorme
# dentro do INSTR tambem degrada. Quem fatia a lista de nomes em chunks e o
# CHAMADOR (BFS por nivel / enriquecimento em lote, T-02/T-03, fora desta
# tarefa); a constante mora aqui, junto dos fetchers que ela limita.
BATCH_CHUNK_SIZE = 200


def chunk_names(names: Iterable[str], size: int = BATCH_CHUNK_SIZE) -> List[str]:
    """Normaliza `names` (maiusculas, sem espaco nas pontas, sem entrada
    vazia, sem duplicata, ordenado) e devolve os pedacos ja prontos para o
    bind `:object_list` -- cada pedaco e uma unica string `'A,B,C'` com no
    maximo `size` nomes.

    Ordenacao e deduplicacao sao deliberadas: este projeto e
    byte-deterministico, e o mesmo conjunto de nomes (em qualquer ordem de
    entrada) tem que produzir sempre os mesmos chunks."""
    unique_sorted = sorted({n.strip().upper() for n in names if n and n.strip()})
    return [
        ",".join(unique_sorted[i : i + size])
        for i in range(0, len(unique_sorted), size)
    ]


def fetch_deps_direct_batch(
    conn, owner: str, object_list: Optional[str]
) -> List[DepsDirectBatchRow]:
    binds = {"owner": owner, "object_list": object_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["deps_direct_batch.sql"], binds)
    return [DepsDirectBatchRow(**_to_kwargs(row)) for row in rows]


def fetch_plscope_statements_batch(
    conn, owner: str, object_list: Optional[str]
) -> List[PlscopeStatementBatchRow]:
    binds = {"owner": owner, "object_list": object_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["plscope_statements_batch.sql"], binds)
    return [PlscopeStatementBatchRow(**_to_kwargs(row)) for row in rows]


def fetch_plscope_tree_batch(
    conn, owner: str, object_list: Optional[str]
) -> List[PlscopeTreeRow]:
    # Mesmo padrao de fetch_plscope_statements_batch/fetch_deps_direct_batch
    # acima: le a query central (queries.QUERY_TEXT), binda :owner/
    # :object_list, tipa cada linha na dataclass correspondente. O chamador
    # (plsqlflow/attribute.py, T-01) e quem fatia object_names grande com
    # chunk_names -- esta funcao so faz UMA chamada.
    binds = {"owner": owner, "object_list": object_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["plscope_tree_batch.sql"], binds)
    return [PlscopeTreeRow(**_to_kwargs(row)) for row in rows]


def fetch_source_batch(
    conn, owner: str, object_list: Optional[str], object_type: Optional[str] = None
) -> List[FetchSourceBatchRow]:
    binds = {"owner": owner, "object_list": object_list, "object_type": object_type}
    rows = db.run_query(conn, queries.QUERY_TEXT["fetch_source_batch.sql"], binds)
    return [FetchSourceBatchRow(**_to_kwargs(row)) for row in rows]


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


def fetch_dependentes_batch(
    conn, owner: str, object_list: Optional[str]
) -> List[DependentesBatchRow]:
    """Quem DEPENDE dos objetos de `object_list` (dono `owner`) -- inverso de
    `fetch_deps_direct_batch` (dependentes_batch.sql, contrato dynsql-dossie,
    T-05). Grao OBJETO, visibilidade limitada ao usuario atual
    (ALL_DEPENDENCIES, nao DBA_). Usada como SINALIZADOR de chamadores fora
    do fechamento do mapa (docs/backlog-sql-dinamico-estatico.md, secao
    4.3.1) -- nunca como enumeracao completa de chamadores; quem calcula o
    sinalizador a partir do resultado e `dynsite_origin.attach_call_sites`
    (funcao pura, sem conexao)."""
    binds = {"owner": owner, "object_list": object_list}
    rows = db.run_query(conn, queries.QUERY_TEXT["dependentes_batch.sql"], binds)
    return [DependentesBatchRow(**_to_kwargs(row)) for row in rows]


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
