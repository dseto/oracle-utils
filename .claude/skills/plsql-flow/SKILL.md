---
name: plsql-flow
description: Gera diagrama mermaid do caminho completo de execução de uma procedure/function Oracle 19c — chamadas recursivas até as folhas, triggers disparados, SQL dinâmico, dispatch de object types — com proteção anti-loop e modo dinâmico opcional (DBMS_HPROF). O grafo/mermaid é montado por um script Python determinístico e testado (pacote `plsqlflow/`); o assistente só interpreta o residual (SQL dinâmico irresolúvel, ambiguidade sem PL/Scope, narrativa). Usar quando o usuário pedir "o que essa procedure executa", "mapa/fluxo de execução", "call graph", "por onde esse código passa", ou fornecer procedure+parâmetros pedindo o caminho.
---

# /plsql-flow — mapa de execução de procedure/function

## Entrada
- Alvo: `[owner.]package.subprograma` ou `[owner.]procedure_standalone`.
- Parâmetros (opcionais): resolvem overload (via `ALL_ARGUMENTS`), anotam o nó raiz e habilitam o modo dinâmico.
- Opções: `max_depth` (default 10), `incluir_sys` (default: `DBMS_*`/`UTL_*` viram folhas), modo `estatico` (default) ou `dinamico`.

## Passo 1 — rodar o script (substitui a montagem manual do grafo)

```
python -m plsqlflow OWNER.OBJETO[.SUBPROGRAMA] --conn <alias> --json
```

Sem `--json` a saída é direto para humano: mermaid + linha de estatísticas +
lista de pontos cegos, já formatada. `--json` imprime o relatório completo
(`root`, `nodes`, `edges`, `stats`, `blind_spots`, `mermaid`) para o
assistente processar no Passo 2. Opções adicionais: `--max-depth N`
(default 10), `--node-budget N` (default 120).

**Conexão — `--conn` NUNCA leva senha na linha de comando.** Resolução:
1. `--conn <alias>` → lê `tools/flow-connections.json` (arquivo gitignored,
   formato `{"dev": {"user": "gestao", "dsn": "host:port/service"}}`) e a
   senha vem da variável de ambiente `PLSQLFLOW_PWD_<ALIAS>` (ex.:
   `PLSQLFLOW_PWD_DEV`).
2. Sem `--conn`: variáveis de ambiente diretas `PLSQLFLOW_USER`,
   `PLSQLFLOW_PWD`, `PLSQLFLOW_DSN`.

Identificadores (owner/objeto/subprograma) são validados por regex antes de
qualquer SQL; o pacote só executa os `SELECT`s embutidos em `sql/flow/*.sql`
(carregados por `plsqlflow/queries.py` — fonte única, os mesmos arquivos que
serviam o fluxo manual v1).

Isso substitui toda a montagem manual do grafo que a v1 fazia por
interpretação de LLM — hoje é 100% determinístico e coberto por teste. O que
o script faz internamente, por etapa (nenhuma dessas etapas passa por LLM):

- **Resolver o alvo**: casa aridade/tipos via `ALL_ARGUMENTS`
  (`resolve_target.sql`) — overload ambíguo vira nó com sufixo `#n`. Nome
  pode ser sinônimo: a cadeia (inclui `PUBLIC`, iterando até objeto base,
  `db_link` preenchido ou ciclo) é resolvida via `resolve_synonym.sql`
  (`resolve.resolve_synonym_chain`); `db_link` preenchido vira folha
  externa ("DB link").
- **Camada A — PL/Scope (preferida)**: `plscope_check.sql` confere se o
  objeto foi compilado com `IDENTIFIERS:ALL`/`STATEMENTS:ALL`. Se sim,
  `plscope_calls.sql` (`ALL_IDENTIFIERS`) dá as chamadas já resolvidas pelo
  compilador (overloads e escopo corretos) e `plscope_statements.sql`
  (`ALL_STATEMENTS`) dá o SQL embutido — nós nascem com `confidence:
  "compiler"`.
- **Camada B — léxica sobre ALL_SOURCE (fallback sem PL/Scope — biblioteca
  pronta, AINDA NÃO ligada ao pipeline automático do script)**: o script
  confere `plscope_check.sql` antes de expandir qualquer objeto; se o
  objeto-alvo não estiver compilado com `IDENTIFIERS:ALL`/`STATEMENTS:ALL`,
  `python -m plsqlflow` **falha alto** com uma mensagem explicando o motivo
  e a opção de recompilar — nunca produz um grafo silenciosamente
  incompleto. `fetch_source.sql` (spec+body de `ALL_SOURCE`, primeira linha
  do body começando com `wrapped` = código ofuscado, kind `"wrapped"`),
  `deps_direct.sql`/`ALL_DEPENDENCIES` e `plsqlflow/lexical.py`
  (tokeniza — remove comentários e literais preservando-os para o
  `dynsql.py`, lista candidatos a chamada e classifica cada um em
  `confidence: "lexical"`/`"heuristic"`) já existem prontos e testados
  offline como biblioteca (`tests/test_plsqlflow_lexical.py`), mas ligar
  essa camada à travessia automática do grafo (`plsqlflow/graph.py`) é
  trabalho de um contrato futuro — achado registrado no blind review do
  contrato `plsqlflow-py`. Até lá, sem PL/Scope disponível o assistente
  monta esse trecho do grafo manualmente (fluxo v1: ler `ALL_SOURCE`,
  aplicar a mesma lógica de tokenização/candidatos à mão) se o usuário
  quiser prosseguir mesmo assim.
- **Triggers e cascata FK**: para cada `INSERT`/`UPDATE`/`DELETE` estático
  sobre uma tabela, `triggers_for_tables.sql` (`ALL_TRIGGERS`) traz
  triggers `ENABLED` com evento/timing compatível → aresta pontilhada
  rotulada evento/timing, recursando no corpo do trigger. `DELETE` também
  consulta `fk_cascade.sql` (`ALL_CONSTRAINTS`, `ON DELETE CASCADE`/`SET
  NULL`) e liga as tabelas filhas em cascata.
- **Orientação a objeto (`OVERRIDING`)**: chamada a método de object type
  consulta `type_hierarchy.sql` (`ALL_TYPE`) e lista TODOS os subtipos com
  `OVERRIDING` do método como candidatos — dispatch é runtime, não
  resolvível estaticamente, então todos entram como arestas tracejadas
  `override?` (nó `confidence: "heuristic"`, nunca um só "vencedor").
- **SQL dinâmico** (`EXECUTE IMMEDIATE`, `OPEN ... FOR`,
  `DBMS_SQL.PARSE`): `plsqlflow/dynsql.py` faz *constant folding* —
  concatenação só de literais (mais no máximo um nível de indireção via
  variável atribuída antes) vira SQL resolvido e reintegrado ao grafo como
  nó `dynsql` (`confidence: "lexical"`). Qualquer identificador que não é
  literal e não é constante conhecida torna o trecho **irresolúvel**: nó
  `dynsql` com `confidence: "heuristic"` e o fragmento literal visível, sem
  adivinhar o valor de variável/parâmetro.
- **Anti-loop e limites**: `visited` set por nó — nunca expande o mesmo nó
  duas vezes. Aresta para um nó já no caminho atual (`path`) vira aresta
  `kind: "recursion"` (vermelha no mermaid), sem re-expandir — cobre
  recursão direta e mútua. `max_depth` atingido → nó marcado
  `truncated`/"(+N niveis?)". Orçamento (`--node-budget`, default 120) —
  estouro colapsa o excedente num nó agregado por chamador/tipo, contado
  em `stats.collapsed_groups`.
- **Mermaid + legenda + estatísticas**: `plsqlflow/mermaid.py` gera o
  `flowchart TD` pronto (retângulo = proc/func; cilindro = tabela/view;
  hexágono = remoto/DB link; trapézio = trigger; nó classe `dynsql` =
  tracejado; aresta sólida = estática; tracejada = dinâmica/override/
  trigger/cascade; vermelha = recursão) com a legenda já embutida como
  comentário `%%` no final do diagrama. `stats` traz `nodes`, `edges`,
  `depth_reached`, `collapsed_groups` e `pct_plscope` (% de nós resolvidos
  pelo compilador vs. léxico/heurístico).

## Passo 2 — o que fazer com a saída JSON (aqui, e só aqui, entra o LLM)

O relatório JSON tem o campo `blind_spots`, lista de
`{"type": "dynsql_unresolved"|"override_open", "at": "owner.objeto:linha", ...}`.
O assistente decide a ação por caso:

- **`blind_spots` vazio** → apresentar o `mermaid` + `stats` direto ao
  usuário. **Zero interpretação de LLM no grafo em si** — o script já
  entregou tudo determinístico.
- **Item `dynsql_unresolved`** (campo `fragment`) → o LLM lê o `fragment`/
  `at` e escreve UM comentário curto sobre o que aquele SQL dinâmico
  *parece* fazer — não re-deriva o grafo, só comenta, com a ressalva
  explícita de que é palpite (não é possível confirmar sem o valor real da
  variável em runtime).
- **Item `override_open`** (campo `candidates`, todos já no grafo como
  arestas `override?`) → o LLM pode comentar qual subtipo parece mais
  provável dado o contexto de domínio, mas deixa claro que o dispatch é
  runtime e TODOS os candidatos continuam no diagrama — o comentário é só
  uma nota, nunca remove os demais candidatos.
- **Sem PL/Scope disponível** (o script falha alto — `RuntimeError` de
  `plscope_check.sql`, ver Passo 1 — em vez de entregar grafo incompleto
  silenciosamente): oferecer ao usuário o script de recompilação com
  `PLSCOPE_SETTINGS='IDENTIFIERS:ALL, STATEMENTS:ALL'` — **entregar, nunca
  executar** (invalida cursores e recompila dependentes), só roda com
  confirmação explícita. Se o usuário preferir não recompilar, o assistente
  monta a Camada B manualmente (ver nota na seção da Camada B acima —
  `plsqlflow/lexical.py` existe como biblioteca testada mas ainda não está
  ligada à travessia automática do grafo); os nós montados assim entram
  como `confidence: "lexical"`/`"heuristic"` e SÓ os `"heuristic"` (lista
  fechada) precisam de arbitragem do LLM, nunca re-derivar o grafo do zero.

Grafo grande (`stats.collapsed_groups > 0` ou muitos nós) → Artifact HTML
com o mermaid pronto do JSON (um bloco por subárvore se necessário) — o
Artifact é só apresentação, não recalcula nada.

## Modo dinâmico (DBMS_HPROF) — opcional

Caminho REAL para os parâmetros dados. Fica fora do pacote Python
(não-objetivo do contrato T-01..T-05): segue como scripts entregues, nunca
executados automaticamente. **Executa o código de verdade — efeitos
colaterais reais**: DML do código acontece, autonomous transactions não
sofrem rollback, DDL quebra a transação. **Só com confirmação explícita do
usuário.**

1. [hprof_setup.sql](../../../sql/flow/hprof_setup.sql) — script de setup
   (grant + tabelas de análise `DBMS_HPROF`) **entregue ao usuário**, nunca
   executado pela skill.
2. Wrapper de execução com `START_PROFILING`/`STOP_PROFILING` + chamada com
   os parâmetros — entregue.
3. [hprof_report.sql](../../../sql/flow/hprof_report.sql) — consulta as
   tabelas `DBMSHP_*` com o `runid` (modo tabela: `STOP_PROFILING` já
   grava; sem `ANALYZE`).
4. Sobrepor ao grafo estático (do JSON do Passo 1): caminho executado em
   verde com contagens/tempo; nó executado que a análise estática não
   previu = SQL dinâmico capturado (listar como feedback ao usuário).

## Regras
- Análise estática = somente `SELECT` em views de dicionário + o script
  Python determinístico (`python -m plsqlflow`) para montar o grafo/mermaid
  — **nenhuma chamada a LLM no caminho crítico do grafo**. LLM só entra no
  Passo 2 (comentar `blind_spots` e arbitrar nós `heuristic`).
- Recompilação PL/Scope, setup HPROF e execução do alvo continuam scripts
  entregues, nunca executados sem confirmação explícita do usuário.
- Compatibilidade 19c: `ALL_STATEMENTS` existe em 19c; nenhuma feature
  21c+ nas queries de `sql/flow/*.sql` nem no pacote `plsqlflow/`.
- Privilégio insuficiente (`ORA-00942`): avisar limitação e seguir com o
  que `ALL_*` enxerga (mesmo comportamento de fallback `DBA_`→`ALL_` do
  resto do toolkit).
