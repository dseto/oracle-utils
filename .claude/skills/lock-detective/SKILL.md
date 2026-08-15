---
name: lock-detective
description: Investiga bloqueios de sessão em Oracle 19c — árvore de bloqueio (holder raiz → waiters), decodificação de tipo/modo de lock, objetos travados e SQL de cada sessão — e entrega diagnóstico + script de KILL SESSION para revisão (nunca executado). Usar quando o usuário relatar "sessão travada", "sistema parado esperando", "lock no banco", ORA-00060/deadlock, ou pedir "quem está bloqueando quem".
---

# /lock-detective — investigação de bloqueios

## Entrada
Nenhuma obrigatória. Opcional:
- SID/serial# ou username de uma sessão específica reclamada pelo usuário.
- Nome de tabela suspeita de estar travada.

## Pré-requisito
Conexão via SQLcl MCP (alias `dev` ou `hml`). Se MCP indisponível, pedir ao usuário a saída das queries de `sql/locks/` e analisar offline.

## Fluxo

### 1. Montar a árvore de bloqueio
Rodar [blocking_tree.sql](../../../sql/locks/blocking_tree.sql) — `CONNECT BY PRIOR sid = blocking_session` sobre `V$SESSION`.
- `tree_level = 1` = **holder raiz** (bloqueia e não é bloqueado). É o alvo da investigação.
- Sem linhas = não há cadeia de bloqueio ativa neste momento — informar e encerrar (bloqueios são voláteis; sugerir rodar de novo no momento do sintoma).

### 2. Decodificar os locks envolvidos
Rodar [lock_details.sql](../../../sql/locks/lock_details.sql) — `V$LOCK` filtrado por `block = 1 OR request > 0`, com tipo (TX/TM/UL...) e modo (SS/SX/S/SSX/X) decodificados.
- TX em modo X requisitado por outro = lock de linha clássico (transação não commitada).
- TM = lock de tabela — atenção a FK sem índice (waiter pede modo 4/5 em TM).
- `ctime` alto no holder = transação aberta há muito tempo.

### 3. Identificar objetos travados
Rodar [locked_objects.sql](../../../sql/locks/locked_objects.sql) — `V$LOCKED_OBJECT` + `DBA_OBJECTS` (fallback `ALL_OBJECTS`).

### 4. Contexto de cada sessão da cadeia
Para holder raiz e principais waiters:
- SQL atual: `sql_id` da árvore → se relevante, `V$SQL.sql_text` (ou encadear `/sql-tune`).
- Holder com `status = INACTIVE` e lock TX = **transação esquecida sem commit** (causa mais comum) — o SQL atual pode ser NULL; considerar `prev_sql_id`.
- Origem: `username`, `osuser`, `machine`, `program`, `logon_time` de `V$SESSION`.

### 5. Diagnóstico e ação

Saída:
```
## Bloqueio: <resumo>
**Holder raiz**: SID <n>,<serial#> — <username>@<machine> (<program>), status <...>, transação aberta há <ctime>s
**Cadeia**: <n> sessões esperando; espera máxima <s>s
**Objeto(s)**: <owner.tabela> (modo <X/SX/...>)
**Causa provável**: <ex.: transação sem commit / lote longo / FK sem índice>
**Ações (em ordem de preferência)**:
1. Contatar o dono da sessão para COMMIT/ROLLBACK (menos invasivo)
2. Se necessário, kill — SCRIPT ABAIXO, revisar e executar por sua conta:
```
```sql
-- REVISAR ANTES DE EXECUTAR. Derruba a sessao holder e desfaz (rollback) a transacao dela.
ALTER SYSTEM KILL SESSION '<sid>,<serial#>' IMMEDIATE;
-- Em RAC, incluir inst_id: ALTER SYSTEM KILL SESSION '<sid>,<serial#>,@<inst_id>';
```

## Regras
- **NUNCA executar** `ALTER SYSTEM KILL SESSION` (nem DISCONNECT SESSION) — sempre entregar o script com sid/serial# preenchidos para o usuário revisar e decidir.
- Avisar o custo do kill: rollback da transação do holder pode demorar proporcionalmente ao trabalho não commitado.
- Somente SELECT em V$/DBA_. Fallback ALL_ quando ORA-00942 (anotado nas queries).
- Bloqueio é estado volátil: sempre registrar o horário da coleta no relatório.
