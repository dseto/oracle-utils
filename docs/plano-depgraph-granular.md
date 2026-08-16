# Plano — depgraph granular (processo end-to-end, grão SUBPROGRAMA)

Objetivo declarado pelo usuário: mapear um **PROCESSO** de ponta a ponta. A
procedure passada como parâmetro é só a ponta do iceberg — a primeira de uma
cadeia que pode atravessar dezenas de packages, procedures, functions, views,
tabelas e qualquer outro objeto do banco. O mapeamento desce **recursivamente
até o fundo**, em profundidade total, com qualidade suficiente para reescrever
o processo como microserviço em outra tecnologia. Critério de sucesso: **se o
grafo omitir ou resumir algo, a migração falha**.

Consequências diretas do objetivo, que este plano implementa:

1. **A raiz é um subprograma**: `owner.package.procedure` é alvo válido.
2. **A travessia é dirigida pelo processo, não pelo objeto**: só entra no
   grafo o que a cadeia de execução alcança. Um package com 80 procedures das
   quais o processo usa 3 contribui com 3 nós — mais o que essas 3 arrastam
   (estado do package, triggers, etc.). Isso não é resumo: é o recorte exato
   do processo. Os outros 77 subprogramas não pertencem ao processo.
3. **Profundidade total por default**: no modo granular não há cap de
   profundidade implícito. Caps continuam existindo como opção de segurança
   explícita, e qualquer truncamento é declarado — nunca silencioso.

Este documento é o desenho. O contrato executável (`.harness/work/...`) vem
depois da aprovação.

---

## 1. Por que nem o flow nem o depgraph atual atendem

**flow** tem nós de subprograma, mas as arestas são do pacote inteiro
replicadas em cada nó — limitação declarada na própria docstring
(`plsqlflow/graph.py:9-20`) e confirmada no código
([graph.py:259](plsqlflow/graph.py:259): `calls = self._get_calls(owner,
object_name)` — **todas** as chamadas do objeto para **qualquer** subprograma
expandido). No `FLOW_DEMO`, o flow desenha `MAIN` chamando `LENGTH` e
inserindo em `FLOW_DEMO_LOG`; na verdade quem chama `LENGTH` é
`CALC_OVERLOAD` e quem insere é `LOG_MSG`. Para migração, aresta falsa quebra
tanto quanto omissão: recorta a fronteira do serviço no lugar errado.

**depgraph** tem atribuição correta de trigger/sinônimo/fronteira, mas grão
objeto: `PACKAGE` inteiro é 1 nó. Mapeia o iceberg todo do *objeto*, não o
caminho do *processo* — e sem dizer qual subprograma faz o quê.

O alvo real é **atribuição exata em travessia de processo** — que o flow
declarou impossível sem parser de fonte. Não é impossível. Seção 2.

---

## 2. Base técnica — provada contra o banco `dev`

PL/Scope expõe uma **árvore de contexto**: cada linha de `ALL_IDENTIFIERS`
tem `usage_id` e `usage_context_id` (o `usage_id` do pai). Subindo a árvore
até o `DEFINITION` de `PROCEDURE`/`FUNCTION` mais próximo, obtém-se o
subprograma que envolve cada chamada e cada statement.

Verificado em `GESTAO.FLOW_DEMO` (Oracle XE 21c local, somente `SELECT`):

| usage_id | linha | tipo | alvo | subprograma envolvente (apurado) |
|---|---|---|---|---|
| 6 | 6 | STMT | INSERT | `LOG_MSG` |
| 13 | 11 | CALL | LOG_MSG | `PROC_A` |
| 16 | 13 | CALL | PROC_B | `PROC_A` |
| 22 | 20 | CALL | PROC_A | `PROC_B` |
| 30 | 28 | STMT | EXECUTE IMMEDIATE | `RUN_DYNAMIC` |
| 33 | 31 | STMT | EXECUTE IMMEDIATE | `RUN_DYNAMIC` |
| 44 | 41 | CALL | LENGTH | `CALC_OVERLOAD` |
| 53 | 47 | CALL | PROC_A | `MAIN` |
| 55 | 48 | CALL | RUN_DYNAMIC | `MAIN` |
| 58 | 49 | CALL | CALC_OVERLOAD | `MAIN` |
| 61 | 50 | CALL | CALC_OVERLOAD | `MAIN` |

11 arestas reais, todas atribuídas, nenhuma ambígua. O flow relatou **51
arestas** para o mesmo alvo — inflação por sobre-aproximação.

Quatro fatos adicionais confirmados na mesma sessão:

1. **`ALL_STATEMENTS` participa da mesma árvore.** Os `usage_id` ausentes de
   `ALL_IDENTIFIERS` (6, 30, 33) são exatamente os statements, e seus
   `usage_context_id` apontam para dentro da árvore de identificadores.
   Calls e SQL se atribuem pelo mesmo mecanismo.

2. **`signature` resolve o destino da chamada exatamente — inclusive
   sobrecarga e inclusive ENTRE objetos.** A CALL carrega a `signature` da
   DECLARATION que o *compilador* resolveu; o join por signature entrega
   `(decl_owner, decl_object, decl_subprogram)` do alvo real, em qualquer
   package. `CALC_OVERLOAD` tem duas DEFINITIONs (`15AD5598…` NUMBER,
   `BC715965…` VARCHAR2) e as duas CALLs de `MAIN` carregam a signature da
   que de fato resolvem. É isso que torna a travessia recursiva
   inter-package possível sem heurística: cada chamada atribuída já diz
   exatamente qual subprograma de qual objeto entra na fila.

3. **Coluna é atribuível.** `MSG COLUMN REFERENCE` tem contexto 6 — o
   INSERT — que está em `LOG_MSG`. Linhagem coluna a coluna por subprograma,
   vinda do compilador, no lugar do regex best-effort atual
   (`depgraph_enrich._extract_write_cols`).

4. **Estado de package é detectável pelo mesmo caminho.** Variável cujo walk
   termina no `PACKAGE` (e não num subprograma) é estado compartilhado; as
   `ASSIGNMENT`/`REFERENCE` dizem quem escreve e quem lê. Para migração é
   decisivo: package com estado não se recorta em serviços independentes sem
   tratar a sessão.

---

## 3. Defeito encontrado de passagem (omissão silenciosa)

`_DepGraphEngine.has_plscope`
([depgraph.py:439-447](plsqlflow/depgraph.py:439)) marca `plscope=True` se as
settings contiverem `IDENTIFIERS:ALL` — **sem checar `STATEMENTS:ALL`**. Um
objeto compilado só com `IDENTIFIERS:ALL` é hoje tratado como coberto, mas
`ALL_STATEMENTS` vem vazio: todo acesso a tabela e SQL dinâmico dele somem do
grafo, **sem entrar em PONTOS CEGOS nem em `needs_recompile`**. É exatamente o
modo de falha que o usuário descreveu. Correção obrigatória (T-06), com as
duas capacidades reportadas separadamente.

---

## 4. Arquitetura — travessia de processo com fallback declarado

### Motor novo: BFS em grão SUBPROGRAMA

A fila da travessia carrega **subprogramas**, não objetos:

1. **Semear**: raiz `owner.package.procedure` entra na fila. (Raiz
   `owner.objeto` sem subprograma também é aceita: semeia todos os
   subprogramas públicos da spec — útil para mapear a API inteira de um
   package.)
2. **Expandir um subprograma**: na primeira visita a um OBJETO, puxa de uma
   vez a árvore PL/Scope inteira dele (identifiers + statements, em lote) e
   monta a atribuição (seção 2) — cacheado; visitas a outros subprogramas do
   mesmo objeto não repetem a leitura. Do subprograma corrente saem:
   - **CALL** → alvo resolvido por `signature` → `(owner, objeto,
     subprograma)` exato entra na fila. Recursão direta/mútua fecha ciclo no
     visited-set (e vira SCC, T-07).
   - **READ/WRITE** → tabela/view alvo com colunas do compilador. Tabela
     escrita → triggers dela (reuso da fase 4 atual) → o corpo do trigger
     entra na fila **como subprograma** (trigger é objeto PL/Scope como
     qualquer outro).
   - **View** → expande via `ALL_DEPENDENCIES` até as tabelas base (grão
     objeto — view não tem subprograma).
   - **SQL dinâmico** → classificação `resolved`/`partial`/`opaque` por
     subprograma; alvo resolvido entra na fila/grafo.
   - **Sinônimo/db_link/fronteira** → reuso da lógica atual do
     `_DepGraphEngine` (resolve cadeia, folha opaca, `stop_schemas`).
   - **Estado de package** → arestas STATE_READ/STATE_WRITE para o nó
     sintético de estado do package (T-05).
3. **Repetir até a fila esvaziar** — profundidade total. Sem cap default no
   modo granular; `--max-depth`/`--max-objects` viram opt-in de segurança e,
   se baterem, o truncamento é declarado por item em `not_expanded`.

### Fallback de completude: objeto sem PL/Scope

Quando a cadeia alcança um objeto PL/SQL **sem** PL/Scope utilizável
(`IDENTIFIERS`+`STATEMENTS`) ou **wrapped**, a atribuição por subprograma é
impossível — mas a travessia **não para nem omite**: o objeto inteiro é
expandido no grão OBJETO via `ALL_DEPENDENCIES` (motor atual,
`_DepGraphEngine`, reusado), e tudo que ele alcança continua a travessia.
Sobre-aproximação deliberada e **marcada** (`grain: object`, motivo por nó):
o mapa nunca perde um ramo do processo; no pior caso um trecho fica mais
grosso que o ideal, e a seção COBERTURA diz exatamente onde e por quê. Objeto
recompilável entra em `recompile.sql` — recompilar com PL/Scope e regerar
refina o trecho.

É a regra do plano inteiro: **grão fino onde o compilador prova, grão objeto
declarado onde não prova, omissão nunca.**

### Proteção contra recursão infinita (referências circulares)

Ciclo é **esperado** num processo real — recursão direta, mútua entre
packages, trigger que escreve na tabela de outro trigger, sinônimo apontando
para sinônimo. A travessia termina **sempre**, por construção:

- **Invariante única**: um nó (subprograma, objeto em fallback, tabela,
  view) entra na fila no máximo UMA vez na vida da travessia — visited-set
  global chaveado pela identidade do nó (seção "Identidade de nó"), marcado
  no **enqueue**, não no processamento. Encontro posterior com nó já visto
  gera só a aresta (o ciclo aparece no grafo) e não re-enfileira nada. Como
  cada expansão consome um item da fila e itens novos só nascem de nós
  nunca vistos, a fila esvazia em no máximo |nós alcançáveis| passos —
  travessia termina mesmo com "profundidade total" e sem cap. Mesmo padrão
  já provado nos dois motores existentes (`graph.py` caminho/visited,
  `depgraph.py` visited-set), agora unificado num set só.
- **Ciclos indiretos cobertos pela mesma invariante**, sem caso especial:
  - CALL circular (A→B→A, direta ou atravessando N packages);
  - trigger: tabela T1 → trigger TRG1 → escreve T2 → trigger TRG2 →
    escreve T1 — T1 já visitada, aresta fecha o ciclo, fila não cresce;
  - sinônimo circular: `resolve_synonym_chain` (reusado) já detecta laço na
    cadeia e corta com marcador;
  - fallback grão objeto: o `_DepGraphEngine` reusado compartilha o mesmo
    visited — objeto que a travessia fina já viu não é re-expandido pelo
    fallback, e vice-versa;
  - `__INIT__`/`__STATE__`: nós sintéticos têm identidade própria e entram
    no mesmo set.
- **Ciclo não é só tolerado — é reportado**: T-07 roda SCC (Tarjan) sobre o
  grafo final e todo ciclo vira grupo nomeado no INDEX (`## Ciclos`), porque
  para migração um ciclo inter-package é acoplamento que precisa aparecer,
  não sumir num marcador de aresta.
- **Prova de terminação como teste**: fixture com ciclo em cada camada
  (CALL mútua entre 2 packages, ciclo de trigger entre 2 tabelas, sinônimo
  circular) + timeout curto no teste — travessia tem que terminar e o grafo
  tem que conter os 3 ciclos declarados. Entra na prova do T-03 (CALL) e do
  T-05 (trigger); sinônimo já tem teste em `resolve.py`.

### Reuso

`_DepGraphEngine` (fronteira, sinônimo, db_link, triggers, caps, catálogo,
lote por owner/chunk) vira o executor dos passos de grão objeto do novo
motor. `depgraph_enrich`/`dynsql` são reusados por subprograma.
`plsqlflow/graph.py` **não é tocado** — congelado por golden test do contrato
`plsqlflow-py`; o modo `flow` continua com o comportamento atual.

### Módulos

| Arquivo | Papel |
|---|---|
| `plsqlflow/attribute.py` (novo) | árvore de contexto + subprograma envolvente + resolução de CALL por signature. Puro, sem banco. |
| `plsqlflow/procgraph.py` (novo) | motor da BFS de subprogramas (seção acima), orquestrando `attribute` + `_DepGraphEngine` para os passos de grão objeto. |
| `plsqlflow/procgraph_render.py` (novo) | saída em disco (formato da seção 6). |
| `sql/flow/plscope_tree_batch.sql` (novo) | árvore crua de identificadores, N objetos por chamada. |
| `sql/flow/plscope_statements_batch.sql` (editado) | acrescenta `usage_id`/`usage_context_id`. |
| `plsqlflow/depgraph.py` (editado, cirúrgico) | só o `has_plscope` da seção 3. |

**Caminhada em Python, não `CONNECT BY`**: walker puro é testável com fixture
sem banco (padrão de toda a suíte); o lote por owner já existe
(`*_batch.sql`); um `CONNECT BY` por objeto não escala em schema de 10-50 mil
objetos.

### Identidade de nó

- subprograma: `OWNER.OBJETO.SUBPROGRAMA`
- aninhado: `OWNER.OBJETO.EXTERNA.INTERNA` (caminho completo — nó próprio,
  nunca fundido no pai)
- sobrecarga: sufixo `#<n>` (posição da signature ordenada por linha de
  declaração, determinístico); a `signature` viaja como campo do nó e é ela
  que resolve a aresta
- sintéticos: `OWNER.OBJETO.__INIT__` (bloco de inicialização — **executa na
  primeira chamada ao package, então entra na fila junto com o primeiro
  subprograma alcançado do package**), `OWNER.OBJETO.__SPEC__` (declarações
  de nível de package), `OWNER.OBJETO.__STATE__` (âncora das arestas de
  estado)
- objeto em fallback: `OWNER.OBJETO` com `grain: object` e motivo

---

## 5. Tarefas

| id | entrega | prova |
|---|---|---|
| T-01 | `attribute.py`: árvore de contexto + walk envolvente + resolução de CALL por signature (inter-objeto), aninhados, `__INIT__`/`__SPEC__` | unitário com as 11 atribuições da seção 2 |
| T-02 | queries: `plscope_tree_batch.sql` novo; `plscope_statements_batch.sql` ganha `usage_id`/`usage_context_id`; `extract.py` tipa | teste live contra `GESTAO.FLOW_DEMO` |
| T-03 | `procgraph.py`: BFS de subprogramas — semeadura por raiz `owner.objeto[.subprograma]`, expansão recursiva inter-package, visited-set global (invariante de terminação, seção 4), `__INIT__` na primeira visita do package | golden `FLOW_DEMO.MAIN`: cadeia exata, zero aresta falsa; fixture com CALL mútua entre 2 packages termina (timeout no teste) com ciclo no grafo |
| T-04 | fallback grão objeto: objeto sem PL/Scope/wrapped expande via `_DepGraphEngine` e a travessia continua através dele; nós marcados `grain: object` + motivo | fixture com cadeia A(fino)→B(sem plscope)→C(fino): C tem que aparecer |
| T-05 | READ/WRITE por subprograma com colunas do compilador (substitui regex); estado de package (STATE_READ/STATE_WRITE vs `__STATE__`); triggers de tabela escrita entram como subprograma | comparação contra regex no fixture; fixture com estado; ciclo de trigger entre 2 tabelas termina com ciclo declarado |
| T-06 | correção `has_plscope` (seção 3): `IDENTIFIERS`/`STATEMENTS` reportados separados; sem STATEMENTS → `needs_recompile` + PONTOS CEGOS | regressão: objeto só com IDENTIFIERS não sai "coberto" |
| T-07 | SCC (Tarjan) sobre o grafo de subprogramas — todo ciclo (recursão direta/mútua, ciclo de trigger, acoplamento inter-package) vira grupo nomeado na seção `## Ciclos` do INDEX | fixture `PROC_A`/`PROC_B` (recursão mútua existente) + ciclo inter-package do T-09 |
| T-08 | COBERTURA + render/saída (seção 6) + CLI (`owner.objeto[.subprograma]`, profundidade total default, caps opt-in) | reconciliação de contadores como teste |
| T-09 | fixture DDL: 2+ packages encadeados (processo cruzando package), CALL mútua ENTRE os packages (ciclo inter-package), aninhado, estado, `__INIT__`, objeto sem PL/Scope no meio da cadeia, view sobre tabela, ciclo de trigger entre 2 tabelas | **DDL — exige confirmação explícita do usuário** |

Ordem: T-01 e T-02 fundação (paralelizáveis). T-03 depende das duas. T-04,
T-05, T-07 dependem de T-03, paralelizáveis entre si. T-06 independente, pode
sair primeiro. T-08 fecha. T-09 é pré-requisito de evidência real para
T-03/T-04/T-05 — aprovação à parte por ser DDL.

### CLI

```
python -m plsqlflow depgraph GESTAO.PKG_PEDIDO.PROCESSA --granular
python -m plsqlflow depgraph GESTAO.PKG_PEDIDO --granular   # API inteira do package
```

Alvo com 3 partes implica raiz-subprograma; com 2 partes semeia a spec
inteira. Sem `--granular`, comportamento atual byte a byte (grão objeto,
alvo 2 partes) — nenhum contrato existente muda.

---

## 6. Completude — requisito duro, tratado como obrigação de prova

A saída não declara "grafo completo": declara **o que cobriu em que grão e o
que não cobriu, com contagem que fecha**.

`INDEX.md`, seção `## COBERTURA`:

```
objetos alcançados = grão subprograma + grão objeto (por motivo) + folhas de fronteira
calls por objeto   = atribuídos a subprograma + atribuídos a __INIT__/__SPEC__ + não atribuídos
statements idem
```

Soma que não fecha é **erro, não aviso** — o grafo não é gravado como válido.
Número que não bate é a assinatura da omissão silenciosa.

Motivos de rebaixamento para grão objeto ou folha, todos declarados por nó:

- sem PL/Scope (`IDENTIFIERS` e/ou `STATEMENTS`) → fallback + `recompile.sql`
- objeto *wrapped* → fallback; irrecuperável estaticamente
- fronteira de schema (`SYS`/`SYSTEM`, `DBMS_`/`UTL_`, `--stop-schemas`) → folha deliberada
- alvo remoto via db_link → folha opaca
- truncamento por cap opt-in → declarado por item em `not_expanded`

### Pontos cegos irredutíveis (enumerados, nunca fingidos)

- SQL dinâmico não literal (`partial`/`opaque` por subprograma)
- chamada indireta / `DBMS_SQL` montado em runtime
- `OVERRIDING` de tipo: dispatch é runtime; candidatos listados como candidatos
- autonomous transaction; cascata de trigger além do nível coberto
- job/scheduler que invoca a cadeia por fora

---

## 7. Escala

Alvo: "processos gigantes, centenas de subchamadas, dezenas de packages".

- árvore PL/Scope puxada **uma vez por objeto**, na primeira visita, em lote
  por owner/chunk (mesmo `chunk_names`) — nunca o schema inteiro, nunca
  repetida por subprograma
- travessia visita só o que o processo alcança — em package grande isso é
  **menos** trabalho que o fechamento de objeto atual, não mais
- atribuição é passada linear sobre as linhas da árvore
- profundidade total default; `--max-objects`/`--max-depth` opt-in de
  segurança com truncamento declarado
- `--index-split` particiona INDEX por owner, valendo para nós de subprograma
- mermaid: não por padrão (centenas de nós = ilegível); atrás de flag, por
  partição

---

## 8. Não-objetivos

- não alterar `plsqlflow/graph.py` nem o modo `flow` (congelados por golden
  test)
- não melhorar a resolução de SQL dinâmico além dos três níveis atuais
- nenhum dado de runtime (profiling, AWR, contagem de execução)
- nenhuma geração de código da migração — o entregável é o mapa, não o port

---

## 9. Verificação

- golden `FLOW_DEMO.MAIN`: exatamente a cadeia da seção 2 alcançável de
  `MAIN`, nenhuma aresta a mais (ausência de aresta falsa é critério)
- T-04: cadeia com objeto sem PL/Scope no meio — o que está depois dele TEM
  que aparecer no grafo (anti-omissão do fallback)
- reconciliação de contadores da seção 6 como teste, não relatório
- regressão T-06: objeto só com `IDENTIFIERS:ALL` sai não-coberto
- recursão mútua `PROC_A`/`PROC_B` como SCC
- terminação sob ciclo: fixtures com CALL mútua inter-package, ciclo de
  trigger e sinônimo circular terminam (timeout no teste) e os ciclos
  aparecem declarados em `## Ciclos` — nunca loop infinito, nunca ciclo
  omitido
- sem `--granular`, saída idêntica byte a byte à atual
- compatibilidade 19c: `usage_id`/`usage_context_id`/`signature` existem
  desde 11g em `ALL_IDENTIFIERS` e desde 12.2 em `ALL_STATEMENTS` — dentro da
  baseline. Validar contra a conexão 19c real antes de fechar.
