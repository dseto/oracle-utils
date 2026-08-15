---
name: db-dashboard
description: Gera dashboard HTML de snapshot do banco Oracle 19c publicado como Artifact — sessões por status/wait_class, top SQL por elapsed, uso de tablespaces e top eventos de espera, em página single-file com KPIs, tabelas e barras CSS. Usar quando o usuário pedir "dashboard do banco", "visão geral / saúde do banco", "snapshot de performance", "como está o banco agora", ou quiser uma página para compartilhar o estado da instância.
---

# /db-dashboard — snapshot visual do banco como Artifact

## Entrada
Nenhuma obrigatória. Opcional: alias da conexão (default `dev`).

## Pré-requisito
Conexão via SQLcl MCP (alias `dev` ou `hml`). As queries usam views `V$`/`DBA_` — exigem `SELECT_CATALOG_ROLE` ou grants diretos. **Sem MCP não há modo offline útil**: avisar e parar (ou aceitar saídas coladas pelo usuário).

## Fluxo

### 1. Coletar dados (somente SELECT, cada query independente)
- [dash_sessions.sql](../../../sql/viz/dash_sessions.sql) — sessões USER por status e wait_class.
- [dash_top_sql.sql](../../../sql/viz/dash_top_sql.sql) — top 10 SQL por elapsed em `V$SQLSTATS`.
- [dash_tablespaces.sql](../../../sql/viz/dash_tablespaces.sql) — uso % por tablespace (considera autoextend).
- [dash_waits.sql](../../../sql/viz/dash_waits.sql) — top 10 eventos não-idle de `V$SYSTEM_EVENT` (acumulado desde startup).
- Registrar também: timestamp do snapshot (`SYSDATE`), instância e versão (`v$instance.instance_name`, `version_full`).
- **Falha parcial não aborta o dashboard**: se uma query der ORA-00942/ORA-01031 (sem `SELECT_CATALOG_ROLE` ou grant), renderizar o card correspondente com aviso textual ("sem privilégio em V$SQLSTATS — peça grant ou SELECT_CATALOG_ROLE") e seguir com as demais. Views `V$` não têm fallback `ALL_`.

### 2. Montar o HTML (single-file)
Restrições de Artifact (CSP bloqueia rede):
- **Dados embutidos** no HTML na geração — sem `fetch`, sem CDN, sem fontes externas, sem libs JS. CSS 100% inline.
- Tema claro/escuro via variáveis CSS em `:root` + bloco `@media (prefers-color-scheme: dark)` redefinindo as variáveis; `body` com background explícito.

Layout:
- **Header**: nome da instância, versão, e **timestamp do snapshot bem visível** (os dados são estáticos — deixar claro que é uma foto, não monitoramento ao vivo; waits/top SQL são acumulados desde o startup).
- **Linha de KPIs** (cards): sessões ativas, sessões totais, tablespace mais cheio (%), top wait class.
- **Sessões**: tabela status × wait_class ou barras horizontais por wait_class.
- **Tablespaces**: uma barra CSS pura por tablespace (`div` externa = trilho, `div` interna com `width:<pct>%`), cor de alerta ≥ 85% e crítica ≥ 95%; rótulo com used_mb/max_mb.
- **Top SQL**: tabela (sql_id, execs, elapsed total e por exec, buffer_gets, texto truncado em 120 chars) com scroll horizontal em container `overflow-x:auto`.
- **Waits**: tabela ou barras por time_waited_sec.
- Cards ausentes por privilégio: renderizar o card com a mensagem de limitação no lugar dos dados.

### 3. Publicar
- Escrever o HTML em arquivo e publicar como Artifact (título curto e estável, ex.: nome da instância; favicon estável).
- Novo snapshot da mesma instância na mesma conversa = republicar no mesmo arquivo/URL (atualização), não criar artifact novo.
- Entregar o link ao usuário com resumo de 2-3 linhas dos destaques (ex.: tablespace acima de 85%, wait dominante).

## Regras
- Somente SELECT. Nenhuma ação corretiva automática — se o snapshot revelar problema (tablespace cheio, SQL pesado), sugerir próximos passos (ex.: `/sql-tune` no sql_id) e scripts para revisão.
- Sem dados sensíveis: o SQL text pode conter literais com dados de negócio — truncado em 120 chars; se o usuário for compartilhar externamente, oferecer versão sem coluna de texto SQL.
- Compatibilidade 19c: `FETCH FIRST` (12c+) ok; nada de sintaxe 21c+.
