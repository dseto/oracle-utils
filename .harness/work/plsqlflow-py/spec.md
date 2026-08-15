---
slug: plsqlflow-py
approved_by: daniel.rubens.seto@gmail.com
approved_at: 2026-08-15T11:45:00Z
stop_conditions:
  - "3 falhas consecutivas do mesmo verify_cmd sem progresso"
  - "python-oracledb nao conecta no dev (localhost:1521/XEPDB1) apos 2 tentativas de diagnostico"
  - "fixture FLOW_DEMO ausente/invalida no dev e recriacao exigiria DDL nao autorizado"
  - "qualquer necessidade de DML/DDL no banco alem da ja autorizada (fixture FLOW_DEMO existente)"
---

# Spec: Nucleo deterministico do /plsql-flow em Python

## Resumo executivo
Hoje o /plsql-flow monta o diagrama de execucao "na cabeca" do assistente: os
scripts SQL so extraem dados, e toda a construcao do grafo (recursao, overloads,
triggers, mermaid) e interpretacao de LLM — nao ha teste que prove que o
diagrama esta certo. Esta demanda reescreve o nucleo como pacote Python
deterministico: mesma entrada produz sempre o mesmo diagrama, provado por teste
byte a byte. O assistente passa a atuar so onde julgamento e insubstituivel
(SQL dinamico montado em variavel, ambiguidade sem PL/Scope, narrativa).

## Escopo
Criar pacote `plsqlflow/` (raiz do repo) com: conexao Oracle via python-oracledb
modo thin (19c/21c, sem client); carga dos SQLs existentes de `sql/flow/*.sql`
como fonte unica; extracao tipada do dicionario; resolucao de alvo, overloads e
sinonimos; construcao do grafo com visited set anti-loop, deteccao de recursao
(direta e mutua), triggers por DML, cascata FK, hierarquia de object types com
OVERRIDING, orcamento de ~120 nos com colapso; constant folding de SQL dinamico
(literais + constantes de spec de package); camada lexica mecanica (fallback sem
PL/Scope) com tokenizacao e filtro por ALL_DEPENDENCIES, marcando confianca por
no; render mermaid com legenda; relatorio JSON (grafo, stats, blind_spots,
unresolved); CLI `python -m plsqlflow` com `--json` e `--dump-fixtures`.
Atualizar a SKILL.md do /plsql-flow para rodar o script primeiro e reservar LLM
ao residual. Credenciais nunca em linha de comando: config gitignored
(`tools/flow-connections.json`) + senha via variavel de ambiente
`PLSQLFLOW_PWD_<ALIAS>`. Codigo compativel Python 3.9+; SQL compativel 19c
(nenhum texto novo de SQL — reuso dos arquivos ja validados).

## Criterios de aceitacao
- Carga de queries, guarda de conexao (identificadores validados, so SELECTs do
  repositorio de queries) e extracao tipada passam offline:
  `pytest tests/test_plsqlflow_unit.py -q`
- Grafo deterministico (recursao mutua marcada, overload resolvido por
  assinatura, trigger e cascata FK no grafo, OVERRIDING como candidatos,
  visited set, max_depth, colapso por orcamento) provado offline com fixtures:
  `pytest tests/test_plsqlflow_graph.py -q`
- Constant folding de SQL dinamico (literal puro resolve; literal+constante de
  spec resolve; variavel marca irresoluvel) e camada lexica mecanica
  (comentarios/literais removidos, candidatos filtrados por dependencias,
  confianca atribuida) passam offline: `pytest tests/test_plsqlflow_lexical.py -q`
- Mermaid gerado do FLOW_DEMO identico byte a byte ao golden file, a partir de
  fixtures JSON gravadas do dev: `pytest tests/test_plsqlflow_golden.py -q`
- SKILL.md v2 documenta o fluxo script-primeiro/LLM-residual com links validos,
  e evidencia e2e contra o dev real existe (mermaid do FLOW_DEMO gerado pelo
  CLI, contagens conferidas): `pytest tests/test_plsqlflow_skill.py -q`
- Suite completa (inclui os 22 testes existentes) verde: `pytest -q`

## Nao-objetivos
- Parser PL/SQL completo (gramatica) — camada lexica e tokenizador + filtro,
  residual documentado vai para LLM.
- Resolver dispatch OO em runtime — impossivel estaticamente; lista candidatos.
- Executar recompilacao PL/Scope, setup HPROF ou o codigo-alvo — scripts
  continuam entregues ao usuario, nunca executados.
- Overlay HPROF automatizado no CLI (fica para demanda futura; skill segue
  descrevendo o modo dinamico manual).
- Publicacao do pacote (pip/PyPI) ou instalacao via pyproject — import direto
  do repo basta.

## Unknowns
- (nenhum — profile sem unknowns)
