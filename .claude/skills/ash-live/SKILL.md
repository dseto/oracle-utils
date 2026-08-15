---
name: ash-live
description: Analisa a atividade recente da instância Oracle 19c via ASH (v$active_session_history — requer Diagnostics Pack) ou, sem licença, via amostragem manual de v$session — atividade por wait class/evento, top sql_id e top sessões dos últimos N minutos. Usar quando o usuário perguntar "o que o banco está fazendo agora", "o que rodou nos últimos X minutos", "quem está consumindo o banco", ou relatar pico de lentidão recente/em andamento.
---

# /ash-live — atividade recente da instância

## ⚠️ Licenciamento
`V$ACTIVE_SESSION_HISTORY` faz parte do **Oracle Diagnostics Pack** (licença paga). **Antes de consultá-la, confirmar com o usuário se o banco-alvo tem a licença.**
- **Com licença** → caminho ASH (histórico em memória, minutos a horas para trás).
- **Sem licença** → alternativa livre: amostragem manual de `V$SESSION` com [samples_vsession.sql](../../../sql/health/samples_vsession.sql) executado repetidamente (só enxerga o presente; não reconstrói o passado).
- Na conexão `dev` (XE local de teste) a consulta funciona tecnicamente, mas manter o hábito de perguntar — os alvos reais são bancos licenciados de formas diversas.

## Entrada
- Janela em minutos (bind `:minutes`, padrão 15) — caminho ASH.
- Opcional: sintoma ("travou às 14:32") para focar a leitura.

## Pré-requisito
Conexão via SQLcl MCP (alias `dev` ou `hml`). Se MCP indisponível, entregar as queries para o usuário rodar e analisar a saída offline.

## Fluxo

### Caminho A — ASH (com Diagnostics Pack)
Rodar os 3 blocos de [ash_activity.sql](../../../sql/health/ash_activity.sql) (bind `:minutes`):
1. **Atividade por wait class/evento** — perfil da janela. Regra de leitura: 1 sample ≈ 1 segundo de 1 sessão ativa; `total samples / (minutes*60)` ≈ AAS da janela.
2. **Top 10 sql_id por samples** — candidatos diretos a `/sql-tune <sql_id>`; colunas cpu_samples vs wait_samples indicam se o SQL é CPU-bound ou I/O/contenção.
3. **Top 10 sessões** — sessão dominando sozinha = job/lote; muitas sessões no mesmo sql_id = SQL quente de aplicação.

Interpretar eventos com as mesmas heurísticas de `/awr-analyze` (tabela de wait events).

### Caminho B — amostragem manual (sem licença)
1. Rodar [samples_vsession.sql](../../../sql/health/samples_vsession.sql) N vezes (ex.: 10–20 execuções espaçadas de ~5–10s; espaçar pelas próprias chamadas, sem loop no servidor).
2. Acumular as linhas e agregar manualmente: contagem por `activity`/`wait_class`, por `sql_id`, por `sid`.
3. Avisar a limitação: amostra pequena e apenas do intervalo observado — eventos rápidos podem não aparecer.

## Saída (relatório)
```
## Atividade — últimos <N> min (AAS ≈ <n>)

| Wait class / evento | Samples | % |
|---|---|---|
| ON CPU | 320 | 45.1 |
| User I/O — db file sequential read | 210 | 29.6 |

ON CPU                       ████████████████████ 45%
db file sequential read      █████████████ 30%
enq: TX - row lock           █████ 11%

**Top SQL**: <sql_id> (<pct>%) — <cpu ou wait bound> → sugerir /sql-tune
**Top sessões**: SID <n> (<user>) — <observação>
**Leitura**: <1 parágrafo: o banco está fazendo X; gargalo aparente Y; próxima ação Z>
```
O gráfico de barras textual (blocos `█` proporcionais ao %) é opcional — incluir quando houver 3+ linhas relevantes.

## Regras
- Somente SELECT. Nenhuma ação sobre sessões/SQL identificados — kill, purge, etc. apenas como script para revisão (encadear `/lock-detective` se o achado for bloqueio).
- Não consultar `DBA_HIST_ACTIVE_SESS_HISTORY` (também Diagnostics Pack e fora do escopo "live" — para histórico longo, `/awr-analyze` com relatório fornecido).
- Registrar horário da coleta; atividade é volátil.
