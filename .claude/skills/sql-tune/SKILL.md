---
name: sql-tune
description: Diagnostica performance de uma query Oracle 19c a partir de SQL texto ou sql_id — plano real via DBMS_XPLAN, estatísticas de objetos, binds, histogramas — e entrega causa raiz + recomendação (índice, hint, reescrita, stats). Usar quando o usuário pedir para "tunar", "analisar performance", "por que essa query está lenta", ou colar um sql_id/plano de execução.
---

# /sql-tune — diagnóstico de performance de SQL

## Entrada
Um destes:
- `sql_id` (13 chars, ex.: `7ztv2z24kw0s0`)
- Texto do SQL (será localizado em `V$SQL` ou analisado via `EXPLAIN PLAN`)
- Plano de execução colado pelo usuário (modo offline — pular etapas de coleta)

## Pré-requisito
Conexão via SQLcl MCP (alias `dev` ou `hml`). Se MCP indisponível, pedir ao usuário a saída das queries de `sql/tune/` e analisar offline.

## Fluxo

### 1. Localizar cursor e plano real
- Com sql_id: rodar [xplan_cursor.sql](../../../sql/tune/xplan_cursor.sql) — `DBMS_XPLAN.DISPLAY_CURSOR(:sql_id, NULL, 'ALLSTATS LAST +OUTLINE +PEEKED_BINDS')`.
  - Se sem A-Rows (query não rodou com STATISTICS_LEVEL=ALL nem GATHER_PLAN_STATISTICS), avisar que E-Rows/A-Rows não é comparável e considerar pedir re-execução com hint `/*+ GATHER_PLAN_STATISTICS */`.
- Com texto SQL apenas: procurar em V$SQL via [find_sqlid.sql](../../../sql/tune/find_sqlid.sql); se não estiver em cache, `EXPLAIN PLAN FOR ...` + `DBMS_XPLAN.DISPLAY(NULL, NULL, 'ALL')`.
- Verificar múltiplos child cursors ([child_cursors.sql](../../../sql/tune/child_cursors.sql)) — plano instável / bind sensitivity / ACS.

### 2. Coletar contexto dos objetos
Para cada tabela do plano:
- [table_stats.sql](../../../sql/tune/table_stats.sql) — num_rows, blocks, last_analyzed, stale_stats.
- [index_info.sql](../../../sql/tune/index_info.sql) — índices, colunas, clustering_factor, distinct_keys.
- [col_histograms.sql](../../../sql/tune/col_histograms.sql) — histogramas e num_distinct das colunas de predicado.

### 3. Binds e execução
- [sql_binds.sql](../../../sql/tune/sql_binds.sql) — valores capturados em V$SQL_BIND_CAPTURE.
- [sql_stats.sql](../../../sql/tune/sql_stats.sql) — executions, buffer_gets/exec, elapsed/exec, rows/exec de V$SQLSTATS.

### 4. Diagnóstico (checklist)
Analisar nesta ordem — parar na primeira causa dominante, mas listar secundárias:
1. **Cardinalidade errada**: E-Rows vs A-Rows divergindo >10x em algum step → stats stale? histograma faltando? predicado com função sobre coluna? correlação entre colunas (candidato a extended stats)?
2. **Access path ruim**: FULL SCAN em tabela grande com predicado seletivo e sem índice → propor índice (avaliar colunas + ordem por seletividade). INDEX RANGE SCAN com clustering_factor ~ num_rows → índice pouco eficaz para range.
3. **Join**: NESTED LOOPS com outer grande (explosão de starts) vs HASH JOIN faltando; ordem de join ruim.
4. **Bind peeking / ACS**: múltiplos childs com planos distintos, is_bind_sensitive/is_bind_aware.
5. **Reescrita**: subquery correlacionada → join; OR expansivo → UNION ALL; funções em predicado → coluna virtual/índice function-based.

### 5. Saída (relatório)
```
## Diagnóstico: <sql_id ou resumo>
**Causa raiz**: <1 frase>
**Evidência**: <steps do plano com E-Rows vs A-Rows, stats relevantes>
**Recomendação** (em ordem de preferência):
1. <ação> — script pronto (NÃO executar; entregar para revisão)
2. ...
**Riscos/observações**: <impacto em outras queries, custo de índice, etc.>
```

## Regras
- Somente SELECT/EXPLAIN. Nunca criar índice, coletar stats ou criar profile — entregar script.
- EXPLAIN PLAN usa PLAN_TABLE (insert) — aceitável, é tabela de trabalho padrão.
- Se privilégio insuficiente (ORA-00942 em V$/DBA_), cair para ALL_ e avisar limitação.
