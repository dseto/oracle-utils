# Plans: depgraph-scale

Base do contrato: commit `8792fba` (queries em lote + render indexado),
ja em disco e verde. Nenhuma tarefa abaixo reimplementa isso.

Ordem: T-01 e T-03 em paralelo (`depgraph.py` vs `cli.py`, superficies
disjuntas). T-02 depois de T-01 (mesmo modulo, e o recorte delicado que
merece evidencia propria). T-04 depois de T-03. T-05 fecha.

Decisoes fixadas no spec: metodos em lote OPCIONAIS no Protocol, com
fallback por-no preservado — os fakes dos testes existentes nao mudam, e
o fallback vira o proprio teste de equivalencia; chunk de 200 nomes
(`extract.BATCH_CHUNK_SIZE`); sinonimo continua por-no; limiar do INDEX
via `--index-split` (default 1000, registrado em `meta.params`); hubs =
top 20 por grau entrada+saida; regeracao remove `INDEX-*.md` orfaos;
`--max-objects` default 500 -> 5000, `0` = sem cap; multi-raiz exige
`--name`.

## [T-01] Percorrer as dependências consulta o banco uma vez por nível da busca em vez de uma vez por objeto, sem mudar o grafo resultante
- files: `plsqlflow/depgraph.py`, `tests/test_depgraph_bfs_batch.py`
- verify: `pytest tests/test_depgraph_bfs_batch.py tests/test_depgraph_bfs.py tests/test_depgraph_triggers.py -q`

## [T-02] O limite de tamanho e a fronteira de schema continuam se comportando exatamente como antes quando a busca passa a ser em lote
- files: `plsqlflow/depgraph.py`, `tests/test_depgraph_bfs_limits.py`
- verify: `pytest tests/test_depgraph_bfs_limits.py tests/test_depgraph_bfs.py -q`
- depends: T-01

## [T-03] Classificar acessos a tabela e SQL dinâmico busca fonte e statements em lotes por schema, com o mesmo resultado de antes
- files: `plsqlflow/cli.py`, `tests/test_depgraph_enrich_batch.py`
- verify: `pytest tests/test_depgraph_enrich_batch.py tests/test_depgraph_cli.py -q`

## [T-04] Acima de um limiar de nós o índice vira um sumário com os objetos mais conectados, e o fechamento transitivo é separado por schema
- files: `plsqlflow/depgraph_render.py`, `plsqlflow/cli.py`, `tests/test_depgraph_index_split.py`
- verify: `pytest tests/test_depgraph_index_split.py tests/test_depgraph_render.py -q`
- depends: T-03

## [T-05] O grafo aceita várias raízes num resultado único, o limite de objetos pode ser ampliado ou desligado, e a skill documenta o uso em sistemas gigantes
- files: `plsqlflow/cli.py`, `plsqlflow/depgraph.py`, `tests/test_depgraph_multiroot.py`, `.claude/skills/oracle-dependency-graph/SKILL.md`, `docs/plano-oracle-dependency-graph.md`, `tests/test_depgraph_cli.py`
- verify: `pytest -q -rs`
- depends: T-02, T-04
