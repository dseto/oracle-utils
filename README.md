# oracle-utils

Toolkit de skills do Claude Code para Oracle Database 19c (devs e DBAs).

## Skills disponíveis

### Tuning / Debug
| Skill | O que faz | Precisa de banco? |
|---|---|---|
| `/sql-tune` | Diagnóstico de performance: plano real (DBMS_XPLAN), stats, binds, histogramas → causa raiz + recomendação | Sim (ou modo offline com plano colado) |
| `/ora-error` | Triagem de erro ORA-XXXXX: causas ordenadas + queries de verificação | Opcional |
| `/trace-analyze` | Interpreta trace 10046 / tkprof: tempo por fase, waits, SQL dominante | Não (offline) |

### Diagnóstico DBA
| Skill | O que faz | Precisa de banco? |
|---|---|---|
| `/ora-health` | Health check com semáforo: tablespaces, FRA, inválidos, jobs, bloqueios, waits, memória | Sim |
| `/lock-detective` | Árvore de bloqueio, decodificação de locks, script KILL para revisão | Sim |
| `/alert-triage` | Triagem do alert log: agrupamento ORA-, rajadas, correlação | Opcional (V$DIAG_ALERT_EXT ou arquivo) |
| `/awr-analyze` | Interpreta relatório AWR/Statspack fornecido (offline; gerar AWR exige Diagnostics Pack) | Não |
| `/ash-live` | Atividade recente via ASH (Diagnostics Pack) ou amostragem de v$session | Sim |

### Visuais
| Skill | O que faz | Precisa de banco? |
|---|---|---|
| `/erd` | Diagrama ER mermaid do schema (PK/UK/FK, cardinalidade) | Sim |
| `/dep-graph` | Grafo de dependências para análise de impacto (quem usa X / X usa o quê) | Sim |
| `/db-dashboard` | Dashboard HTML de snapshot (Artifact single-file) | Sim |

### PL/SQL
| Skill | O que faz | Precisa de banco? |
|---|---|---|
| `/plsql-review` | Review com checklist: segurança, robustez, performance, manutenibilidade | Opcional (arquivo ou all_source) |
| `/plsql-test` | Gera testes utPLSQL v3 para package/procedure | Opcional |

### Schema / Migração
| Skill | O que faz | Precisa de banco? |
|---|---|---|
| `/schema-diff` | Compara schemas (objetos, colunas, constraints, índices) + script ALTER comentado | Sim |
| `/ddl-review` | Review de migração DDL: locks, rollback, performance de deploy → veredito | Não |

## Subagentes (`.claude/agents/`)

- `oracle-dba` — diagnóstico read-only de estado do banco.
- `sql-tuner` — tuning de query específica (fluxo /sql-tune).
- `plsql-reviewer` — review PL/SQL (checklist /plsql-review).

## Conexão

Primária: SQLcl MCP server (`tools\sqlcl\bin\sql.exe -mcp`), registrado no Claude Code.
Fallback: `scripts\run-query.ps1 -Connection dev -SqlFile sql\tune\sql_stats.sql`.

Conexões salvas no SQLcl: usar `conn -save dev -savepwd ...` dentro do SQLcl (aliases `dev`, `hml`).

## Backlog (fases futuras)

Diagnóstico DBA (/ora-health, /lock-detective, /alert-triage, /awr-analyze, /ash-live), visuais (/erd, /dep-graph, /db-dashboard), PL/SQL (/plsql-review, /plsql-test), schema (/schema-diff, /ddl-review). Ver plano em `.claude/plans` da sessão original.

## Segurança

Skills são read-only (SELECT/EXPLAIN). Qualquer DDL/DML sugerido é entregue como script para revisão humana — nunca executado automaticamente. Ver [CLAUDE.md](CLAUDE.md).
