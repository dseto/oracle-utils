---
name: trace-analyze
description: Interpreta arquivos de trace Oracle 10046 (raw) ou saída tkprof — tempo por fase, top waits, SQL dominante, binds — e aponta onde o tempo foi gasto. Usar quando o usuário fornecer um arquivo .trc, saída tkprof, ou pedir análise de trace/event 10046. Funciona 100% offline, sem conexão com banco.
---

# /trace-analyze — análise de trace 10046 / tkprof

## Entrada
- Caminho de arquivo `.trc` (raw 10046) ou `.txt`/`.prf` (tkprof), ou conteúdo colado.
- Arquivos grandes (>2000 linhas): ler em blocos; começar pelo fim (summary do tkprof) ou amostrar WAITs do raw.

## Detecção de formato
- Contém linhas `PARSE #`, `EXEC #`, `FETCH #`, `WAIT #` → raw 10046.
- Contém blocos `call count cpu elapsed disk query current rows` → tkprof.

## Análise — tkprof
1. Para cada SQL do relatório: somar elapsed; ranquear. Focar nos que somam >=80% do tempo total.
2. Por SQL dominante:
   - `elapsed >> cpu` → espera (ver seção de wait events do tkprof).
   - `disk` alto vs `query` → leitura física; buffer cache pequeno ou full scan.
   - `count` de PARSE ~ count de EXEC → falta de cursor caching / parse por execução.
   - `rows` no FETCH vs fetches → array size pequeno (fetch row-by-row).
3. Waits: ranquear por tempo total. Interpretar:
   - `db file sequential read` → I/O de índice/rowid; muitos = nested loops ou índice ruim.
   - `db file scattered read` → full scan.
   - `direct path read temp`/`write temp` → sort/hash estourando PGA.
   - `enq: TX - row lock contention` → lock de linha; identificar bloqueador.
   - `log file sync` → commits frequentes demais.
   - `SQL*Net message from client` → tempo no client/rede, NÃO no banco (idle — descontar).

## Análise — raw 10046
1. Extrair cursores: mapear `#N` → texto do SQL (linhas `PARSING IN CURSOR #N`).
2. Somar `ela=` por cursor (EXEC+FETCH) e por evento WAIT (`nam='...'`).
3. Binds (level 12): seção `BINDS #N` — valores reais; conferir tipo (dty) vs coluna (conversão implícita).
4. `STAT #N` linhas → plano real com rows por step.
5. Gaps de timestamp sem WAIT → tempo não instrumentado (client/CPU fora do banco).

## Saída
```
## Análise do trace: <arquivo>
**Tempo total**: Xs | banco: Ys | client/idle: Zs
**Onde o tempo foi**: <top 3 — SQL ou wait, com %>
**SQL dominante**: <sql> — <diagnóstico: por que lento>
**Recomendações**: 1..N (se envolver tuning profundo de um SQL, sugerir /sql-tune com o sql_id)
```

## Regras
- Descontar sempre waits idle (`SQL*Net message from client`, `pmon timer`, etc.) do tempo de banco — erro clássico de leitura.
- Se trace truncado (`dump file size limit`), avisar que totais são parciais.
