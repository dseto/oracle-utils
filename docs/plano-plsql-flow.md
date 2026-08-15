# Plano: skill /plsql-flow — mapa de execução de procedure/function

## Objetivo

Dada uma procedure/function (+ parâmetros), gerar diagrama mermaid do caminho completo de execução e dependências, recursivo até as folhas, sem loop infinito, cobrindo casos complexos: SQL dinâmico, orientação a objeto, triggers, overloads, sinônimos, DB links.

## Entrada

- Alvo: `[owner.]package.subprograma` ou `[owner.]procedure_standalone`.
- Parâmetros (opcionais): usados para (a) resolver overload, (b) anotar o nó raiz, (c) modo dinâmico opcional, (d) poda heurística de branches (best effort).
- Opções: `max_depth` (default 10), `incluir_sys` (default: colapsar DBMS_*/UTL_* como folhas), modo `estatico` (default) ou `dinamico` (requer confirmação — executa o código).

## Estratégia em duas camadas

### Camada A — PL/Scope (precisa, preferida)

`ALL_IDENTIFIERS` (usage='CALL') dá chamadas resolvidas pelo compilador com granularidade de subprograma e linha; `ALL_STATEMENTS` (19c+) dá statements SQL embutidos com tipo e texto. Resolve sozinho: overloads, escopo de package, sinônimos.

- Pré-check: `ALL_PLSQL_OBJECT_SETTINGS.plscope_settings` do alvo e da cadeia. Objetos compilados sem `IDENTIFIERS:ALL` não têm dados.
- Se ausente: entregar script `ALTER ... COMPILE PLSCOPE_SETTINGS='IDENTIFIERS:ALL, STATEMENTS:ALL'` para o usuário rodar (recompilação = DDL, nunca executar sem confirmação; avisar impacto: invalida cursores, recompila dependentes).
- Sem recompilação autorizada → Camada B.

### Camada B — análise léxica de ALL_SOURCE (fallback universal)

1. Baixar fonte do alvo via `ALL_SOURCE` (spec + body).
2. Normalizar: remover comentários (`--`, `/* */`) e literais string (guardando-os para a fase de SQL dinâmico), case-fold.
3. Candidatos de chamada: identificadores seguidos de `(` ou `;` que não são keywords/variáveis locais declaradas.
4. Resolução por ordem de escopo do PL/SQL:
   a. subprograma do mesmo package (spec+body parseados);
   b. objeto do mesmo schema (`ALL_PROCEDURES`);
   c. sinônimo (`ALL_SYNONYMS` → resolver base, inclusive PUBLIC);
   d. package SYS conhecido (DBMS_*, UTL_* — folha por default).
5. Espaço de busca limitado por `ALL_DEPENDENCIES` do objeto (o compilador já registrou os objetos referenciados — reduz falso positivo).

Camadas podem se misturar: PL/Scope onde disponível, léxico onde não.

## Casos complexos (requisito central)

### 1. SQL dinâmico
- Detectar: `EXECUTE IMMEDIATE`, `DBMS_SQL.PARSE`, `OPEN <cur> FOR '<string>'`.
- Constant folding best-effort: concatenação de literais + constantes de package (ler valores das constantes na spec) → se o texto final for reconstituível, parsear como SQL estático e seguir.
- Irresolúvel (string montada com variável/parâmetro): nó especial `{{SQL dinamico?}}` (losango tracejado) com o fragmento visível da string; listar na tabela de "pontos cegos" do relatório. Heurística adicional: procurar nomes de objetos conhecidos do schema dentro dos fragmentos literais e ligar com aresta tracejada rotulada "provável".

### 2. Orientação a objeto (object types)
- Chamada a método: `ALL_TYPE_METHODS` do tipo declarado.
- **Dispatch dinâmico**: se o tipo tem subtipos (`ALL_TYPES.supertype_name`) que fazem OVERRIDING do método, o alvo real só existe em runtime → incluir TODOS os candidatos como arestas tracejadas rotuladas "override?", agrupados num subgraph do supertipo.
- Constructor, MAP/ORDER, STATIC vs MEMBER tratados como subprogramas normais do tipo.

### 3. Triggers
- Para cada DML estático (e dinâmico resolvido) sobre tabela/view T:
  - `ALL_TRIGGERS` de T filtrando evento compatível (INSERT/UPDATE/DELETE, inclusive UPDATE OF coluna) e `status='ENABLED'`;
  - aresta pontilhada `DML -.-> trigger` rotulada com evento/timing (BEFORE ROW etc.);
  - recursar no corpo do trigger (é PL/SQL — volta pra camada A/B).
- Views: INSTEAD OF triggers.
- **Cascata de FK**: DELETE em pai com `ON DELETE CASCADE` dispara triggers dos filhos → seguir `ALL_CONSTRAINTS` (delete_rule) e incluir.

### 4. Overload
- `ALL_ARGUMENTS` por (owner, package, subprograma): comparar aridade e tipos com os parâmetros fornecidos; escolher assinatura compatível. Ambíguo ou sem parâmetros → incluir todas com sufixo `#n` e nota.

### 5. Outros
- **Sinônimos**: sempre resolver antes de criar nó (nó = objeto base; sinônimo vira rótulo).
- **DB links**: `nome@link` = nó folha externo (hexágono), nunca recursar.
- **AUTHID CURRENT_USER**: anotar no nó — resolução de nomes depende do invocador; avisar que o grafo assume o owner.
- **Funções dentro de SQL** (`SELECT fn(...)`): extrair identificadores de expressões nos statements SQL e resolver como chamada.
- **Bloco de inicialização de package**: primeira chamada a qualquer subprograma do package executa o init → nó `pkg (init)` ligado na primeira aresta que entra no package.
- **Scheduler**: `DBMS_SCHEDULER.CREATE_JOB`/`RUN_JOB` com job conhecido = folha anotada "assíncrono" (não recursar por default).

## Anti-loop e limites (requisito central)

- **Visited set** com chave `(owner, object, subprograma, assinatura)` — nunca expandir duas vezes.
- **Recursão direta/indireta**: ao encontrar aresta para nó já no caminho atual (stack), criar aresta de retorno estilizada (vermelha, rotulada "recursao") e NÃO expandir.
- `max_depth` (default 10): nós no limite viram folhas com `⋯ (+N níveis?)`.
- **Orçamento de nós** (default 120): estourou → estratégia de colapso em ordem: (1) agrupar folhas-tabela por package chamador, (2) colapsar packages SYS, (3) dividir em múltiplos diagramas por subárvore (um "mapa geral" só de packages + um diagrama detalhado por package).
- Timeout de coleta: máx. ~50 queries ao dicionário por execução; acima disso, avisar e pedir escopo menor.

## Saída

1. **Diagrama mermaid** (flowchart TD):
   - Formas: retângulo = proc/func; retângulo duplo = método de tipo; cilindro = tabela/view; hexágono = objeto remoto/externo; losango tracejado = SQL dinâmico irresolúvel; trapézio = trigger.
   - Arestas: sólida = chamada estática; tracejada = dinâmica/provável/override; pontilhada rotulada = trigger/cascata; vermelha = ciclo.
   - Subgraphs por package (e por supertipo no caso OO). classDef por categoria, cores tema-neutras.
   - Nó raiz anotado com os parâmetros fornecidos.
   - Legenda embutida.
2. **Tabela de pontos cegos**: cada SQL dinâmico irresolúvel, dispatch OO aberto, DB link, AUTHID — com owner.objeto:linha.
3. **Estatísticas**: nós, arestas, profundidade máx., % de chamadas resolvidas por PL/Scope vs léxico.
4. Diagrama grande → oferecer Artifact HTML (mermaid nativo, um bloco por subárvore).

## Modo dinâmico (opcional, fase 2 da skill)

Caminho REAL para os parâmetros dados via `DBMS_HPROF` (hierarchical profiler):
- Requer: grant EXECUTE em DBMS_HPROF + tabelas de análise (script de setup entregue).
- **Executa o código de verdade** → só com confirmação explícita; avisar sobre efeitos colaterais (DML do próprio código, autonomous transactions não sofrem rollback, DDL quebra a transação).
- Wrapper entregue: start_profiling → chamada com os parâmetros → stop → `DBMS_HPROF.ANALYZE` → query nas tabelas `DBMSHP_*`.
- Resultado sobreposto ao grafo estático: caminho executado em verde, contagens/tempo por nó. Divergências (nó executado que a análise estática não previu = SQL dinâmico capturado) viram feedback da qualidade estática.

## Estrutura de arquivos

```
.claude/skills/plsql-flow/SKILL.md      fluxo acima como instrução
sql/flow/resolve_target.sql             all_procedures + all_arguments (overloads)
sql/flow/plscope_check.sql              all_plsql_object_settings da cadeia
sql/flow/plscope_calls.sql              all_identifiers CALL + hierarquia usage_context_id
sql/flow/plscope_statements.sql         all_statements (SQL embutido, tipo+texto)
sql/flow/fetch_source.sql               all_source de um objeto
sql/flow/deps_direct.sql                all_dependencies diretas (espaco de busca)
sql/flow/triggers_for_tables.sql        all_triggers por lista de tabelas + evento
sql/flow/fk_cascade.sql                 all_constraints delete_rule=CASCADE
sql/flow/type_hierarchy.sql             all_types supertype + all_type_methods (overriding)
sql/flow/resolve_synonym.sql            all_synonyms (inclui PUBLIC) recursivo
sql/flow/hprof_setup.sql                script entregue (nao executado): setup DBMS_HPROF
sql/flow/hprof_report.sql               query nas DBMSHP_* pos-analise
```

## Ordem de implementação

1. Queries `sql/flow/` + SKILL.md com fluxo estático (Camada B léxica primeiro — funciona sempre; PL/Scope como upgrade).
2. Casos complexos: triggers → SQL dinâmico → OO → cascata FK (nessa ordem de frequência real).
3. Colapso/split de diagramas grandes + Artifact.
4. Modo dinâmico DBMS_HPROF.

## Verificação (no dev local)

Schema GESTAO não tem PL/SQL → criar fixture de teste (com confirmação): package `FLOW_DEMO` com: 2 níveis de chamada, uma recursão mútua A→B→A, um EXECUTE IMMEDIATE resolvível e um irresolúvel, DML em tabela com trigger, um overload. Rodar a skill e conferir: ciclo detectado (não trava), trigger aparece, dinâmico irresolúvel vira losango, overload resolvido pelos parâmetros.

## Riscos conhecidos

- Parser léxico ≠ compilador: falsos positivos em identificadores homônimos de variáveis — mitigado por declarações locais parseadas e espaço de busca via all_dependencies.
- PL/Scope exige recompilação (mudança de estado) — sempre via script entregue.
- Fontes gigantes (>10k linhas por objeto): ler em blocos, avisar custo.
- Wrapped code (`ALL_SOURCE` ofuscado): detectar prefixo `wrapped` → nó folha "codigo wrapped", só PL/Scope ou dependencies ajudam.
