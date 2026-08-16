---
slug: depgraph-granular
approved_by: daniel.rubens.seto@gmail.com
approved_at: 2026-08-16T00:04:29Z
stop_conditions:
  - "Travessia nao terminar em fixture com ciclo (timeout do teste estourar) -- parar e redesenhar o visited-set, NUNCA aumentar o timeout nem por cap de profundidade para mascarar"
  - "Contadores da secao COBERTURA nao fecharem -- parar e achar a causa, NUNCA ajustar o numero para fechar"
  - "Precisar alterar plsqlflow/graph.py ou o modo flow (congelados por golden test do contrato plsqlflow-py) -- parar e devolver ao humano"
  - "Qualquer DDL/DML contra o banco (CREATE/ALTER/DROP/INSERT/UPDATE/DELETE) -- parar e pedir confirmacao explicita; as tarefas deste contrato sao somente-leitura"
  - "3 falhas consecutivas da mesma suite de teste -- parar"
---

# Spec: depgraph granular -- mapa de processo end-to-end em grao subprograma

## Resumo executivo

Hoje o depgraph responde "de quais OBJETOS este objeto depende". Quem precisa
reescrever um processo Oracle em outra tecnologia precisa de outra resposta:
"partindo desta procedure, o que exatamente acontece, ate o fim". Este
contrato entrega isso -- um mapa que comeca numa procedure especifica, desce
recursivamente por todas as chamadas que ela faz (atravessando quantos
packages forem necessarios), e registra por SUBPROGRAMA quais tabelas e
colunas sao lidas e escritas, quais triggers disparam, qual estado de package
e compartilhado e onde ha SQL dinamico.

A garantia que sustenta o uso: o mapa nunca omite em silencio. Onde a analise
nao consegue a precisao maxima, ela declara o motivo e continua o caminho em
granularidade menor -- e a contagem final tem que fechar, senao o mapa nao e
gravado.

## Escopo

Referencia completa do desenho: docs/plano-depgraph-granular.md (aprovado).

Novo modo `--granular` do subcomando `depgraph`, com raiz em subprograma
(`owner.objeto.subprograma`) ou objeto (`owner.objeto`, que semeia os
subprogramas publicos da spec). Sem a flag, o comportamento atual permanece
identico byte a byte.

Base tecnica (provada contra a conexao dev, ver secao 2 do plano): PL/Scope
expoe uma arvore de contexto (`usage_id`/`usage_context_id`) que permite
atribuir cada CALL e cada statement ao subprograma que o envolve, e a coluna
`signature` resolve o destino exato de cada chamada -- inclusive sobrecarga e
inclusive entre objetos diferentes. Isso torna a travessia recursiva
inter-package possivel sem heuristica.

Quatro propriedades sao requisito, nao detalhe:

1. **Atribuicao exata**: nenhuma aresta falsa. O modo flow atual replica as
   chamadas do pacote inteiro em cada no de subprograma (limitacao declarada
   em plsqlflow/graph.py:9-20); aqui isso nao pode acontecer.
2. **Profundidade total**: sem cap de profundidade default no modo granular.
   `--max-depth`/`--max-objects` continuam existindo como opt-in de
   seguranca, e qualquer truncamento e declarado item a item.
3. **Terminacao sob ciclo**: referencia circular e esperada (recursao mutua,
   ciclo de trigger, sinonimo circular). A travessia termina sempre, por
   visited-set global marcado no enqueue, e o ciclo aparece DECLARADO no
   grafo -- nunca loop infinito, nunca ciclo omitido.
4. **Anti-omissao no elo fraco**: objeto sem PL/Scope utilizavel ou wrapped
   no meio da cadeia nao interrompe nem some -- expande em grao objeto pelo
   motor atual, marcado com o motivo, e a travessia continua atraves dele.

Inclui tambem a correcao de um defeito de omissao silenciosa achado durante o
levantamento: `_DepGraphEngine.has_plscope` (plsqlflow/depgraph.py:439-447)
aceita `IDENTIFIERS:ALL` sem checar `STATEMENTS:ALL`, entao um objeto assim e
hoje tratado como coberto enquanto todo o SQL dele some do grafo, sem entrar
em PONTOS CEGOS nem em needs_recompile.

## Criterios de aceitacao

- Atribuicao por subprograma correta sobre a arvore de contexto, incluindo
  subprograma aninhado e blocos sem subprograma envolvente: `pytest tests/test_attribute.py -q`
- Queries novas/alteradas devolvem `usage_id`/`usage_context_id`/`signature` e
  sao tipadas em extract.py: `pytest tests/test_extract_plscope_tree.py -q`
- Travessia recursiva em grao subprograma partindo de uma raiz
  `owner.objeto.subprograma`, cruzando packages, com zero aresta falsa, e que
  TERMINA em fixture com ciclo (recursao mutua inter-package): `pytest tests/test_procgraph_bfs.py -q`
- Objeto sem PL/Scope no meio da cadeia nao interrompe a travessia: o que
  vem depois dele continua aparecendo no grafo, e o no fica marcado com o
  motivo do rebaixamento: `pytest tests/test_procgraph_fallback.py -q`
- Acesso a tabela por subprograma com colunas vindas do compilador, estado de
  package com quem le/escreve, e trigger de tabela escrita entrando como
  subprograma: `pytest tests/test_procgraph_access.py -q`
- Objeto compilado so com IDENTIFIERS:ALL sai reportado como NAO coberto e
  entra em needs_recompile: `pytest tests/test_depgraph_plscope_settings.py -q`
- Todo ciclo do grafo (recursao direta, mutua, inter-package, de trigger)
  reportado como grupo SCC: `pytest tests/test_procgraph_cycles.py -q`
- Secao COBERTURA com contagem que fecha (soma diferente = erro, nao aviso),
  CLI aceitando alvo de 3 partes com `--granular`, e saida sem a flag
  identica a atual: `pytest tests/test_procgraph_render.py tests/test_cli_granular.py -q`
- Suite completa e lint limpos ao final: `pytest -q`

## Nao-objetivos

- Nao alterar plsqlflow/graph.py nem o modo flow (congelados por golden test
  do contrato plsqlflow-py)
- Nao melhorar a resolucao de SQL dinamico alem dos tres niveis atuais
  (resolved/partial/opaque) -- so passa a ser reportada por subprograma
- Nenhum dado de runtime (profiling, AWR, contagem de execucao)
- Nenhuma geracao de codigo da migracao -- o entregavel e o mapa, nao o port
- Fixture DDL no banco (T-09 do plano) fica FORA deste contrato: exige
  confirmacao explicita do usuario por ser DDL. As tarefas aqui provam com
  fixture sintetica; a validacao live contra 19c fica para depois

## Unknowns

- Nenhum. O profile do repo nao reportou unknowns (test_command `pytest`,
  test_glob `tests/**/*.py`, lint `ruff check .`, todos com evidencia em
  pyproject.toml).
