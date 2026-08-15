# Plano de tarefas: plsql-flow

## [T-01] DBA consegue consultar toda a base estática do grafo (chamadas, triggers, tipos, sinônimos, overloads) com queries prontas da biblioteca
- files: `sql/flow/resolve_target.sql`, `sql/flow/plscope_check.sql`, `sql/flow/plscope_calls.sql`, `sql/flow/plscope_statements.sql`, `sql/flow/fetch_source.sql`, `sql/flow/deps_direct.sql`, `sql/flow/triggers_for_tables.sql`, `sql/flow/fk_cascade.sql`, `sql/flow/type_hierarchy.sql`, `sql/flow/resolve_synonym.sql`, `tests/test_plsql_flow.py`
- verify: `pytest tests/test_plsql_flow.py -q -k queries`

## [T-02] Usuário invoca /plsql-flow com procedure+parâmetros e recebe diagrama mermaid do caminho completo, com casos complexos marcados e proteção anti-loop
- files: `.claude/skills/plsql-flow/SKILL.md`, `tests/test_plsql_flow.py`
- verify: `pytest tests/test_plsql_flow.py -q -k skill_doc`
- depends: T-01

## [T-03] Ambiente dev tem package de demonstração FLOW_DEMO cobrindo recursão mútua, SQL dinâmico, trigger e overload para validar a skill
- files: `sql/flow/fixture_flow_demo.sql`, `tests/test_plsql_flow.py`
- verify: `pytest tests/test_plsql_flow.py -q -k fixture`
- depends: T-01

## [T-04] Usuário pode ativar modo dinâmico (DBMS_HPROF) e ver o caminho realmente executado sobreposto ao grafo estático
- files: `sql/flow/hprof_setup.sql`, `sql/flow/hprof_report.sql`, `.claude/skills/plsql-flow/SKILL.md`, `tests/test_plsql_flow.py`
- verify: `pytest tests/test_plsql_flow.py -q -k hprof`
- depends: T-02

## [T-05] Skill validada de ponta a ponta contra FLOW_DEMO: ciclo detectado sem travar, trigger e SQL dinâmico no diagrama, overload resolvido, evidência gravada
- files: `.harness/scratch/plsql-flow-evidence.md`, `tests/test_plsql_flow.py`
- verify: `pytest tests/test_plsql_flow.py -q -k e2e_evidence`
- depends: T-02, T-03
