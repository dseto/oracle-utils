# Plans: dotenv-conn

Ordem: T-01 e T-02 em paralelo (Python vs PowerShell, superficies
disjuntas). T-03 fecha (conftest + docs + suite completa).

Decisoes fixadas no spec: carga lazy em `resolve_connection_params`, so
quando `env` nao e injetado; `find_dotenv(usecwd=True)`; `override=False`
(ambiente real ganha); o `load_dotenv()` interino em tempo de import sai.
No PS 5.1: `-Connection env`, parser de `.env` proprio (ASCII no codigo),
credencial via linha `connect` no script temporario (nunca na linha de
comando do processo), `-DryRun` imprime usuario/DSN e nunca senha.

## [T-01] CLI flow/depgraph le credenciais do .env do diretorio onde e invocado, ambiente real ganha do arquivo
- files: `plsqlflow/db.py`, `tests/test_db_dotenv.py`
- verify: `pytest tests/test_db_dotenv.py tests/test_plsqlflow_unit.py -q`

## [T-02] run-query.ps1 aceita -Connection env e resolve credenciais de variaveis de ambiente ou .env, sem expor a senha
- files: `scripts/run-query.ps1`, `tests/test_run_query_env.py`
- verify: `pytest tests/test_run_query_env.py -q`

## [T-03] Testes live rodam sozinhos em maquina com .env preenchido, e a documentacao das skills ensina o formato
- files: `tests/conftest.py`, `.claude/skills/plsql-flow/SKILL.md`, `.claude/skills/oracle-dependency-graph/SKILL.md`, `CLAUDE.md`, `.gitignore`
- verify: `pytest -q -rs`
- depends: T-01, T-02
