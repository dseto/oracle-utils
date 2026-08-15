---
name: schema-diff
description: Compara dois schemas Oracle 19c (mesma instância ou entre conexões dev/hml) — inventário de objetos, colunas, constraints e índices — e gera relatório de divergências + script ALTER de sincronização comentado para revisão. Usar quando o usuário pedir para "comparar schemas", "diff de schema", "o que mudou entre dev e hml", ou verificar se dois ambientes estão estruturalmente iguais.
---

# /schema-diff — comparação estrutural de schemas

## Entrada
- Dois schemas: `SCHEMA_A` (referência/origem) e `SCHEMA_B` (alvo a sincronizar).
- Cenários:
  - **Mesma instância**: uma conexão só, rodar cada query duas vezes trocando `:owner`.
  - **Conexões diferentes** (ex.: `dev` vs `hml`): coletar de cada conexão separadamente via MCP — `connect` no primeiro alias, rodar as queries, guardar resultados, `disconnect`, `connect` no segundo alias, repetir. Nunca assumir que as duas conexões estão acessíveis ao mesmo tempo.

## Pré-requisito
Conexão via SQLcl MCP (aliases `dev`, `hml`). Se MCP indisponível, pedir ao usuário a saída das queries de `sql/schema/` de cada ambiente e comparar offline.

## Fluxo

### 1. Inventário de objetos
Rodar [schema_objects.sql](../../../sql/schema/schema_objects.sql) (`:owner` = cada schema).
- Diff por `(object_type, object_name)`: objetos **ausentes** em B, **sobrando** em B (existem só em B).
- Marcar objetos com `status = 'INVALID'` em qualquer lado — divergência de saúde, não só de estrutura.

### 2. Colunas divergentes
Para tabelas presentes em ambos: [schema_tables_cols.sql](../../../sql/schema/schema_tables_cols.sql).
- Comparar por `(table_name, column_name)`: coluna ausente/sobrando; depois `data_type`, tamanho (`data_length`/`data_precision`/`data_scale`/`char_length`), `char_used` (BYTE vs CHAR), `nullable`, `data_default`.
- `data_default` é LONG e pode vir truncado — em caso de dúvida, confirmar via `get_ddl.sql`.

### 3. Constraints
[schema_constraints.sql](../../../sql/schema/schema_constraints.sql).
- Comparar por `(table_name, constraint_type, columns)` — **nunca por nome** (SYS_C... difere entre schemas).
- Divergências: constraint ausente, status (`ENABLED`/`DISABLED`), `validated`, regra de delete em FK.

### 4. Índices
[schema_indexes.sql](../../../sql/schema/schema_indexes.sql).
- Comparar por `(table_name, columns, uniqueness)` — nomes gerados também divergem.
- Divergências: índice ausente, unicidade diferente, `status`/`visibility` diferente.

### 5. DDL pontual (quando o diff tabular não basta)
Para objetos com divergência complexa (views, packages, check constraints, defaults truncados): [get_ddl.sql](../../../sql/schema/get_ddl.sql) em cada lado e comparar o texto. O bloco `SET_TRANSFORM_PARAM` remove storage/tablespace para o diff não acusar diferença irrelevante de físico.

### 6. Script de sincronização (entregar, NUNCA executar)
Para cada divergência estrutural, gerar o `ALTER`/`CREATE` que levaria B ao estado de A — **todo comentado** e com avisos inline:

```sql
-- =========================================================
-- Script de sincronizacao: SCHEMA_B -> estado de SCHEMA_A
-- GERADO PARA REVISAO. NAO EXECUTAR SEM ANALISE.
-- =========================================================

-- [AVISO] Reducao de tamanho de coluna: falha se houver dados maiores
--         que o novo limite; risco de perda/truncamento. Validar antes:
--         SELECT MAX(LENGTH(col)) FROM tab;
-- ALTER TABLE tab MODIFY (col VARCHAR2(50));

-- [AVISO] ALTER TABLE exige lock DDL; em tabela quente pode gerar
--         ORA-00054 / fila de sessoes. Janela de manutencao recomendada.
-- ALTER TABLE tab ADD (col_nova NUMBER DEFAULT 0 NOT NULL);

-- [AVISO] Objeto existe apenas em B. DROP e destrutivo e fora do escopo
--         desta skill decidir; confirmar se e lixo ou feature nova de B.
-- -- DROP TABLE tab_so_em_b;  -- NAO descomentara sem backup/export
```

Regras do script:
- Toda linha executável comentada; usuário descomenta o que aprovar.
- Shrink de coluna, `DROP`, `NOT NULL` em coluna com dados: aviso obrigatório de perda de dados/erro.
- `NOT NULL` sem `DEFAULT` em tabela populada: avisar que exige update prévio dos NULLs.
- Índices: sugerir `ONLINE`; constraints em tabela grande: sugerir `ENABLE NOVALIDATE` + `VALIDATE` posterior.
- Sugerir encadear **/ddl-review** no script gerado antes de qualquer execução.

## Saída (relatório)
```
## Schema diff: SCHEMA_A (ref) vs SCHEMA_B
**Resumo**: X objetos ausentes, Y sobrando, Z tabelas com colunas divergentes, ...

### Objetos
| divergencia | tipo | nome | detalhe |

### Colunas
| tabela | coluna | SCHEMA_A | SCHEMA_B |

### Constraints
### Indices

### Script de sincronizacao
<script comentado acima — entregar em bloco ou arquivo, conforme o usuario preferir>
```

## Regras
- Somente SELECT e chamadas read-only (`DBMS_METADATA` é leitura; `SET_TRANSFORM_PARAM` afeta só a sessão).
- Script de sincronização é **entregável para revisão** — nunca executar DDL, mesmo que o usuário pareça ter aprovado no diff; execução exige pedido explícito e é feita pelo usuário.
- Compatibilidade 19c: nenhum DDL sugerido pode usar sintaxe 21c+.
- Se ORA-00942 em `DBA_`, as queries já usam `ALL_` — mas avisar que `ALL_` só mostra o que o usuário conectado enxerga (diff pode ficar incompleto sem privilégio).
