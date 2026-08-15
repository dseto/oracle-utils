# oracle-utils

Toolkit de skills Claude Code para desenvolvedores e DBAs Oracle Database 19c.

## Conexão com o banco

- Método primário: **SQLcl MCP server** (`sql -mcp`, SQLcl >= 25.2). Ferramentas MCP: listar conexões salvas, executar SQL, etc.
- Conexões salvas no SQLcl via `connmgr` (ou `conn -save`). Aliases esperados: `dev`, `hml`.
- Conexão `dev` = Oracle XE **21c** local (`localhost:1521/XEPDB1`, schema `gestao`). Bancos-alvo reais incluem **19c** — o 21c local é só ambiente de teste.
- **Compatibilidade dupla 19c/21c**: antes de recomendação específica de release, conferir versão da conexão ativa (`v$instance.version_full`). Nenhuma query ou sugestão pode usar sintaxe/feature acima de 19c (baseline). Features 21c-only (ex.: JSON nativo binário default, SQL macros, JavaScript MLE) nunca em scripts do repo.
- **Não há conexão de produção nesta máquina.** Se um alias novo aparecer, confirmar ambiente antes de usar.
- SQLcl e JDK são portáteis do repo: `tools\sqlcl`, `tools\jdk`. Processos SQLcl precisam de `JAVA_HOME=C:\Projetos\oracle-utils\tools\jdk` (o wrapper `scripts\run-query.ps1` configura sozinho).

## Regras de segurança (guarda leve)

- Skills de tuning/diagnóstico executam apenas `SELECT`, `EXPLAIN PLAN` e chamadas read-only a `DBMS_XPLAN`/views `V$`/`DBA_`/`ALL_`.
- Nunca executar DML (INSERT/UPDATE/DELETE/MERGE) ou DDL (CREATE/ALTER/DROP/TRUNCATE) sem confirmação explícita do usuário na conversa.
- Sugestões de índice, hint, profile ou coleta de estatísticas são entregues como script para o usuário revisar — nunca aplicadas automaticamente.
- Não usar `GATHER_TABLE_STATS` ou `DBMS_SQLTUNE` que altere estado sem confirmação.

## Estrutura

```
.claude/skills/   15 skills: tuning (sql-tune, ora-error, trace-analyze),
                  diagnostico DBA (ora-health, lock-detective, alert-triage,
                  awr-analyze, ash-live), visuais (erd, dep-graph, db-dashboard),
                  PL/SQL (plsql-review, plsql-test), schema (schema-diff, ddl-review)
.claude/agents/   subagentes: oracle-dba, sql-tuner, plsql-reviewer
sql/              biblioteca de queries reutilizáveis (tune/, health/, locks/, viz/, schema/)
scripts/          wrappers PowerShell 5.1 (fallback sem MCP)
```

## Convenções de queries em sql/

- Compatível Oracle 19c; sem sintaxe 21c+ (ex.: sem `GROUP BY` alias, sem SQL macros).
- Bind variables com prefixo `:` documentadas em comentário no topo do arquivo.
- Usar `ALL_`/`DBA_` conforme privilégio; queries tentam `DBA_` e caem para `ALL_` se ORA-00942.

## Ambiente da máquina

- Windows 11, PowerShell 5.1 apenas (sem pwsh). Scripts `.ps1` em sintaxe PS 5.1.
- Nunca embutir caracteres não-ASCII em `.ps1` (ver instruções globais do usuário).
