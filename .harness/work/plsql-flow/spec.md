---
slug: plsql-flow
approved_by: daniel.rubens.seto@gmail.com
approved_at: 2026-08-15T10:16:00Z
stop_conditions:
  - "3 falhas consecutivas do mesmo verify_cmd sem progresso"
  - "Conexao MCP sqlcl indisponivel ou banco dev fora do ar"
  - "Erro de privilegio (ORA-01031/ORA-00942) que exigir grant novo no dev"
---

# Spec: skill /plsql-flow — mapa de execução de procedure/function

## Resumo executivo
Hoje, entender o que uma procedure Oracle executa de ponta a ponta (quem ela chama, que triggers dispara, que SQL dinâmico esconde) exige leitura manual de código. A skill /plsql-flow recebe uma procedure/function e seus parâmetros e devolve um diagrama mermaid do caminho completo de execução — recursivo, sem travar em loops, marcando visualmente o que não é resolvível estaticamente (SQL dinâmico, dispatch de object types). Opcionalmente, executa o código com DBMS_HPROF e sobrepõe o caminho real ao estático.

## Escopo
Conforme [docs/plano-plsql-flow.md](../../../docs/plano-plsql-flow.md):
- Biblioteca de queries `sql/flow/` (PL/Scope, ALL_SOURCE, dependencies, triggers, cascata FK, hierarquia de tipos, sinônimos, overloads).
- SKILL.md com o fluxo de duas camadas (PL/Scope preferida, léxica fallback), tratamento de casos complexos (SQL dinâmico com constant folding, OO com candidatos a override, triggers incluindo INSTEAD OF e cascata de FK, overloads via ALL_ARGUMENTS, sinônimos, DB links, wrapped code), anti-loop (visited set + aresta de recursão + max_depth + orçamento de nós) e montagem do mermaid com legenda.
- Modo dinâmico DBMS_HPROF: scripts de setup/relatório entregues ao usuário, execução só com confirmação explícita, caminho real sobreposto ao grafo.
- Fixture FLOW_DEMO no banco dev (XE local, DDL autorizada pelo usuário nesta demanda): package com recursão mútua, EXECUTE IMMEDIATE resolvível e irresolúvel, DML em tabela com trigger e overload — base da validação end-to-end.

## Critérios de aceitação
- Queries de `sql/flow/` seguem as convenções do repo (binds documentados, ASCII, sem sintaxe 21c+) e cobrem os 12 arquivos do plano — prova: `pytest tests/test_plsql_flow.py -q -k queries`
- SKILL.md cobre as seções obrigatórias (camada PL/Scope, fallback léxico, SQL dinâmico, OO/override, triggers, cascata FK, anti-loop, overloads, mermaid com legenda, modo dinâmico) e todos os links para queries resolvem — prova: `pytest tests/test_plsql_flow.py -q -k skill_doc`
- Script da fixture FLOW_DEMO contém os casos exigidos (recursão mútua A/B, EXECUTE IMMEDIATE resolvível e irresolvível, trigger, overload) — prova: `pytest tests/test_plsql_flow.py -q -k fixture`
- Scripts do modo dinâmico DBMS_HPROF presentes e íntegros (setup + relatório, avisos de efeito colateral na SKILL.md) — prova: `pytest tests/test_plsql_flow.py -q -k hprof`
- Execução end-to-end contra FLOW_DEMO no dev gera diagrama com: ciclo de recursão detectado (sem loop infinito), trigger no grafo, nó de SQL dinâmico irresolúvel, overload resolvido — evidência salva em `.harness/scratch/plsql-flow-evidence.md` — prova: `pytest tests/test_plsql_flow.py -q -k e2e_evidence`

## Não-objetivos
- Análise de código fora do banco (arquivos .pkb locais não compilados) — a skill lê do dicionário.
- Recursão em objetos via DB link (viram folha externa).
- Poda automática de branches por valor de parâmetro (anotação heurística apenas).
- Scheduler jobs como continuação do grafo (folha anotada).

## Unknowns
- (nenhum — profile completo, decisões de escopo confirmadas pelo usuário)
