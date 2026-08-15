---
name: ddl-review
description: Faz review de scripts de migração DDL Oracle 19c (arquivo ou texto colado) antes de rodar em produção — checklist de disponibilidade (locks, ONLINE), reversibilidade (rollback, backup), performance do deploy e correção (tipos, grants, dependências), com veredito final. Usar quando o usuário pedir para "revisar migração", "validar DDL", "esse script é seguro para produção?", ou colar um script com CREATE/ALTER/DROP antes de deploy.
---

# /ddl-review — review de script de migração DDL

## Entrada
- Caminho de arquivo `.sql` (ler via Read) ou script colado na conversa.
- Contexto opcional (perguntar se não informado e for relevante): tamanho das tabelas afetadas, janela de deploy (online vs manutenção), volume de dados a carregar.

## Escopo
Review **estático** — o script nunca é executado. Se houver conexão MCP disponível, pode-se rodar SELECTs de apoio (ex.: `num_rows` em `all_tables`, FKs existentes em `all_constraints`) para calibrar severidade; nada além de SELECT.

## Severidades
- **[CRITICO]** — pode causar indisponibilidade, perda de dados ou deploy irrecuperável.
- **[ALTO]** — risco real em produção (lock longo, sem rollback), mas contornável.
- **[MEDIO]** — degrada o deploy ou deixa dívida (performance, grants).
- **[BAIXO]** — melhoria recomendada, sem risco imediato.

## Checklist

### a) DISPONIBILIDADE
- `CREATE INDEX` sem `ONLINE` → lock de DML na tabela durante a criação. Em produção: sempre `ONLINE`. [ALTO]
- `ALTER TABLE ... ADD col NOT NULL`:
  - **COM** `DEFAULT` → rápido em 19c (default metadata-only, não reescreve linhas). OK.
  - **SEM** `DEFAULT` em tabela populada → falha (ORA-01758) ou exige update massivo prévio com lock. [CRITICO]
- `ALTER TABLE ... DROP COLUMN` direto → reescreve a tabela inteira sob lock. Preferir `SET UNUSED` + `DROP UNUSED COLUMNS` em janela. [ALTO]
- `ALTER TABLE ... MOVE` sem `ONLINE` → tabela offline para DML + índices ficam UNUSABLE. Usar `MOVE ONLINE` (19c suporta) e conferir rebuild de índices. [ALTO]
- FK nova sem índice na coluna filha → locks em cascata (TM lock full) na tabela pai a cada DML no pai. Criar índice junto. [ALTO]
- `ALTER TABLE` em tabela quente sem `DDL_LOCK_TIMEOUT` → ORA-00054 imediato ou fila; sugerir `ALTER SESSION SET ddl_lock_timeout = N`. [MEDIO]

### b) REVERSIBILIDADE
- Script sem rollback par a par → para cada mudança, exigir o inverso documentado (ADD↔DROP/SET UNUSED, CREATE↔DROP, GRANT↔REVOKE). Mudanças sem inverso possível (DROP, TRUNCATE) exigem backup. [ALTO]
- `DROP TABLE`/`DROP` de objeto com dados sem backup/export prévio documentado no script → [CRITICO]
- `TRUNCATE` → irreversível (sem flashback de DDL, sem rollback); exigir confirmação de que a perda é intencional e backup se houver dúvida. [CRITICO]
- `DROP COLUMN` → irreversível; mesmo tratamento de backup. [ALTO]

### c) PERFORMANCE DO DEPLOY
- Índice criado **antes** de carga massiva no mesmo script → cada linha inserida mantém o índice; inverter ordem (carga primeiro, índice depois). [MEDIO]
- Constraint (FK/CHECK) adicionada com validação inline em tabela grande → full scan sob lock. Preferir `ENABLE NOVALIDATE` no deploy + `VALIDATE` em passo posterior. [MEDIO]
- `CREATE INDEX`/`MOVE`/`ALTER TABLE` em objeto grande sem `PARALLEL` → deploy desnecessariamente longo. Sugerir `PARALLEL n` na criação + `NOPARALLEL` ao final (não deixar grau residual). [MEDIO]

### d) CORREÇÃO
- Tipos inconsistentes com colunas relacionadas (FK filha `NUMBER` vs pai `VARCHAR2`, join columns com tipos diferentes) → conversão implícita, índice ignorado. [ALTO]
- Length semantics: `VARCHAR2(n)` sem `CHAR`/`BYTE` explícito depende de `NLS_LENGTH_SEMANTICS` do ambiente; em base multibyte (AL32UTF8), `BYTE` trunca acentuados. Explicitar `CHAR` quando a coluna guarda texto. [MEDIO]
- Sequence/identity: tabela nova com PK numérica sem sequence/`GENERATED AS IDENTITY` associada, ou sequence criada sem `CACHE` adequado para alta insercao. [MEDIO]
- Grants esquecidos após `CREATE OR REPLACE`/recriação via DROP+CREATE → `DROP` derruba grants; recriar exige re-grant. `CREATE OR REPLACE` preserva. Conferir se o script re-concede. [ALTO]
- Sinônimos e dependências: recriar/alterar objeto invalida dependentes (packages, views, triggers); prever `ALTER ... COMPILE` ou verificação de inválidos ao final. Para mapear o impacto, encadear **/dep-graph** no objeto alterado. [MEDIO]

## Saída
Uma linha por finding, no formato:
```
<script>:<linha> [SEVERIDADE] <problema> -> <correcao>
```
Exemplo:
```
migra_v2.sql:14 [ALTO] CREATE INDEX sem ONLINE em tabela de producao -> adicionar ONLINE
migra_v2.sql:27 [CRITICO] TRUNCATE em PEDIDOS sem backup documentado -> exportar antes (expdp) ou remover o passo
```

Veredito final (obrigatório, uma das três linhas):
- `VEREDITO: APROVADO` — nenhum finding ALTO/CRITICO.
- `VEREDITO: APROVADO COM RESSALVAS` — findings ALTO presentes, mas com correção simples antes do deploy; listar quais bloqueiam.
- `VEREDITO: REPROVADO` — qualquer CRITICO sem mitigação, ou acúmulo de ALTOs que torna o deploy inseguro.

## Regras
- Review estático: o script em análise **nunca é executado**, nem parcialmente, nem "só o CREATE pequeno".
- Queries de apoio (se conectado): somente SELECT em `ALL_`/`DBA_`.
- Baseline 19c: qualquer sintaxe 21c+ no script é finding [ALTO] (quebra no alvo real).
- Sem elogios, sem reescrever o script inteiro — findings pontuais + veredito.
