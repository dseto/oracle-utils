# Backlog — fechar o buraco do SQL dinâmico no mapa de processo

Documento de backlog. **Não é contrato** — é o material para planejar um,
depois que `depgraph-granular` estiver mesclado.

## 1. O problema, na formulação certa

O contrato `depgraph-granular` trata SQL dinâmico não resolvido como ponto cego
declarado: a ocorrência aparece com linha, subprograma dono e trecho de fonte,
mas com alvo `?`. Isso foi desenhado pensando em **acesso a dado** — "não sei
qual tabela este SELECT lê".

A formulação está incompleta, e a diferença é de severidade, não de detalhe:

```sql
EXECUTE IMMEDIATE 'BEGIN pkg_x.proc_critica(:1); END;' USING v_id;
```

Isso não é um alvo desconhecido. É uma **subárvore inteira ausente**. Some do
mapa tudo que `proc_critica` faz: as tabelas que escreve, os triggers que
dispara, o que ela chama por sua vez, e a cadeia inteira abaixo. E some nos
**dois grãos**: `ALL_DEPENDENCIES` também não registra, porque não existe
dependência estática — o objeto pode nem entrar no fechamento.

Consequência para quem migra: não é "um campo pode sair errado". É "um ramo
inteiro do processo não existe no mapa, e nada indica que ele deveria existir".

### Evidência real (não hipótese)

Levantado ao vivo contra o schema `GESTAO_OO` (Oracle 21c, ver seção 5):

- 3 ocorrências de SQL dinâmico no fechamento: `PKG_DYNAMIC_EVALUATOR` L54 e
  L73 (`EXECUTE IMMEDIATE`, ambas `opaque`) e L29 (`OPEN v_cursor FOR v_sql`,
  com `v_sql` concatenando parâmetros de entrada).
- O fechamento estático alcançou 27 objetos e **`PKG_GESTAO_LEGADO` não está
  entre eles** — nenhuma referência estática a ele em todo o processo.
- Se qualquer um dos 3 pontos dinâmicos invoca esse package, ele está fora do
  mapa inteiro, e hoje **nada no relatório sugere isso**.

O nome do package (`LEGADO`) e a existência de uma view com INSTEAD OF trigger
(`VW_PROJETOS_LEGADOS` / `TRG_IO_VW_PROJETOS_LEGADOS`) tornam a hipótese
plausível o bastante para não ser ignorada — mas isso é justamente o ponto:
**hoje é hipótese, e deveria ser fato ou não-fato.**

## 2. Item A — severidade e procedência nos pontos cegos

Barato, usa dado que o pipeline já busca. Não resolve o alvo; muda o que o
humano faz com a informação.

### A.1 Classificar a FORMA do fragmento

Hoje todo `opaque` é igual. Deveria distinguir, pelo texto já capturado:

| forma | sinal no fragmento | severidade |
|---|---|---|
| **invoca código** | `BEGIN`, `END`, `CALL`, `EXECUTE` | **alta** — pode esconder subárvore |
| **manipula dado** | `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE` | média — esconde acesso, não fluxo |
| **DDL** | `CREATE`, `ALTER`, `DROP`, `TRUNCATE` | alta — efeito estrutural |
| **indeterminado** | nada reconhecível | **alta** (regra de desempate) |

A detecção é sobre o fragmento, então funciona mesmo quando o nome do objeto é
montado em runtime — a forma do comando costuma ser literal ainda que o alvo
não seja.

**Regra de desempate, mesma assimetria já adotada no contrato:** na dúvida,
severidade ALTA. Um ponto cego superestimado custa 5 minutos de leitura; um
subestimado é um ramo de processo que ninguém foi conferir.

### A.2 Procedência das variáveis

Para cada ocorrência opaca, listar **quais variáveis e parâmetros compõem a
string**. PL/Scope já registra as `ASSIGNMENT`/`REFERENCE` da variável, e a
árvore de contexto (já implementada em `plsqlflow/attribute.py`) resolve o
escopo.

No caso real da L29: `p_filtro_extra` e `p_ordenacao`. Transforma
"SQL desconhecido" em "montado a partir destes dois parâmetros — rastreie a
origem deles". Acionável; o alvo em si continua desconhecido.

### A.3 Efeito na saída

`## PONTOS CEGOS` ordenado por severidade, com a forma e a procedência em cada
entrada. Correlato: builtins de `SYS.STANDARD` (`NVL`/`ROUND`/`SUBSTR`/…) saem
da seção para uma linha-resumo — hoje afogam o que exige ação humana (20
entradas no `GESTAO_OO`, quase todas builtin). *Este correlato já está sendo
corrigido no contrato atual; registrado aqui só para não se perder o motivo.*

## 3. Item B — cruzamento estático × runtime

É o item que fecha de verdade, e o que nenhuma melhoria de análise estática
alcança.

### Por que agora é viável

O repo já tem a base, escrita para este fim: `sql/flow/hprof_setup.sql` e
`sql/flow/hprof_report.sql`, cuja própria docstring diz *"árvore pai→filho com
contagem de chamadas e tempo, para sobrepor ao grafo estático"*.

O que faltava era o mapa no grão certo. `hprof_report.sql` projeta
`owner` / `module` / `function` — **exatamente o grão
`OWNER.OBJETO.SUBPROGRAMA` dos nós do `procgraph`**. O join é natural agora; no
grão objeto do `depgraph` não era.

### O que o cruzamento produz

Rodar o processo com `DBMS_HPROF` ligado registra as chamadas **efetivamente
executadas**, inclusive as que vieram de SQL dinâmico. Comparando os dois
conjuntos:

| situação | leitura |
|---|---|
| no perfil, **não** no grafo | **exatamente o que o SQL dinâmico escondeu** — é o achado que justifica o item |
| no grafo, **não** no perfil | caminho não exercitado por aquele cenário — não é erro, é cobertura de teste |
| nos dois | confirmado |

A primeira linha é o produto. As outras duas são subproduto útil: a segunda
vira métrica de cobertura de cenário, o que importa para decidir se os casos de
teste da migração exercitam o processo todo.

### Limites honestos

- O perfil cobre **um cenário**, não o universo. Vários cenários → união.
- `DBMS_HPROF` exige `GRANT EXECUTE` e cria tabelas `DBMSHP_*` no schema —
  efeito colateral, **script entregue ao usuário, nunca executado pela skill**
  (é a regra que `hprof_setup.sql` já declara no cabeçalho).
- Overhead de profiling: não rodar em produção sem combinar.
- Alternativa parcial e sem instrumentação: `V$SQL` / `DBA_HIST_SQLTEXT`
  filtrado por módulo/tempo — pega o SQL que rodou, não a árvore de chamadas.
  Vale como plano B quando não dá para instrumentar.

## 4. O que continua irredutível depois dos dois itens

Registrar explicitamente para o documento não prometer mais do que entrega:

- Nome de objeto vindo de tabela de configuração ou parâmetro de runtime:
  nenhuma análise de compilação alcança, e o perfil só cobre os cenários
  executados.
- Dispatch de `OVERRIDING`: qual método roda depende do tipo do objeto em
  runtime. O grafo lista candidatos; o perfil diz qual rodou **naquele** dado.
- Qual ramo (`IF`/`CASE`/handler de exceção) é tomado para qual dado.
- Chamadas externas ao fechamento: job, scheduler, outra aplicação.
- **Correção de valor** — se o campo foi preenchido com o valor certo. Isso não
  é problema de grafo nem de perfil; é teste diferencial (rodar antigo e novo
  lado a lado sobre dado real e reconciliar campo a campo). Vale deixar escrito
  porque é o risco que mais se confunde com o de omissão, e o controle é outro.

## 5. Origem deste documento

Levantado durante a execução ao vivo do contrato `depgraph-granular` contra
`GESTAO_OO` (Oracle 21c local, 5 packages, hierarquia de tipos com
`OVERRIDING`, 1 view com INSTEAD OF trigger). Somente `SELECT`; nenhuma
alteração de dado.

Achado que originou a discussão: `OPEN v_cursor FOR v_sql` na L29 do
`PKG_DYNAMIC_EVALUATOR` saía classificado como cursor **estático** — o mapa
afirmava que não havia SQL dinâmico ali. Defeito de classificação, corrigido no
contrato atual. Este documento trata do que sobra **depois** dessa correção,
que é a limitação legítima do PL/Scope.

## 5.1 Defeito conhecido e NÃO corrigido: o modo `flow` tem o mesmo buraco

Fora do escopo do contrato `depgraph-granular`, por isso registrado aqui em vez
de silenciado.

`plsqlflow/report.py:45`:

```python
DYNSQL_STMT_TYPES = {"EXECUTE IMMEDIATE", "OPEN FOR", "DBMS_SQL.PARSE"}
```

O literal `"OPEN FOR"` está **factualmente errado**. Provado contra o banco: o
`OPEN v_cursor FOR v_sql;` da L29 do `PKG_DYNAMIC_EVALUATOR` chega com
`ALL_STATEMENTS.TYPE = 'OPEN'`, puro. Um ref cursor dinâmico nunca casa com
esse conjunto, então **o modo `flow` (skill `plsql-flow`) não reporta esse SQL
dinâmico** — mesmo defeito que foi corrigido no modo granular.

Situação por modo, depois do contrato atual:

| modo | detecção de `OPEN ... FOR` | como |
|---|---|---|
| `depgraph --granular` | **correta** | desambigua pelo texto-fonte (`_open_stmt_is_dynamic`), com regra de desempate para dinâmico |
| `depgraph` (objeto) | **correta, por acidente** | `depgraph_enrich._looks_dynamic_stmt_type` usa `startswith("OPEN")` — sobre-inclui, e sobre-incluir aqui é seguro |
| `flow` | **ERRADA** | igualdade exata contra `"OPEN FOR"`, que nunca ocorre |

Correção é de uma linha, mas `plsqlflow/graph.py`/`report.py` estão congelados
por golden test do contrato `plsqlflow-py` — mexer exige contrato próprio com
regeneração do golden. Não é trabalho grande; é trabalho que precisa de gate.

## 5.2 Defeito conhecido e NÃO corrigido: "Chamado por" vazio em nó não-resolvido

Achado durante a correção da colisão de arquivo entre builtins homônimos
(rodada 6 de verificação). O `to_ref` de uma aresta `CALL` para um nó
não-resolvido/builtin usa a **signature inteira**
(`__EXTERNAL__.<nome>.<signature completa>`), enquanto `node_ref`/
`node_filename` (usados para renderizar a seção `## Chamado por`)
reconstroem a identidade do nó a partir de `owner`/`object_name`/
`subprogram` — que carrega só o **sufixo curto** da signature (8
caracteres, ver seção 5.1 acima). As duas strings nunca batem, então a
seção `## Chamado por` de todo nó `UNKNOWN.UNKNOWN.*` (builtin ou alvo
genuinamente não resolvido) sai **vazia** — pré-existente, não introduzido
pela correção da colisão, confirmado ao vivo contra `GESTAO_OO`.

**Não é omissão do fato**: a chamada continua em `edges.jsonl` (com a
signature completa) e na seção `## Chama (outbound)` do nó **chamador**.
Só a vista "de quem me chama", olhando a partir do nó chamado, fica muda —
para um builtin isso quase nunca importa (a seção existe pra alvo real, não
pra `SYS.STANDARD`), mas para um alvo não-resolvido genuíno (linha
`## PONTOS CEGOS`) pode ser informação útil que fica só do lado errado do
grafo.

Fix mecânico (não feito aqui, fora do escopo da correção que o motivou):
fazer `_unresolved_ref`/`_ensure_unresolved_node` derivarem o mesmo sufixo
curto usado no `subprogram`, em vez de duas fontes de verdade (`ref` interno
vs. campos do `ProcNode`) para a mesma identidade.

## 6. Decisões pendentes antes de virar contrato

1. Item A e item B num contrato só, ou dois? (A é barato e independente; B
   exige instrumentação e decisão de ambiente.)
2. O cruzamento é um subcomando novo (`depgraph --hprof <runid>`) ou uma seção
   a mais no INDEX quando um runid é informado?
3. Como o perfil chega ao tool: consulta direta às `DBMSHP_*` na conexão, ou
   arquivo exportado? A primeira é mais simples; a segunda permite cruzar um
   perfil colhido em outro ambiente.
4. Vale enumerar cenários (perfis) e unir, ou um perfil por execução do
   comando?

## 7. Não-objetivos

- Não resolver o alvo de SQL dinâmico genuinamente composto em runtime — nem
  o item A nem o B prometem isso.
- Não substituir teste diferencial na migração.
- Não instrumentar produção automaticamente.
- Não alterar `plsqlflow/graph.py` (modo flow, congelado por golden test).
