---
name: awr-analyze
description: Interpreta relatório AWR ou Statspack já gerado (HTML/txt fornecido pelo usuário) — load profile/AAS, top wait events com heurísticas de causa, SQL ordered by, segments by waits, advisories — e entrega diagnóstico priorizado. 100% offline, não gera AWR (requer licença Diagnostics Pack). Usar quando o usuário fornecer um relatório AWR/Statspack, pedir "analisar esse AWR", ou perguntar "o que esse relatório diz sobre o banco".
---

# /awr-analyze — interpretação de relatório AWR/Statspack

## Entrada
Relatório **já gerado** pelo usuário:
- AWR em HTML ou texto (`awrrpt_*.html` / `.txt`), ou
- Statspack (`spreport` texto).

**Este skill NÃO gera relatórios.** Consultar `DBA_HIST_*` / gerar AWR requer licença **Oracle Diagnostics Pack** — avisar isso se o usuário pedir para gerar. Alternativa livre: **Statspack** (`spcreate.sql` + `spreport.sql`), sem licença adicional; a leitura abaixo vale para ambos (Statspack não tem algumas seções).

Se o usuário não forneceu o arquivo, pedir. Perguntar também o sintoma que motivou a análise (lentidão geral? job específico? horário?) — o relatório se lê melhor com hipótese.

## Fluxo — roteiro de leitura (nesta ordem)

### 1. Cabeçalho e escopo
- Duração do snapshot (elapsed). Janelas > 1–2h diluem picos — avisar se for o caso.
- Versão, CDB/PDB, CPUs, RAC (se sim, um relatório por instância).

### 2. Load profile — a régua do relatório
- **AAS = DB time / elapsed** (média de sessões ativas). AAS < 1 = banco quase ocioso — desconfiar de "lentidão do banco"; AAS >> CPUs = fila real.
- Anotar: logical reads/s, physical reads/s, executes/s, hard parses/s (alto = problema de cursores/binds), redo/s.

### 3. Top 10 foreground events — heurísticas
Para cada evento no topo, causa provável e próxima ação:

| Evento | Causa provável | Próxima ação |
|---|---|---|
| `DB CPU` dominante | carga de CPU: SQL ineficiente com muitos gets, parse excessivo | SQL ordered by Gets/CPU → `/sql-tune` |
| `db file sequential read` | I/O single-block: acesso via índice (às vezes índice ruim/clustering factor alto) | SQL ordered by Reads; avaliar latência média (ms) — >10ms = storage lento |
| `db file scattered read` | full scans multi-block | SQL com FTS em tabela grande → índice ou é esperado (batch)? |
| `direct path read` | full scans direto ao PGA (serial direct read) | tabelas grandes lidas repetidamente; avaliar caching/particionamento |
| `direct path read/write temp` | spill de sort/hash para temp: PGA insuficiente ou SQL exagerado | PGA advisory; SQL com sorts grandes |
| `log file sync` | commits frequentes esperando LGWR | commits em loop (aplicação); latência de redo storage; comparar com `log file parallel write` |
| `enq: TX - row lock contention` | contenção de linha (aplicação) | `/lock-detective` na ocorrência; padrão de acesso |
| `enq: TM - contention` | lock de tabela — FK sem índice é o clássico | indexar FKs envolvidas |
| `latch: shared pool` / `library cache lock/mutex` | hard parse excessivo, SQL sem bind, invalidações | hard parses/s no load profile; SQL com literais |
| `buffer busy waits` | blocos quentes (inserts concorrentes, índice sequencial) | Segments by Buffer Busy; hash partition/reverse index (avaliar) |
| `read by other session` | várias sessões lendo o mesmo bloco do disco | mesmo SQL quente → tuning do SQL |
| `SQL*Net message from client` (foreground alto) | idle — tempo no cliente/rede, não no banco | não é problema do banco; olhar aplicação |

### 4. SQL ordered by — cruzar com os eventos
- Elapsed time: os 3–5 primeiros; % do DB time total (dominância = tuning cirúrgico resolve).
- Gets (CPU-bound), Reads (I/O-bound), Executions (por-execução barato × volume alto = problema de aplicação/chattiness), Parse calls.
- Para cada sql_id candidato: sugerir `/sql-tune <sql_id>` (se o cursor ainda estiver em cache).

### 5. Segments by waits
- Segments by Physical Reads / Buffer Busy / Row Lock: liga o evento ao objeto concreto.

### 6. Instance efficiency e advisories
- Ignorar percentuais "bonitos" isolados (Buffer Hit 99% não prova nada). Usar apenas como confirmação.
- Buffer Pool Advisory e PGA Advisory: estimar ganho real de aumentar memória — só recomendar mudança de parâmetro com advisory apontando ganho relevante, como script para revisão.

## Saída (relatório)
```
## Análise AWR — <instância>, <janela> (elapsed <min>, AAS <n>)
**Perfil**: <CPU-bound / I/O-bound / contenção / commit-bound / ocioso>
**Top achados** (em ordem de impacto):
1. <evento/SQL> — <% DB time> — <causa provável> — <ação: /sql-tune sql_id, script, mudança app>
**Recomendações de parâmetro/memória** (se advisory suportar): <script para revisão — NÃO executado>
**Ressalvas**: <janela longa, RAC, dados ausentes no Statspack, etc.>
```

## Regras
- Trabalho 100% offline sobre o texto fornecido — nenhuma query obrigatória. Se conexão MCP disponível e o usuário quiser aprofundar um sql_id, encadear `/sql-tune`.
- Nunca gerar AWR nem consultar `DBA_HIST_*` sem o usuário confirmar que possui licença Diagnostics Pack.
- Qualquer mudança sugerida (parâmetro, memória, índice) = script comentado para revisão, nunca executado.
