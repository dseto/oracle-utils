# Plano: skill `/oracle-dependency-graph` (rev. 2 — pós-revisão Fable)

Baseado no design doc do usuário (artifact `DESIGN_oracle-dependency-graph.md`).
Objetivo: grafo estático de dependências a partir de uma raiz `OWNER.OBJETO`,
materializado em `nodes/*.md` + `edges.jsonl` + `INDEX.md`, para o **Claude
Code consumir via grep** — não para humano visualizar.

Rev. 2 incorpora a revisão independente (Claude Fable): reuso ampliado para o
projeto todo (`sql/schema/`, `sql/viz/`, skills, agentes), corte do
`--recompile=auto`, correção da contradição de idempotência, riscos de
CLI/BFS, e redimensionamento honesto da ativação de `lexical.py`.

## 0. Decisão de arquitetura: estender `plsqlflow/`, não criar pacote novo

O design doc propõe um script standalone `extract_graph.py --dsn ...`. Isso
duplicaria conexão, credenciais, PL/Scope, SQL dinâmico e resolução de
sinônimo — tudo já construído e testado no contrato `plsqlflow-py`. Decisão:
a nova skill vira um **subcomando do pacote `plsqlflow/` existente**
(`python -m plsqlflow depgraph <owner.objeto> --output ...`), sem tocar nos
módulos congelados por golden test (`graph.py`, `mermaid.py`, `report.py` —
grão subprograma, propósito diferente).

## 1. Por que não substitui as skills existentes

| Skill | Fonte | Escopo | Saída | Consumidor |
|---|---|---|---|---|
| `/dep-graph` (existente) | `*_DEPENDENCIES` via CONNECT BY, 1 round-trip | profundidade baixa | mermaid no chat | Humano, relance visual |
| `/plsql-flow` (existente) | PL/Scope por **subprograma** | 1 cadeia de execução | mermaid + JSON | Humano + LLM (residual) |
| `/oracle-dependency-graph` (novo) | BFS `ALL_DEPENDENCIES` + PL/Scope + triggers + SQL dinâmico, escopo **objeto** | fechamento transitivo completo | `nodes/*.md` + `edges.jsonl` + `INDEX.md` em disco | **Claude Code via grep**, sem reconsultar banco |

Esta tabela vai morar (resumida) nas três SKILL.md como cross-link "quando
usar qual" — não só neste plano (ver T-06).

## 2. Mapa de reuso — projeto todo

### 2.1 Reuso direto (sem alteração)

| Necessidade | Ativo | Evidência |
|---|---|---|
| Conexão thin, alias, credencial nunca em CLI | `plsqlflow/db.py` | `db.py:36-106` |
| Hop de dependência (primitiva da BFS) | `extract.fetch_deps_direct` | `sql/flow/deps_direct.sql` |
| PL/Scope check/calls/statements | `extract.fetch_plscope_*` | `sql/flow/plscope_*.sql` |
| Cadeia de sinônimos | `resolve.resolve_synonym_chain` | `resolve.py:107` |
| SQL dinâmico literal (`resolved`) | `dynsql.resolve_dynamic_sql` | `dynsql.py:186` |
| Triggers de tabelas escritas | `extract.fetch_triggers_for_tables` | `sql/flow/triggers_for_tables.sql` |
| Fonte do objeto | `extract.fetch_source` | `sql/flow/fetch_source.sql` |
| Padrão golden/fixture offline | `tests/fixtures/flow_demo_*` | `test_plsqlflow_golden.py` |

### 2.2 Reuso por derivação (achados da revisão — antes "novo do zero")

| Necessidade | Base existente | Delta |
|---|---|---|
| Catálogo de objetos p/ heurística + hash da cadeia | `sql/schema/schema_objects.sql` (ALL_OBJECTS por owner, já filtra `BIN$`/partições) | Nova `sql/flow/object_catalog.sql` **derivada dela**, acrescentando `LAST_DDL_TIME` e `STATUS` (necessários pro hash da cadeia e pro campo `status` do nó) |
| Colunas de tabela | `sql/schema/schema_tables_cols.sql` (ALL_, char_used, default) + `sql/viz/erd_tables.sql` (bind `:table_list` via INSTR) | Nova `sql/flow/tab_columns.sql` = fusão das duas (ALL_ + `:table_list`), não escrita do zero |
| Fechamento transitivo alternativo | `sql/viz/i_depend_on.sql` / `deps_on_me.sql` (CONNECT BY NOCYCLE) | **Não substitui a BFS** — CONNECT BY enumera caminhos, não nós; em grafos com diamantes explode em linhas repetidas (problema já reconhecido em `dep-graph/SKILL.md:38`). BFS Python com visited-set é linear em arestas e permite fronteira SYS/sinônimo por nó. Justificativa registrada aqui para o implementador não "otimizar" para CONNECT BY depois. |
| Sanitização de nome de arquivo | Convenção de `dep-graph/SKILL.md:26` (sanitiza `$`, `#`, `.`) | Reusar a mesma convenção para `nodes/*.md` (NTFS case-insensitive; identifiers quoted colidem — documentar) |

### 2.3 Reuso superestimado no plano rev. 1 (corrigido)

**`lexical.py` NÃO resolve o nível `partial` sozinho.** Verificado: a regex
`find_call_candidates` (`lexical.py:30`) só captura identificador seguido de
`(` ou `;` — desenhada p/ candidato a *chamada*, não p/ nome de tabela em
fragmento SQL (`FROM x WHERE ...` não casa). Além disso `scan_candidates`
exige `local_vars` que ninguém produz, e chamadas pontuadas (`PKG.PROC`)
capturam só `PROC` (descasamento de grão vs catálogo). T-03 prevê
**modo novo em `lexical.py` para fragmentos SQL** (regex de identificadores
após FROM/JOIN/INTO/UPDATE/DELETE FROM/MERGE INTO + match contra catálogo),
reaproveitando `strip_comments_and_literals` e `classify_candidates` que
servem como estão. Estimativa honesta: extensão, não "primeiro uso do mesmo
código".

### 2.4 Integração com skills/agentes existentes (novo na rev. 2)

- `plsql-flow/SKILL.md:54-72` e `cli.py:6-8` dizem que `lexical.py` "não
  está ligada a nenhum pipeline" — ficam desatualizados com T-03; atualizar
  no T-06 (mesmo contrato).
- `.claude/agents/oracle-dba.md` e `plsql-review/SKILL.md` (que hoje consulta
  `all_dependencies` ad-hoc) ganham 1 linha cada: **grepar `oracle-graph/`
  se existir antes de reconsultar o banco** — é o propósito declarado da
  skill nova.
- `dep-graph/SKILL.md` e `plsql-flow/SKILL.md` ganham cross-link "quando usar
  qual" (tabela da seção 1 resumida).
- `oracle-graph/` entra no `.gitignore` — grafo é artefato derivado do banco,
  regenerável, potencialmente grande; não versionar (decisão explícita).
- SKILL.md nova documenta fallback sem `oracledb`/Python: mesmo padrão das
  demais skills ("Se MCP indisponível..."), apontando `scripts/run-query.ps1`
  para coleta manual + montagem assistida (grafo parcial, marcado como tal).

## 3. Estrutura de módulos novos

```
plsqlflow/
  extract.py          # + fetch_object_catalog, fetch_tab_columns
  depgraph.py         # NOVO — dataclasses DepNode/DepEdge + Fases 0-4 (BFS + enriquecimento)
  depgraph_render.py  # NOVO — Fase 5: nodes/*.md, edges.jsonl, INDEX.md, meta.json, recompile.sql
  lexical.py          # + modo "fragmento SQL" (seção 2.3)
  cli.py              # dispatch de subcomando (seção 4.3)
sql/flow/
  object_catalog.sql  # derivada de sql/schema/schema_objects.sql
  tab_columns.sql     # fusão de schema_tables_cols.sql + erd_tables.sql
.claude/skills/oracle-dependency-graph/
  SKILL.md            # grep patterns de consumo (seção 8 do design)
```

`DepNode`/`DepEdge` são dataclasses próprias em `depgraph.py` — grão objeto
(status, plscope, colunas), distinto de `graph.Node`/`Edge` (grão
subprograma, congelado).

## 4. Decisões de política (rev. 2)

### 4.1 PL/Scope ausente — SEM `--recompile=auto`

Achado crítico da revisão: `auto` executaria `ALTER ... COMPILE` (DDL),
violando a guarda de `db.py:117` (`run_query` só aceita SELECT/WITH), o
CLAUDE.md do projeto (nunca DDL sem confirmação; sugestões entregues como
script, nunca aplicadas) e o precedente do próprio `plsql-flow`.
**Cortado do plano.** Comportamento único: gerar `recompile.sql` consolidado
e sair com exit code dedicado. Se `auto` fizer sentido um dia, é contrato
próprio com guarda separada.

A checagem de PL/Scope é **incremental** (por objeto, ao expandir na BFS) —
a cadeia completa só é conhecida depois da BFS, e triggers descobertos na
Fase 4 também precisam de PL/Scope. O `recompile.sql` consolidado é emitido
no render (Fase 5), cobrindo tudo que foi visitado. Objetos sem PL/Scope
**não abortam** a extração: entram no grafo com `plscope: não`, arestas
CALL/READ/WRITE deles vêm só de `ALL_DEPENDENCIES` (sem linha), e o
`INDEX.md` os lista em pontos cegos.

### 4.2 Idempotência byte-a-byte × timestamp — resolvida

Contradição do rev. 1: `timestamp` em `meta.json` quebraria "mesma cadeia →
mesmos bytes". Solução: **sem timestamp de relógio**. `meta.json` grava
`chain_hash` = SHA-256 sobre a lista ordenada de
`(owner, name, type, last_ddl_time)` de todos os nós (dados do
`object_catalog.sql`, determinísticos, derivados do banco). Isso dá de graça
a detecção de staleness (banco mudou → hash muda → regenerar). Regras de
byte-exatidão em Windows: todos os arquivos gravados com `encoding="utf-8"`,
`newline="\n"`; `edges.jsonl` ordenado por `(from, to, type, line)`; listas
do `INDEX.md` e seções dos `nodes/*.md` com ordenação estável documentada.
Golden test compara arquivos em disco, não string em memória.

### 4.3 CLI — dispatch e exit codes

`cli.py:151` hoje tem `target` posicional livre; `python -m plsqlflow
depgraph X` seria parseado como `target="depgraph"`. Solução: dispatch
explícito antes do argparse — primeiro argumento sem `.` = subcomando
(`depgraph`); targets sempre contêm `.` (`owner.objeto`). Retro-compat
total: `python -m plsqlflow OWNER.OBJ` continua igual.

Exit codes (argparse usa 2 para erro de uso — não colidir):

| código | significado |
|---|---|
| 0 | sucesso |
| 3 | objetos sem PL/Scope (recompile.sql gerado; grafo parcial emitido) |
| 4 | raiz inexistente/inválida |
| 5 | erro de conexão |

(Design doc pedia 2 para recompile — trocado por 3 pra não colidir com
argparse; 3→4, 4→5 deslocados. T-05 testa cada código.)

### 4.4 BFS — dedup e limites

- Visited-set chaveado por `(owner, name)` **agregando PACKAGE + PACKAGE
  BODY num nó só** (deps_direct.sql filtra só por owner/name, sem tipo).
- Self-loop filtrado (BODY depende da própria SPEC → query devolve o próprio
  objeto).
- Arestas spec/body do mesmo par deduplicadas.
- Fronteira de parada: SYS, SYSTEM, prefixos `DBMS_`/`UTL_` viram folha sem
  expansão (`--stop-schemas` configurável).
- **Caps de segurança**: `--max-objects` (default 500) e `--max-depth`
  (default 20). Estouro → erro claro + `INDEX.md` parcial marcado
  explicitamente, nunca truncamento silencioso.

### 4.5 SQL dinâmico — 3 níveis

- `resolved`: `dynsql.py` (literal-only) — existe, reusar.
- `partial`: modo fragmento-SQL de `lexical.py` (seção 2.3) + match contra
  catálogo (`object_catalog.sql`) → arestas candidatas `confidence: partial`.
- `opaque`: nem literal nem candidato → nó marcador + entrada obrigatória em
  pontos cegos do `INDEX.md`.
- `--dynamic-window N` (default 30): nº de linhas de `ALL_SOURCE` capturadas
  ao redor da ocorrência para o trecho embutido na seção `## SQL Dinâmico`
  do node .md.
- `--llm-assist`: **fora do MVP** (extensão futura; nunca promove a
  `resolved`, sempre `llm_assisted: true` — conforme design doc).

### 4.6 Contexto estrutural (USAGE_CONTEXT_ID) — não-objetivo explícito

Design doc pedia contexto loop/branch/handler nas arestas CALL via
`USAGE_CONTEXT_ID`. `plscope_calls.sql` não seleciona `usage_id`/
`usage_context_id`; reconstruir a hierarquia é custo real. **MVP grava
aresta CALL com linha, sem contexto estrutural** — campo `context` fica
reservado no schema da aresta, preenchimento vira contrato futuro. Dropado
com justificativa, não em silêncio.

### 4.7 Colunas em arestas WRITE — best-effort

`ALL_STATEMENTS` não entrega colunas. MVP: colunas extraídas por parse do
`text` do statement quando disponível (INSERT com lista de colunas, UPDATE
SET) — best-effort, campo `cols` opcional na aresta; ausência não é erro.
Extração via `ALL_IDENTIFIERS` usage de coluna fica como melhoria futura.

## 5. Saída (conforme design doc, seção 7)

```
oracle-graph/<OWNER>.<RAIZ>/
├── INDEX.md          # raiz, estatísticas, fechamento transitivo, PONTOS CEGOS
├── edges.jsonl       # 1 aresta/linha, ordenada por (from,to,type,line)
├── nodes/
│   └── <OWNER>.<OBJETO>.md    # nome sanitizado (convenção dep-graph)
├── recompile.sql     # só se houver objeto sem PL/Scope (consolidado pós-BFS)
└── meta.json         # chain_hash, versão do extrator, parâmetros (SEM timestamp)
```

Template do node .md (seções fixas, ordem fixa — pré-requisito do golden):

```markdown
# OWNER.NOME
- tipo: ... | status: VALID|INVALID | plscope: sim|não | source: linhas X-Y

## Chama (outbound)
## Chamado por (inbound)
## Tabelas acessadas        # só nós PL/SQL; R / W:INSERT|UPDATE|DELETE|MERGE + linha + cols
## Colunas                  # só nós TABLE (de tab_columns.sql)
## Triggers ativados        # se houver escritas
## SQL Dinâmico             # se houver; linha + classificação + trecho (dynamic-window)
```

Arestas duplicadas nos dois nós (outbound origem, inbound destino) —
redundância proposital pra grep bidirecional sem join, conforme design.

## 6. Tarefas

| id | entrega | prova |
|---|---|---|
| T-01 | `extract.py` + 2 SQL derivadas (`object_catalog.sql`, `tab_columns.sql`) + dataclasses `DepNode`/`DepEdge` em `depgraph.py` (só tipos, sem lógica) | `pytest tests/test_depgraph_unit.py tests/test_conventions.py -q` |
| T-02 | `depgraph.py` Fases 0-1: BFS com visited-set `(owner,name)`, dedup spec/body, self-loop filtrado, fronteira stop-schemas, sinônimos via `resolve.py`, checagem PL/Scope incremental, caps max-objects/max-depth | `pytest tests/test_depgraph_bfs.py -q` |
| T-03 | `depgraph_enrich.py` (módulo próprio — evita conflito com T-02 em paralelo) Fases 2-3: cruzamento statement↔tabela (READ/WRITE + linha + cols best-effort), SQL dinâmico 3 níveis; + modo fragmento-SQL em `lexical.py` | `pytest tests/test_depgraph_enrich.py tests/test_plsqlflow_lexical.py -q` |
| T-04 | `depgraph.py` Fase 4 (triggers re-injetados na BFS) + `depgraph_render.py` Fase 5 (arquivos byte-exatos: utf-8, `\n`, ordenação estável, chain_hash) — 2 arquivos de teste distintos | `pytest tests/test_depgraph_triggers.py tests/test_depgraph_render.py -q` |
| T-05 | `cli.py` dispatch de subcomando + flags (`--output`, `--stop-schemas`, `--dynamic-window`, `--max-objects`, `--max-depth`) + exit codes 0/3/4/5 testados + retro-compat do modo flow | `pytest tests/test_depgraph_cli.py -q` |
| T-06 | SKILL.md nova (grep patterns) + cross-links em dep-graph/plsql-flow SKILL.md + atualização das notas "lexical não ligada" + linhas de consumo em oracle-dba.md/plsql-review + `.gitignore` + evidência real contra `FLOW_DEMO` no dev + regressão completa | `pytest -q -rs` (suíte inteira — protege goldens congelados) |

Dependências: T-02/T-03 dependem de T-01 (paralelizáveis — módulos
distintos); T-04 depende de T-02+T-03; T-05 de T-04; T-06 de T-05.

## 7. Não-objetivos

- Plano de execução / tuning.
- DB link — nó folha opaco, sem seguir conexão remota.
- `--llm-assist` (seção 4.5).
- Contexto estrutural loop/branch/handler nas arestas (seção 4.6).
- Colunas WRITE via ALL_IDENTIFIERS (seção 4.7 — MVP é parse best-effort).
- Paginação do fechamento transitivo no INDEX.md (cap `--max-objects` cobre
  o risco de explosão; paginação real vira contrato futuro se houver volume).
- Modo thick / Kerberos / wallet.
- `--recompile=auto` (seção 4.1 — cortado por política).

## 8. Pendências do design doc — resolvidas

- **Conexão**: thin via `db.py`, `--conn <alias>` ou env vars; sem DSN/
  credencial posicional (melhor que o design doc pedia).
- **Recompilação**: só manual (seção 4.1).
- **Multi-schema**: `ALL_*` com grants do usuário; visão parcial documentada
  no `INDEX.md` se faltar grant.
- **Volume**: caps explícitos (seção 4.4); fixture `FLOW_DEMO` cobre o aceite
  sem bater limite.
