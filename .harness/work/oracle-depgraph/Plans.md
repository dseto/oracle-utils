# Plans: oracle-depgraph

## [T-01] Catalogo de objetos e colunas de tabela ficam disponiveis ao extrator, com data de ultimo DDL para detectar grafo desatualizado
- files: `sql/flow/object_catalog.sql`, `sql/flow/tab_columns.sql`, `plsqlflow/queries.py`, `plsqlflow/extract.py`, `plsqlflow/depgraph.py`, `tests/test_depgraph_unit.py`, `tests/test_plsqlflow_unit.py`
- verify: `pytest tests/test_depgraph_unit.py tests/test_conventions.py -q`

## [T-02] Percorrer as dependencias a partir da raiz visita cada objeto uma unica vez, para nas fronteiras do sistema e avisa quando atinge o limite de tamanho em vez de truncar calado
- files: `plsqlflow/depgraph.py`, `tests/test_depgraph_bfs.py`, `tests/fixtures/depgraph_extract.json`
- verify: `pytest tests/test_depgraph_bfs.py -q`
- depends: T-01

## [T-03] Cada acesso a tabela vira aresta de leitura ou escrita com linha do fonte, e todo SQL dinamico aparece classificado como resolvido, parcial ou opaco
- files: `plsqlflow/depgraph_enrich.py`, `plsqlflow/lexical.py`, `tests/test_depgraph_enrich.py`, `tests/test_plsqlflow_lexical.py`
- verify: `pytest tests/test_depgraph_enrich.py tests/test_plsqlflow_lexical.py -q`
- depends: T-01

## [T-04] Triggers das tabelas escritas entram no grafo com suas proprias dependencias, e regerar o grafo contra o mesmo banco produz arquivos identicos byte a byte
- files: `plsqlflow/depgraph.py`, `plsqlflow/depgraph_render.py`, `tests/test_depgraph_triggers.py`, `tests/test_depgraph_render.py`, `tests/fixtures/depgraph_golden/`
- verify: `pytest tests/test_depgraph_triggers.py tests/test_depgraph_render.py -q`
- depends: T-02, T-03

## [T-05] Gerar o grafo pela linha de comando funciona sem quebrar o uso atual do pacote, nunca recebe senha por argumento e sinaliza cada situacao com um codigo de saida proprio
- files: `plsqlflow/cli.py`, `tests/test_depgraph_cli.py`, `sql/flow/triggers_any_status.sql`, `plsqlflow/queries.py`, `plsqlflow/extract.py`
- verify: `pytest tests/test_depgraph_cli.py -q`
- depends: T-04

## [T-06] O assistente sabe consultar o grafo por grep antes de reabrir conexao, e as skills e agentes existentes apontam para ele
- files: `.claude/skills/oracle-dependency-graph/SKILL.md`, `.claude/skills/dep-graph/SKILL.md`, `.claude/skills/plsql-flow/SKILL.md`, `.claude/skills/plsql-review/SKILL.md`, `.claude/agents/oracle-dba.md`, `.gitignore`, `tests/test_depgraph_skill.py`, `tests/test_plsql_flow.py`, `tests/conftest.py`
- verify: `pytest -q -rs`
- depends: T-05
