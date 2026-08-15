---
slug: oracle-depgraph
approved_by: daniel.rubens.seto@gmail.com
approved_at: 2026-08-15T13:50:00Z
stop_conditions:
  - "3 falhas consecutivas do mesmo verify_cmd sem progresso"
  - "python-oracledb nao conecta no dev (localhost:1521/XEPDB1) apos 2 tentativas de diagnostico"
  - "fixture FLOW_DEMO ausente/invalida no dev e recriacao exigiria DDL nao autorizado"
  - "qualquer necessidade de DML/DDL no banco (o contrato e 100% somente-leitura)"
  - "necessidade de alterar plsqlflow/graph.py, mermaid.py ou report.py (modulos congelados por golden test de outro contrato)"
---

# Spec: Grafo de dependencias Oracle persistido em disco para consumo por grep

## Resumo executivo

Hoje, toda vez que o assistente precisa saber "quem escreve na tabela X" ou
"o que quebra se eu alterar a procedure Y", ele reabre conexao com o banco e
refaz as mesmas consultas de dicionario, gastando contexto e tempo a cada
pergunta. Esta demanda gera, uma unica vez por objeto-raiz, um conjunto de
arquivos em disco (um arquivo por objeto, mais um indice e um arquivo de
arestas) que responde essas perguntas com um `grep` — sem banco, sem
reconsulta. O grafo diz explicitamente onde ele NAO enxerga (SQL montado em
tempo de execucao, objeto sem PL/Scope), para o assistente nunca afirmar
cobertura que nao tem.

## Escopo

Novo subcomando `python -m plsqlflow depgraph <owner.objeto>` no pacote
`plsqlflow/` ja existente, e nova skill `/oracle-dependency-graph` que
documenta como consumir o resultado. O comando percorre as dependencias a
partir de um objeto raiz (busca em largura sobre `ALL_DEPENDENCIES`),
enriquece cada objeto PL/SQL com dados de PL/Scope (chamadas, statements
SQL com linha e tipo de operacao), classifica SQL dinamico em tres niveis
(resolvido / parcial / opaco), inclui triggers das tabelas escritas como
nos proprios da cadeia, e grava tudo em `oracle-graph/<OWNER>.<RAIZ>/`.

Plano de referencia com o desenho completo, decisoes e justificativas:
`docs/plano-oracle-dependency-graph.md` (rev. 2, pos-revisao independente).

Reuso obrigatorio (nao reimplementar): conexao e credencial
(`plsqlflow/db.py`), fetchers de dicionario (`plsqlflow/extract.py`),
cadeia de sinonimos (`plsqlflow/resolve.py`), SQL dinamico literal
(`plsqlflow/dynsql.py`), scanner lexico (`plsqlflow/lexical.py`), e as
queries existentes em `sql/flow/`. As duas queries novas derivam de queries
ja existentes em `sql/schema/` e `sql/viz/` (ver plano, secao 2.2).

Somente leitura: o contrato inteiro executa apenas SELECT. Objetos sem
PL/Scope geram um script `recompile.sql` para o DBA revisar — nunca
executado pelo pipeline.

## Criterios de aceitacao

- Extracao tipada e somente-leitura das duas fontes novas de dicionario
  (catalogo de objetos com data de ultimo DDL, colunas de tabela), derivadas
  das queries existentes, com binds documentados: `pytest tests/test_depgraph_unit.py tests/test_conventions.py -q`
- Busca em largura devolve cada objeto uma unica vez mesmo com pacote
  spec+body, auto-referencia e ciclo; para nas fronteiras configuradas e nos
  limites de tamanho, sem truncar em silencio: `pytest tests/test_depgraph_bfs.py -q`
- Cada acesso a tabela vira aresta tipada (leitura ou escrita, com linha do
  fonte) e cada ocorrencia de SQL dinamico aparece classificada em um dos
  tres niveis — nenhuma silenciada: `pytest tests/test_depgraph_enrich.py tests/test_plsqlflow_lexical.py -q`
- Triggers das tabelas escritas entram como nos com suas proprias
  dependencias expandidas, e rodar duas vezes contra o mesmo banco produz
  arquivos byte-identicos: `pytest tests/test_depgraph_triggers.py tests/test_depgraph_render.py -q`
- Linha de comando aceita o novo subcomando sem quebrar o uso atual, nunca
  recebe senha por argumento, e devolve codigo de saida distinto para cada
  situacao (sucesso, falta PL/Scope, raiz inexistente, erro de conexao):
  `pytest tests/test_depgraph_cli.py -q`
- Skill nova documenta os padroes de grep de consumo, as skills e agentes
  existentes passam a apontar para o grafo antes de reconsultar o banco, e a
  suite inteira do repo continua verde: `pytest -q -rs`

## Nao-objetivos

- Executar `ALTER ... COMPILE` automaticamente (`--recompile=auto`): cortado
  por politica — o pipeline entrega `recompile.sql` para revisao humana.
- Classificacao assistida por LLM de SQL dinamico parcial (`--llm-assist`).
- Contexto estrutural (dentro de loop / branch / handler) nas arestas de
  chamada via `USAGE_CONTEXT_ID`.
- Colunas de escrita extraidas via `ALL_IDENTIFIERS` (MVP faz parse
  best-effort do texto do statement; ausencia de coluna nao e erro).
- Paginacao do fechamento transitivo no indice para cadeias muito grandes.
- Seguir DB link (entra como no folha opaco).
- Modo thick do driver, Kerberos ou wallet.
- Analise de plano de execucao ou tuning.
- Alterar `plsqlflow/graph.py`, `mermaid.py` ou `report.py` (congelados por
  golden test do contrato `plsqlflow-py`).

## Unknowns

- Nenhum. O `analyze` nao reportou unknowns (test_command `pytest`,
  lint_command `ruff check .`, ambos com evidencia em `pyproject.toml`).
