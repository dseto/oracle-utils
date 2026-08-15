---
name: ora-health
description: Health check geral de instância Oracle 19c — tablespaces, FRA, objetos inválidos, jobs falhos, sessões bloqueadas, waits e memória — com relatório semáforo (OK/ATENÇÃO/CRÍTICO) por área. Usar quando o usuário pedir "health check", "como está o banco", "checagem geral", "status da instância", ou antes de uma janela de manutenção/deploy.
---

# /ora-health — health check geral da instância

## Entrada
Nenhuma obrigatória. Opcional:
- Janela para jobs falhos (padrão `:hours = 24`).
- Lista de áreas a pular (ex.: "sem FRA" em ambiente sem backup local).

## Pré-requisito
Conexão via SQLcl MCP (alias `dev` ou `hml`). Se MCP indisponível, pedir ao usuário a saída das queries de `sql/health/` e analisar offline.

## Fluxo

Rodar as 7 queries abaixo (todas read-only) e classificar cada área com semáforo:

### 1. Tablespaces — [tablespace_usage.sql](../../../sql/health/tablespace_usage.sql)
- Usar `pct_used_max` (considera autoextend até maxbytes) como métrica principal.
- 🟢 OK < 85% | 🟡 ATENÇÃO 85–94% | 🔴 CRÍTICO ≥ 95%.
- Tablespace sem autoextend (`autoext_files = 0`) com pct alto merece destaque extra.

### 2. FRA — [fra_usage.sql](../../../sql/health/fra_usage.sql)
- Usar `pct_used_net` (desconta reclaimable). 🟢 < 80% | 🟡 80–89% | 🔴 ≥ 90%.
- Sem linhas ou `mb_limit = 0`: FRA não configurada — informar, não é erro.

### 3. Objetos inválidos — [invalid_objects.sql](../../../sql/health/invalid_objects.sql)
- 🟢 zero | 🟡 inválidos apenas em schemas de aplicação | 🔴 inválidos em SYS/SYSTEM ou centenas de objetos (sugere falha de deploy/patch).
- Sugerir script de recompilação (`UTL_RECOMP` ou `ALTER ... COMPILE`) — entregar, nunca executar.

### 4. Jobs falhos — [failed_jobs.sql](../../../sql/health/failed_jobs.sql) (bind `:hours`)
- 🟢 zero | 🟡 falhas pontuais | 🔴 job crítico falhando repetidamente na janela.
- Erros ORA- em `additional_info`: encadear com `/ora-error` para diagnóstico.

### 5. Sessões bloqueadas — [blocked_sessions.sql](../../../sql/health/blocked_sessions.sql)
- 🟢 zero | 🟡 bloqueios com `seconds_in_wait` < 60s | 🔴 bloqueio ≥ 60s ou cadeia com várias sessões.
- Se houver bloqueio relevante, encadear com `/lock-detective` para a árvore completa.

### 6. Waits atuais — [session_waits.sql](../../../sql/health/session_waits.sql)
- Foto instantânea. 🟢 maioria ON CPU / User I/O proporcional | 🟡 Concurrency ou Configuration visíveis | 🔴 Commit/Concurrency dominando ou dezenas de sessões no mesmo evento.

### 7. Memória — [memory_usage.sql](../../../sql/health/memory_usage.sql)
- Comparar `total PGA allocated` vs `aggregate PGA target parameter`: 🟡 se alocado > target; 🔴 se muito acima (risco de ORA-04036).
- `cache hit percentage` do PGA baixo (< 80%) indica spill para temp → 🟡.

## Saída (relatório)
```
# Health Check — <instância> — <data/hora>

| Área | Status | Resumo |
|------|--------|--------|
| Tablespaces | 🟢/🟡/🔴 | <pior tablespace e pct> |
| FRA | ... | ... |
| Objetos inválidos | ... | ... |
| Jobs falhos (24h) | ... | ... |
| Sessões bloqueadas | ... | ... |
| Waits atuais | ... | ... |
| Memória | ... | ... |

## Detalhes por área com status ≠ OK
<evidência (linhas relevantes da query) + ação recomendada>

## Scripts sugeridos (revisar antes de executar — NÃO executados)
<ex.: ALTER DATABASE DATAFILE ... RESIZE, recompilação, etc.>
```

## Regras
- Somente SELECT em views V$/DBA_. Nenhuma ação corretiva é executada — resize, recompilação, kill, purge de FRA etc. são entregues como script para revisão do usuário.
- Se privilégio insuficiente (ORA-00942), usar fallback anotado em cada query (ALL_) e marcar a área como "não avaliada" quando não houver fallback, sem inventar status.
- Conferir versão da conexão antes de recomendações release-specific (`v$instance.version_full`) — baseline 19c.
