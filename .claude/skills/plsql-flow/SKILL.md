---
name: plsql-flow
description: Gera diagrama mermaid do caminho completo de execução de uma procedure/function Oracle 19c — chamadas recursivas até as folhas, triggers disparados, SQL dinâmico, dispatch de object types — com proteção anti-loop e modo dinâmico opcional (DBMS_HPROF). Usar quando o usuário pedir "o que essa procedure executa", "mapa/fluxo de execução", "call graph", "por onde esse código passa", ou fornecer procedure+parâmetros pedindo o caminho.
---

# /plsql-flow — mapa de execução de procedure/function

## Entrada
- Alvo: `[owner.]package.subprograma` ou `[owner.]procedure_standalone`.
- Parâmetros (opcionais): resolvem overload via [resolve_target.sql](../../../sql/flow/resolve_target.sql) (ALL_ARGUMENTS), anotam o nó raiz e habilitam o modo dinâmico.
- Opções: `max_depth` (default 10), `incluir_sys` (default: DBMS_*/UTL_* viram folhas), modo `estatico` (default) ou `dinamico`.

## Fluxo

### 0. Resolver o alvo
[resolve_target.sql](../../../sql/flow/resolve_target.sql): confirma existência, lista overloads. Com parâmetros: casar aridade/tipos (ALL_ARGUMENTS); ambíguo = incluir todas as assinaturas com sufixo `#n` e nota. Nome pode ser sinônimo: resolver antes com [resolve_synonym.sql](../../../sql/flow/resolve_synonym.sql) (inclui PUBLIC; cadeia = iterar; `db_link` preenchido = folha externa).

### 1. Camada A — PL/Scope (preferida)
- [plscope_check.sql](../../../sql/flow/plscope_check.sql): objeto compilado com `IDENTIFIERS:ALL`?
- Sim → [plscope_calls.sql](../../../sql/flow/plscope_calls.sql): chamadas resolvidas pelo compilador (linha, subprograma declarado — overloads e escopo já corretos) e [plscope_statements.sql](../../../sql/flow/plscope_statements.sql): SQL embutido com tipo/texto (requer `STATEMENTS:ALL`).
- Não → oferecer script de recompilação (`ALTER <obj> COMPILE PLSCOPE_SETTINGS='IDENTIFIERS:ALL, STATEMENTS:ALL'`) — **entregar, nunca executar** (invalida cursores e recompila dependentes); sem autorização, Camada B.

### 2. Camada B — léxica sobre ALL_SOURCE (fallback)
- [fetch_source.sql](../../../sql/flow/fetch_source.sql): spec+body. Primeira linha do body com `wrapped` = código ofuscado → nó folha "wrapped", só dependencies ajudam.
- Normalizar: remover comentários e literais (guardar literais para SQL dinâmico).
- Candidatos: identificador seguido de `(` ou `;` que não é keyword/variável local declarada.
- Resolução por escopo: (a) subprograma do mesmo package; (b) objeto do schema (ALL_PROCEDURES); (c) sinônimo; (d) SYS conhecido.
- Espaço de busca limitado por [deps_direct.sql](../../../sql/flow/deps_direct.sql) — reduz falso positivo.

### 3. Triggers e cascata
Para cada DML sobre tabela/view T:
- [triggers_for_tables.sql](../../../sql/flow/triggers_for_tables.sql): triggers ENABLED com evento compatível (INSERT/UPDATE/DELETE, UPDATE OF, INSTEAD OF em view) → aresta pontilhada rotulada evento/timing → recursar no corpo (é PL/SQL).
- DELETE no pai: [fk_cascade.sql](../../../sql/flow/fk_cascade.sql) — `ON DELETE CASCADE`/`SET NULL` executa nos filhos e dispara triggers deles.

### 4. SQL dinâmico
Detectar `EXECUTE IMMEDIATE`, `DBMS_SQL.PARSE`, `OPEN <cur> FOR '<string>'`:
- Constant folding: concatenação só de literais + constantes de package (ler spec) → reconstituir e tratar como estático.
- Irresolúvel (variável/parâmetro na string): nó losango tracejado `{{SQL dinamico?}}` com fragmento visível; heurística: nomes de objetos conhecidos dentro dos literais → aresta tracejada "provável".
- Todos os irresolúveis entram na tabela de pontos cegos.

### 5. Orientação a objeto
Chamada a método de object type → [type_hierarchy.sql](../../../sql/flow/type_hierarchy.sql): subtipos com `OVERRIDING` do método são candidatos em runtime (dispatch dinâmico não é resolvível estaticamente) → TODOS entram como arestas tracejadas "override?" num subgraph do supertipo. Constructor/MAP/ORDER/STATIC = subprogramas normais.

### 6. Casos restantes
- **AUTHID CURRENT_USER**: anotar no nó — grafo assume resolução pelo owner.
- **Funções dentro de SQL**: extrair identificadores dos statements e resolver como chamada.
- **Init de package**: primeira entrada num package com bloco de inicialização → nó `pkg (init)`.
- **DBMS_SCHEDULER**: folha anotada "assíncrono", não recursar.

## Anti-loop e limites (obrigatório)
- **visited set** por `(owner, objeto, subprograma, assinatura)` — nunca expandir duas vezes.
- Aresta para nó no caminho atual = **recursao** (aresta vermelha rotulada), sem expandir — cobre recursão direta e mútua.
- `max_depth` atingido → folha `... (+N niveis?)`.
- Orçamento ~120 nós; estouro → colapsar folhas-tabela por chamador → colapsar SYS → dividir por subárvore (mapa geral de packages + detalhe por package).
- Máx. ~50 queries de dicionário por execução; acima, pedir escopo menor.

## Saída
1. **Diagrama mermaid** (`flowchart TD`), com **legenda** embutida:
   - Nós: retângulo = proc/func; cilindro = tabela/view; hexágono = remoto/DB link; losango tracejado = SQL dinâmico irresolúvel; trapézio = trigger; subgraph por package/supertipo.
   - Arestas: sólida = estática; tracejada = dinâmica/provável/override; pontilhada rotulada = trigger/cascata; vermelha = recursao.
   - Nó raiz anotado com os parâmetros.
2. **Pontos cegos**: SQL dinâmico irresolúvel, override aberto, DB link, AUTHID — com `owner.objeto:linha`.
3. **Estatísticas**: nós, arestas, profundidade, % resolvido por PL/Scope vs léxico.
4. Grafo grande → Artifact HTML (mermaid nativo, um bloco por subárvore).

## Modo dinâmico (DBMS_HPROF) — opcional
Caminho REAL para os parâmetros dados. **Executa o código de verdade — efeitos colaterais reais**: DML do código acontece, autonomous transactions não sofrem rollback, DDL quebra a transação. **Só com confirmação explícita do usuário.**
1. [hprof_setup.sql](../../../sql/flow/hprof_setup.sql) — script de setup (grant + tabelas de análise) **entregue ao usuário**, nunca executado pela skill.
2. Wrapper de execução com START_PROFILING/STOP_PROFILING + chamada com os parâmetros — entregue.
3. [hprof_report.sql](../../../sql/flow/hprof_report.sql) — consulta as tabelas DBMSHP_* com o runid (modo tabela: STOP_PROFILING já grava; sem ANALYZE).
4. Sobrepor ao grafo estático: caminho executado em verde com contagens/tempo; nó executado que a análise estática não previu = SQL dinâmico capturado (listar como feedback).

## Regras
- Análise estática = somente SELECT em views de dicionário. Recompilação PL/Scope, setup HPROF e execução do alvo = scripts entregues, só rodam com confirmação explícita.
- Compatibilidade 19c: ALL_STATEMENTS existe em 19c; nenhuma feature 21c+.
- Privilégio insuficiente (ORA-00942): avisar limitação e seguir com o que ALL_* enxerga.
