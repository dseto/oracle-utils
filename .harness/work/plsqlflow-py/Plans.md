# Plans — plsqlflow-py

## [T-01] Pacote conecta no Oracle em modo thin e extrai o dicionario de forma tipada e somente-leitura
- files: `plsqlflow/__init__.py`, `plsqlflow/db.py`, `plsqlflow/queries.py`, `plsqlflow/extract.py`, `tests/test_plsqlflow_unit.py`, `tests/conftest.py`
- verify: `pytest tests/test_plsqlflow_unit.py -q`

## [T-02] Diagrama reflete o caminho real de execucao: recursao marcada sem loop, overload certo, triggers e cascata FK incluidos, subtipos OVERRIDING como candidatos
- files: `plsqlflow/resolve.py`, `plsqlflow/graph.py`, `tests/test_plsqlflow_graph.py`, `tests/fixtures/flow_demo_extract.json`
- verify: `pytest tests/test_plsqlflow_graph.py -q`
- depends: T-01

## [T-03] SQL dinamico com literais e constantes vira aresta resolvida; montado em variavel vira ponto cego explicito; fallback sem PL/Scope entrega candidatos com nivel de confianca
- files: `plsqlflow/dynsql.py`, `plsqlflow/lexical.py`, `tests/test_plsqlflow_lexical.py`
- verify: `pytest tests/test_plsqlflow_lexical.py -q`
- depends: T-01

## [T-04] Mesmo alvo gera sempre o mesmo diagrama: CLI produz mermaid+JSON e o resultado do FLOW_DEMO bate byte a byte com o golden file
- files: `plsqlflow/mermaid.py`, `plsqlflow/report.py`, `plsqlflow/cli.py`, `plsqlflow/__main__.py`, `tests/test_plsqlflow_golden.py`, `tests/fixtures/flow_demo_golden.mmd`
- verify: `pytest tests/test_plsqlflow_golden.py -q`
- depends: T-02, T-03

## [T-05] Skill /plsql-flow passa a usar o script primeiro e reserva o assistente ao residual, com evidencia real contra o banco dev
- files: `.claude/skills/plsql-flow/SKILL.md`, `tests/test_plsqlflow_skill.py`
- verify: `pytest tests/test_plsqlflow_skill.py -q -rs`
- depends: T-04
