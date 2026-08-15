---
slug: depgraph-scale
approved_by: daniel.rubens.seto@gmail.com
approved_at: 2026-08-15T20:46:32Z
stop_conditions:
  - "qualquer necessidade de DML/DDL no banco (o contrato e 100% somente-leitura)"
  - "necessidade de alterar plsqlflow/graph.py, mermaid.py ou report.py (modulos congelados por golden test do contrato plsqlflow-py)"
  - "qualquer mudanca que altere os bytes de tests/fixtures/depgraph_golden -- o grafo pequeno tem que continuar rendendo identico; se uma tarefa nao conseguir evitar isso, parar e devolver ao humano"
  - "T-02 (caps e fronteira sob lote) falhar a equivalencia com o caminho por-no: e sinal de que o recorte por nivel esta errado, nao de que o teste esta chato -- parar e devolver, nao afrouxar o teste"
  - "3 falhas consecutivas do mesmo verify_cmd sem causa nova identificada entre as tentativas"
---

# Spec: grafo de dependencias em sistemas gigantes

## Resumo executivo
O grafo de dependencias funciona bem para raizes pequenas, mas nao serve
para os sistemas reais que o usuario precisa analisar (10 a 50 mil
objetos). Duas coisas o impedem: a extracao faz uma consulta ao banco por
objeto (20 mil objetos = 20 mil idas ao banco, e a latencia domina tudo),
e o INDEX.md vira uma lista de milhares de linhas que ninguem — nem o
proprio assistente — consegue consumir. Este contrato faz o grafo gigante
**gerar** (consultas agrupadas por nivel da busca), **ser consumivel**
(indice sumarizado, com os objetos mais conectados em destaque e o
fechamento transitivo separado por schema) e **caber no caso real**
(limite de tamanho configuravel e varias raizes num grafo unico de
subsistema).

## Base ja pronta (commit 8792fba, primeiro da branch)
Nao faz parte das tarefas abaixo — ja esta em disco, testado e verde:
- `sql/flow/deps_direct_batch.sql`, `plscope_statements_batch.sql`,
  `fetch_source_batch.sql`, os fetchers correspondentes em `extract.py`,
  `BATCH_CHUNK_SIZE`/`chunk_names`, tudo registrado em `queries.py`;
- `depgraph_render.py` indexando arestas por `from_ref`/`to_ref` uma vez
  em vez de refiltrar a lista inteira por no.
Nenhum caminho de producao consome as queries em lote ainda: quem passa a
usa-las sao T-01 e T-03.

## Escopo

1. **BFS por nivel (T-01).** `_DepGraphEngine.run()` passa a drenar a fila
   um NIVEL inteiro por vez: agrupa os pendentes por owner, fatia com
   `chunk_names` e faz uma chamada `deps_direct_batch` por owner/chunk, em
   vez de uma por objeto. Round-trips caem de O(nos) para
   O(niveis x owners x chunks). O metodo em lote e OPCIONAL no Protocol
   `DepExtractor`: extractor que nao o implementa (os fakes dos testes
   existentes) cai no caminho por-no atual, e os dois caminhos tem que
   produzir `DepGraphResult` identico. Resolucao de sinonimo continua
   por-no (so dispara quando o objeto nao tem dependencia nenhuma).

2. **Limites e fronteira sob lote (T-02).** Tarefa separada de proposito:
   e onde o recorte por nivel pode mudar comportamento sem ninguem notar.
   Hoje `max_objects` e conferido ao desenfileirar, um objeto por vez, e
   quem estoura o limite entra em `not_expanded` com `truncation_reason`
   preenchido. Com lote, as dependencias de objetos que talvez nao caibam
   ja foram buscadas. O observavel tem que ser identico: mesmos objetos em
   `not_expanded`, mesmo motivo, mesmo `truncated`. Idem para a fronteira:
   objeto em `stop_schemas` ou com prefixo `DBMS_`/`UTL_` nunca pode
   entrar num `object_list` enviado ao banco.

3. **Enriquecimento em lote (T-03).** `_build_depgraph_result` em `cli.py`
   busca hoje `statements` e `source` por objeto (duas idas por objeto
   PL/SQL). Passa a agrupar por owner e buscar em lote, reagrupando as
   linhas por objeto antes de chamar `depgraph_enrich`, que nao muda. Mesmo
   contrato de fallback e equivalencia da T-01.

4. **INDEX consumivel (T-04).** Acima de um limiar de nos
   (`--index-split`, default 1000): `INDEX.md` vira sumario — estatisticas,
   PONTOS CEGOS, secao `## Hubs` (os 20 nos de maior grau entrada+saida,
   com as contagens) e a lista dos arquivos particionados; o fechamento
   transitivo completo vai para `INDEX-<OWNER>.md`, um por schema.
   Regeracao remove `INDEX-*.md` orfaos, mesma regra dos `nodes/*.md`.
   Abaixo do limiar, o formato atual nao muda um byte.

5. **Caps e multiplas raizes (T-05).** Default de `--max-objects` sobe de
   500 para 5000 e `--max-objects 0` desliga o cap (o aviso por
   `max_depth` continua). O subcomando aceita varias raizes
   (`depgraph GESTAO.PKG_A GESTAO.PKG_B --name modulo-financeiro`): todas
   semeiam a MESMA BFS e saem num grafo unico em `<output>/<name>/`
   (`--name` obrigatorio com mais de uma raiz; com uma so, mantem
   `<OWNER>.<OBJETO>`). `meta.json` registra todas as raizes. A SKILL.md
   ganha secao "sistemas gigantes" com lote, limiar do INDEX, multi-raiz e
   a recomendacao de manter `oracle-graph/` fora do git.

## Criterios de aceitação
- O mesmo fixture percorrido por extractor COM e SEM os metodos em lote
  produz `DepGraphResult` identico, e com lote o numero de chamadas de
  dependencia nao cresce com o numero de nos —
  `pytest tests/test_depgraph_bfs_batch.py -q`
- `max_objects`/`max_depth` pequenos produzem o MESMO `not_expanded`,
  `truncated` e `truncation_reason` nos dois caminhos, e nenhum objeto de
  fronteira aparece em `object_list` enviado ao extractor —
  `pytest tests/test_depgraph_bfs_limits.py -q`
- O enriquecimento em lote produz as mesmas arestas, `snippets` e
  classificacao de SQL dinamico do caminho por-objeto, com numero de
  chamadas independente da quantidade de objetos —
  `pytest tests/test_depgraph_enrich_batch.py -q`
- Grafo sintetico acima do limiar gera INDEX.md sumario (stats + PONTOS
  CEGOS + hubs) mais `INDEX-<OWNER>.md` por schema, regeneravel byte a
  byte e sem orfaos; abaixo do limiar o formato atual nao muda —
  `pytest tests/test_depgraph_index_split.py -q`
- Multiplas raizes geram um grafo unico nomeado por `--name`, e
  `--max-objects 0` desliga o cap sem truncar —
  `pytest tests/test_depgraph_multiroot.py -q`
- Suite inteira verde — `pytest -q -rs`

## Não-objetivos
- Regeracao incremental por `last_ddl_time` (contrato proprio, maior: e o
  que torna o rerun diario viavel, mas nao e pre-requisito de gerar).
- Pool de conexoes / extracao paralela (o lote resolve a latencia; paralelo
  so depois, se medicao mostrar necessidade).
- Arquivo de arestas ENTRE grafos distintos (multi-raiz num grafo unico ja
  cobre o caso subsistema).
- `--roots-file` (raizes vem por argumento posicional).
- Paginacao do INDEX alem da particao por owner.
- Expansao atraves de db link (continua folha opaca).
- Qualquer mudanca no formato dos `nodes/*.md`, `edges.jsonl`,
  `recompile.sql` ou `meta.json` (fora o campo de raizes em `params`).

## Unknowns
- `.harness/harness.yaml` no `main` nao tem `extra_allowed_commands`: os
  comandos liberados durante a sessao anterior (`ruff check` com alvo,
  `git switch`, `git checkout -b`, `python -c`, `gh pr`) sumiram ao trocar
  de branch. Precisa ser reescrito pelo usuario no terminal dele (o
  runtime floor proibe o agente de editar o plano de controle) e
  commitado, senao a fricção volta a cada troca de branch. Nao bloqueia as
  tarefas; bloqueia a fluidez.
