# Backlog — dossiê estático dos pontos de SQL dinâmico

Documento de backlog. **Não é contrato** — é o material para planejar um.

Substitui, em escopo, o `docs/backlog-depgraph-pontos-cegos.md`, que mirava
cruzamento com dado de runtime (`DBMS_HPROF`). O motivo da troca está na
seção 8.

## 1. Objetivo

Hoje o mapa granular declara que existe SQL dinâmico e para por aí: linha,
subprograma dono, trecho de fonte, alvo `?`. Isso é honesto e é pouco.

Este trabalho **não tenta descobrir o alvo**. O objetivo é outro e mais
modesto: para cada ponto de SQL dinâmico, registrar **todos os fatos que o
código-fonte e o compilador provam**, num formato que uma skill futura — com
acesso a dado de execução, log de aplicação ou parâmetro real — consiga
consumir e completar sem precisar reler o código do zero.

Em uma frase: **produzir o dossiê, não o veredito.**

## 2. Fronteira de segurança

Tudo aqui é leitura de dicionário e de fonte:

- `ALL_SOURCE`, `ALL_IDENTIFIERS`, `ALL_STATEMENTS` (PL/Scope), `ALL_ARGUMENTS`,
  `ALL_DEPENDENCIES` (só para o sinalizador de 4.3.1).
- Somente `SELECT`. Nenhum DML, nenhum DDL, nenhuma execução do processo
  analisado, nenhuma instrumentação, nenhuma criação de tabela.

O que a skill **nunca** faz, por desenho e não por configuração:

- executar o SQL dinâmico que encontrou, nem em ambiente de teste;
- executar o subprograma que o contém;
- afirmar qual é o alvo de um fragmento não resolvido.

## 3. Por que o estado atual não serve para a análise posterior

O relatório de hoje aponta a linha do `EXECUTE IMMEDIATE` / `OPEN ... FOR`.
Mas o idioma normal de PL/SQL **separa a montagem da execução**: a string é
construída em N atribuições e o sítio de execução recebe só o nome da
variável. Não é estilo de um projeto — é o que a linguagem induz, porque
concatenação condicional exige statements separados.

Consequência estrutural: **a linha que o relatório aponta é justamente a que
não tem informação nenhuma.** Quem abre o mapa vê entradas indistinguíveis e
não tem por onde começar — nem um humano, nem uma skill seguinte.

A informação existe, só não está onde o relatório olha: está no encadeamento
de atribuições que monta a variável, e na forma sintática do sítio de
execução.

> **Amostra usada neste documento.** Todos os exemplos vêm de um levantamento
> ao vivo contra `GESTAO_OO.PKG_DYNAMIC_EVALUATOR` (Oracle 21c local, só
> `SELECT`), que tem 3 pontos de SQL dinâmico:
>
> | linha | sítio de execução |
> |---|---|
> | 29 | `OPEN v_cursor FOR v_sql;` |
> | 54 | `EXECUTE IMMEDIATE v_sql INTO o_qtd_total, o_media_duracao, o_maior_peso;` |
> | 73 | `EXECUTE IMMEDIATE v_sql USING OUT v_resultado, IN p_id_entidade;` |
>
> É um schema pequeno e serve para **mostrar que o padrão existe e é
> tratável**, nunca como base estatística nem como catálogo do que a skill vai
> encontrar. Onde este documento cita linha ou nome de objeto, é ilustração.
> O que vale como regra está sempre justificado pela linguagem, não pela
> amostra — e a seção 4.6 lista o que a amostra **não** cobre.

## 4. Os fatos que a análise estática prova

Cinco grupos. Todos derivados de fonte e dicionário; nenhum é palpite.

### 4.1 Categoria provada pelo contexto sintático

Este é o achado mais forte, e não estava no documento anterior. A **forma do
sítio de execução** restringe, pelas regras da própria linguagem, o que o
fragmento pode ser — independentemente do texto do fragmento:

| forma do sítio | o fragmento **tem que** ser | pode invocar procedure? |
|---|---|---|
| `OPEN cur FOR <str>` | uma query (`SELECT`) | **não** (função no `SELECT`/`WHERE`, sim) |
| `EXECUTE IMMEDIATE <str> INTO a,b,c` | query de linha única, 3 colunas | **não** (idem) |
| `EXECUTE IMMEDIATE <str> USING OUT ...` | bloco anônimo ou DML com `RETURNING` | **sim** |
| `EXECUTE IMMEDIATE <str>` (sem `INTO`/`USING`) | qualquer coisa | **sim** — não determinável |
| `DBMS_SQL.PARSE` | qualquer coisa | **sim** — não determinável |

A tabela acima é derivada das regras da linguagem, não de observação. Vale
em qualquer schema.

O valor prático é o **fato negativo**: quando a forma prova que o sítio não
pode invocar procedure, aquele ponto sai da lista do que precisa ser
investigado à mão — e sai por prova, não por amostragem. Fato negativo
provado vale tanto quanto o positivo. Quantos pontos caem de cada lado depende
inteiramente do código analisado: uma base pode ter zero forma constrangida e
outra pode ter todas.

Registrar sempre a **prova**, não só a conclusão (`"OPEN ... FOR só aceita
query"`), para a skill futura poder discordar com base.

<details>
<summary>Conferido contra a amostra (ilustração)</summary>

Nos 3 pontos do `PKG_DYNAMIC_EVALUATOR`, a forma sozinha categorizou os três
corretamente — confirmado depois contra o texto reconstruído:

- **L29** → tem que ser `SELECT`. Não pode esconder chamada de procedure.
- **L54** → `SELECT` de linha única devolvendo 3 valores; as três variáveis de
  destino são `NUMBER`, logo 3 colunas numéricas. Não pode esconder chamada
  de procedure.
- **L73** → tem bind `OUT`, logo bloco anônimo ou DML com `RETURNING`. **Este
  pode esconder chamada de código.**

</details>

### 4.2 Reconstrução do template com lacunas nomeadas

Coletar todas as atribuições à variável dentro do subprograma e remontar o
texto, preservando o que é literal e marcando o que é lacuna. Exemplo tirado
da amostra (L43-49) — a forma do registro é o que importa, não o conteúdo:

```
'SELECT COUNT(*), NVL(AVG(x.duracao_estimada), 0), NVL(MAX(x.peso), 0) FROM '
   ⟨p_tabela_nome via DBMS_ASSERT.ENQUOTE_NAME⟩
' x '
[condicional: IF p_filtro_tipo IS NOT NULL]
   'WHERE VALUE(x) IS OF (' ⟨p_filtro_tipo via DBMS_ASSERT.ENQUOTE_NAME⟩ ')'
```

Três coisas ficam registradas: o texto literal, a posição de cada lacuna, e
que o trecho é **condicional** (montado dentro de um `IF`) — o que significa
que o SQL efetivo varia entre execuções, e a skill futura vai encontrar mais
de uma forma no runtime.

**Limites honestos da reconstrução**, que precisam sair marcados no dossiê:

- Atribuição dentro de `LOOP` — a repetição não é determinável estaticamente.
- Atribuição vinda de retorno de função (`v_sql := fn_montar(...)`) — regra
  própria, ver 4.2.1.
- Variável de pacote atribuída em outro subprograma — registrar as origens
  encontradas, marcar como múltiplas.
- Variável cujo valor vem de `SELECT` numa tabela — registrar **qual tabela e
  qual coluna**, e parar. Isso é ouro para a skill seguinte: ela sabe
  exatamente onde ler para completar.

Um template parcial marcado como parcial é útil. Um template parcial
apresentado como completo é o defeito que este documento existe para evitar.

### 4.2.1 Quando a cadeia bate numa função

`v_sql := pkg_helper.fn_montar_where(p_filtro)` interrompe a reconstrução. A
regra é entrar **só quando o resultado é inequívoco**:

> Atravessa se, e somente se, a função tem **exatamente um** `RETURN`, esse
> `RETURN` é reconstruível pelas mesmas regras de 4.2, e não há uma segunda
> travessia encadeada. Qualquer condição que falhe → **para e registra**.

Nunca enumerar variantes e nunca escolher uma delas. O motivo é assimétrico,
e é o mesmo do resto do contrato:

**Atravessar pode afirmar algo falso; parar não pode.** Costurar um `RETURN`
de três produz um SQL que talvez nunca exista — afirmação falsa vestida de
fato, que é o defeito mais caro porque parece informação boa. Parar erra só
por omissão declarada.

Nota sobre custo, para o contrato não repetir uma premissa errada: `v_sql :=
fn_montar(...)` **é uma chamada**. PL/Scope registra, o `procgraph` já
enfileira a função, ela já é nó do mapa e a fonte já foi buscada. Atravessar
não gera I/O novo nem alcança objeto fora do fechamento — o custo é de
lógica (combinatória dos `RETURN`, recursão, lacunas novas vindas dos
parâmetros da função), não de acesso.

### 4.3 Procedência de cada lacuna

Para cada lacuna, classificar a origem — sem tentar adivinhar o valor:

| origem | o que registrar | quem completa |
|---|---|---|
| literal no próprio subprograma | o literal (fato observado) | ninguém, já é fato |
| parâmetro formal | nome, tipo, posição, e os sítios de chamada **do fechamento** (4.3.1) | skill futura ou leitura humana |
| variável de pacote | os subprogramas que atribuem | skill futura |
| coluna de tabela | `owner.tabela.coluna` e o predicado do `SELECT` | skill futura, consultando o dado |
| retorno de função | `node_ref` da função no mapa, argumentos passados, e por que não atravessou (4.2.1) | contrato seguinte, ou leitura humana |
| outro dinâmico | o `site_id` do ponto anterior | encadeamento |
| não determinado | o motivo | — |

**Parar significa parar registrando.** As duas formas de parar não são
equivalentes:

```json
// parar mudo -- inutil, a skill seguinte reconstroi tudo do zero
{"ref": "L0", "origem": "NAO_DETERMINADO"}

// parar registrando -- a skill seguinte salta direto pro ponto certo
{"ref": "L0", "origem": "RETORNO_DE_FUNCAO",
 "funcao": "GESTAO_OO.PKG_HELPER.FN_MONTAR_WHERE",
 "argumentos": ["P_FILTRO"],
 "node_ref": "GESTAO_OO.PKG_HELPER.FN_MONTAR_WHERE",
 "motivo": "3 RETURN distintos"}
```

Com o `node_ref`, quem lê salta para o nó da função **que já está no mapa**,
com a fonte junto. A travessia vira trabalho de quem lê ou de um contrato
seguinte, a custo zero agora. Nenhuma lacuna pode sair do dossiê como
`NAO_DETERMINADO` sem o campo `motivo` preenchido.

Regra que atravessa todas as origens: **a skill registra onde olhar, nunca
conclui a partir dali.** Se a lacuna é parâmetro formal, registrar os sítios
de chamada permite a alguém — humano ou skill seguinte — ver se algum
chamador passa literal. Descobrir isso não é trabalho deste dossiê; entregar
o endereço é.

*Na amostra, a lacuna da L73 é `p_expressao_sql`, parâmetro formal de
`fn_avaliar_condicao_dinamica`.*

### 4.3.1 Escopo dos sítios de chamada

Os sítios listados são **só os do fechamento do mapa** — os subprogramas
alcançáveis a partir da raiz. Não é economia; é a resposta certa.

O mapa responde *"o que acontece quando eu rodo **este** processo"*. Se dentro
do fechamento o único chamador passa literal, a lacuna está resolvida **neste
processo** — fato, não aproximação. Varrer o schema inteiro encontraria um job
noturno passando variável e faria a mesma lacuna aparecer como não-resolvível:
uma varredura maior comprando uma resposta **pior**, e falsa para o processo
mapeado. É a mesma classe de erro do modo `flow`, que replica as chamadas do
pacote inteiro em cada nó e recorta a fronteira no lugar errado.

Varrer o schema também promete o que não entrega: chamador pode estar em outro
schema (grant + sinônimo), e achar todos exigiria `DBA_IDENTIFIERS` no
instance — privilégio que a convenção do repo não assume. O resultado seria
"todos os chamadores que consegui ver" vestido de "todos os chamadores".

**O que entra no lugar da varredura**: um sinalizador de uma consulta a
`ALL_DEPENDENCIES`, em grão objeto. Se algum objeto fora do fechamento depende
do package, existem chamadores externos — sabido **sem enumerá-los**:

```json
{"ref": "L0", "origem": "PARAMETRO_FORMAL",
 "call_sites_no_fechamento": ["APP.PKG_BATCH.SP_RODAR#118"],
 "chamadores_fora_do_fechamento": true,
 "chamadores_fora_fonte": "ALL_DEPENDENCIES (grao objeto, visibilidade do usuario atual)"}
```

Numa migração esse sinalizador é o aviso que interessa: *a função tem chamador
fora do processo que você está portando* — extrair ou reescrever quebra outra
coisa. Alto valor, custo de uma consulta.

O sinalizador também não promete completude — `ALL_DEPENDENCIES` mostra só o
que o usuário enxerga. A diferença é que ele é declarado como sinalizador, com
a fonte junto, e não como lista de chamadores.

**O nome do campo carrega o escopo.** `call_sites` seco seria lido como "os
sítios de chamada", ponto; `call_sites_no_fechamento` não dá para ler errado.
Escopo no nome custa menos que escopo em nota de rodapé que ninguém lê — mesma
disciplina de `categoria_prova` e `dominio_prova`.

### 4.4 Sanitização como fato de domínio

Quando uma lacuna passa por `DBMS_ASSERT` antes de entrar na string, o
wrapper **prova o domínio do valor**:

| wrapper | o valor só pode ser |
|---|---|
| `ENQUOTE_NAME` / `SIMPLE_SQL_NAME` / `QUALIFIED_SQL_NAME` | nome de objeto ou coluna |
| `SCHEMA_NAME` | nome de schema existente |
| `SQL_OBJECT_NAME` | objeto existente e acessível |
| `ENQUOTE_LITERAL` | literal de texto, não identificador |
| nenhum | não restringido — concatenação crua |

Isso é forte para a fase seguinte: uma lacuna com domínio `NOME_DE_OBJETO`
pode ser cruzada contra `ALL_OBJECTS` em vez de contra texto arbitrário.

O dossiê registra o wrapper (ou a ausência dele) com a linha, como fato.
**Não emitir juízo de segurança** — concatenação crua é achado da skill
`plsql-review`; aqui o campo existe para restringir o domínio, e apontar é
efeito colateral.

*Na amostra, o mesmo package usa `DBMS_ASSERT` nos identificadores (L16, L46,
L49) e concatena predicado cru (L20, L24, L68) — os dois casos da tabela
convivendo no mesmo arquivo. É comum, não é regra.*

### 4.5 Chave de correlação para a fase seguinte

O prefixo literal do template reconstruído é uma **chave de busca pronta**.
Do caso real:

```
SELECT COUNT(*), NVL(AVG(x.duracao_estimada), 0), NVL(MAX(x.peso), 0) FROM
```

Isso é um `LIKE` direto contra `V$SQL.SQL_TEXT` ou `DBA_HIST_SQLTEXT`. A
skill estática **não consulta** essas views — ela só produz a chave. Quem
consulta é a skill seguinte, e recebe a chave pronta em vez de ter que
reconstruí-la.

Registrar também um `source_fingerprint` (hash das linhas de origem) para a
skill futura detectar que o código mudou desde o levantamento e o dossiê está
velho.

### 4.6 O que a amostra NÃO cobre — e o contrato precisa cobrir

A amostra tem 3 sítios, de 3 formas. O universo é maior, e um contrato
escrito só contra o que a amostra mostrou nasce com buraco. Formas que
precisam de tratamento definido **antes** de virar tarefa:

| forma | por que é diferente | tratamento mínimo |
|---|---|---|
| `EXECUTE IMMEDIATE <str>` sem `INTO`/`USING` | não determinável pela forma; pode ser DDL, DML ou bloco | `pode_invocar_procedure: true`, categoria `NAO_DETERMINAVEL` |
| `EXECUTE IMMEDIATE ... BULK COLLECT INTO` | query multi-linha; aridade vem da coleção, não de escalares | `into_arity` derivado do tipo da coleção |
| `EXECUTE IMMEDIATE ... USING` só com `IN` | binds não restringem a categoria | não confundir com o caso `OUT` |
| DDL dinâmico (`'ALTER TABLE ' \|\| ...`) | efeito estrutural, não de fluxo nem de dado | registrar como categoria própria |
| `DBMS_SQL` (API completa) | montagem espalhada por `OPEN_CURSOR`/`PARSE`/`BIND_VARIABLE`/`EXECUTE`; a string entra no `PARSE`, os binds em outra chamada | tratar o `PARSE` como sítio, ligar os `BIND` pelo handle do cursor |
| sítio dentro de `LOOP` | o mesmo sítio executa N SQL diferentes | marcar `em_loop: true`; o dossiê descreve uma forma, não uma execução |
| literal 100% estático (`EXECUTE IMMEDIATE 'COMMIT'`) | não tem lacuna nenhuma | `reconstrucao: completa`, sem lacunas — caso trivial que precisa existir no teste |
| objeto sem PL/Scope ou *wrapped* | não há árvore de atribuição para percorrer | declarar o sítio pelo que `depgraph` já sabe e marcar a razão |

O último merece cuidado: **um objeto que não dá para analisar precisa aparecer
no dossiê assim mesmo**, com o motivo. Sumir da lista porque não foi possível
analisar é exatamente a omissão silenciosa que este trabalho combate.

Nenhuma dessas linhas veio da amostra — vieram das regras da linguagem e da
API. É deliberado: o contrato tem que ser dimensionado pelo que a linguagem
permite, não pelo que um schema de exemplo continha.

## 5. Formato de saída — o handoff

**"Dossiê" é a informação, não um arquivo.** Ele se materializa em dois, e a
regra que mantém isso honesto é uma só:

| arquivo | para quem | papel |
|---|---|---|
| `dynamic_sql.jsonl` | máquina | **forma canônica** — fonte da verdade |
| `SQL-DINAMICO.md` | humano | projeção legível da mesma informação |

> O `.md` é **gerado a partir** do `.jsonl`, nunca escrito em paralelo. Duas
> escritas independentes divergem na primeira manutenção, e aí existem duas
> verdades sem nenhuma marcada como derivada.

É o mesmo padrão que o mapa granular já usa (`edges.jsonl` canônico + node
`.md` legível), não a armadilha de informação duplicada.

**`dynamic_sql.jsonl`** — um registro por ponto, consumido por máquina. O
exemplo abaixo usa nomes neutros de propósito: o que está sendo especificado é
o **formato**, e um registro colado de um schema específico convida a
implementar contra aquele schema.

```json
{
  "site_id": "APP.PKG_RELATORIO.SP_TOTALIZAR#54",
  "owner": "APP",
  "object_name": "PKG_RELATORIO",
  "object_type": "PACKAGE BODY",
  "subprogram": "SP_TOTALIZAR",
  "line": 54,
  "exec_form": "EXECUTE_IMMEDIATE_INTO",
  "em_loop": false,
  "categoria_provada": "QUERY_LINHA_UNICA",
  "categoria_prova": "clausula INTO de EXECUTE IMMEDIATE so aceita query de linha unica",
  "pode_invocar_procedure": false,
  "into_arity": 3,
  "into_types": ["NUMBER", "NUMBER", "NUMBER"],
  "using_binds": [],
  "variavel_montada": "V_SQL",
  "reconstrucao": "parcial",
  "reconstrucao_motivo": "lacuna L1 vem de coluna de tabela",
  "template": [
    {"tipo": "literal", "texto": "SELECT COUNT(*), NVL(AVG(t.valor), 0), NVL(MAX(t.peso), 0) FROM ", "linha": 43},
    {"tipo": "lacuna", "ref": "L0", "linha": 46},
    {"tipo": "literal", "texto": " t ", "linha": 46},
    {"tipo": "literal", "texto": "WHERE t.situacao = ", "linha": 49, "condicional": "IF p_situacao IS NOT NULL"},
    {"tipo": "lacuna", "ref": "L1", "linha": 49, "condicional": "IF p_situacao IS NOT NULL"}
  ],
  "lacunas": [
    {"ref": "L0", "nome": "P_TABELA", "origem": "PARAMETRO_FORMAL",
     "tipo": "VARCHAR2", "dominio": "NOME_DE_OBJETO",
     "dominio_prova": "DBMS_ASSERT.ENQUOTE_NAME na linha 46",
     "call_sites_no_fechamento": ["APP.PKG_BATCH.SP_RODAR#118"],
     "chamadores_fora_do_fechamento": true,
     "chamadores_fora_fonte": "ALL_DEPENDENCIES (grao objeto, visibilidade do usuario atual)"},
    {"ref": "L1", "nome": "V_SITUACAO", "origem": "COLUNA_DE_TABELA",
     "tipo": "VARCHAR2", "dominio": null,
     "fonte_dado": "APP.TB_PARAMETRO.VALOR",
     "fonte_predicado": "WHERE chave = 'SITUACAO_PADRAO'",
     "motivo": "valor so existe em tempo de execucao"}
  ],
  "chave_correlacao": "SELECT COUNT(*), NVL(AVG(t.valor), 0), NVL(MAX(t.peso), 0) FROM %",
  "source_fingerprint": "sha256:...",
  "sanitizacao_ausente": []
}
```

Duas lacunas de origem diferente no mesmo registro, de propósito: uma
rastreável a chamador, outra que só o dado responde. O contrato precisa dos
dois caminhos no mesmo teste.

**`SQL-DINAMICO.md`** — a mesma informação legível, ordenada por
`pode_invocar_procedure` desc, depois por `reconstrucao` (parcial primeiro).
Cada entrada abre com a categoria provada e a prova, porque é o que decide se
o humano precisa investigar aquele ponto à mão.

### 5.1 Quem cita o quê

Três arquivos já falam do mesmo sítio, e sem regra explícita eles divergem.
O critério é **cada arquivo carrega o que muda a leitura dele**, e o detalhe
mora num lugar só:

| arquivo | o que carrega do sítio |
|---|---|
| `dynamic_sql.jsonl` | tudo — é a forma canônica |
| `SQL-DINAMICO.md` | tudo, legível; gerado do `.jsonl` |
| node `.md` do subprograma dono (seção `## SQL Dinâmico`, já existente) | só `categoria_provada`, `pode_invocar_procedure` e link para o dossiê |
| `INDEX.md`, seção `## PONTOS CEGOS` | uma linha por sítio + link |

O node `.md` é sobre o **subprograma**, não sobre o SQL dinâmico: ali interessa
"este subprograma tem um sítio que pode/não pode esconder chamada". Template,
lacunas e procedência ficam no dossiê. `INDEX.md` mantém a seção
`## PONTOS CEGOS` como navegação — deixa de ser onde a informação mora, sem
deixar de existir.

## 6. O que a skill declara, e o que ela nunca declara

**Declara**: onde está, qual a forma do sítio, qual categoria a linguagem
prova, o template com lacunas, a origem de cada lacuna, o domínio quando
`DBMS_ASSERT` prova, os sítios de chamada, a chave de correlação, e o que
ficou incompleto e por quê.

**Nunca declara**: qual objeto é acessado, qual procedure é chamada, se o
ponto é perigoso, quantas vezes executa, ou que a reconstrução está completa
quando não está.

Regra de desempate, mesma assimetria do contrato `depgraph-granular`: na
dúvida entre `reconstrucao: completa` e `parcial`, é **parcial**. Entre
"prova que não invoca procedure" e "não determinável", é **não determinável**.
Declarar de menos é o defeito caro; declarar de mais custa leitura.

## 7. O que a skill futura recebe pronto

Para a fase que tiver dado de runtime, log de aplicação ou parâmetro real:

| ela precisa de | o dossiê já entrega |
|---|---|
| onde procurar | `site_id` estável + `source_fingerprint` para detectar drift |
| o que procurar | `chave_correlacao` (prefixo literal, pronto para `LIKE`) |
| o que ainda falta saber | as `lacunas` com origem classificada |
| onde ler o que falta | `call_sites_no_fechamento`, ou `owner.tabela.coluna` quando a lacuna vem de dado |
| se o alcance do mapa basta | `chamadores_fora_do_fechamento` — avisa que a lacuna tem origem fora do processo |
| o que já é fato provado | `categoria_provada` + `categoria_prova` |
| se vale instrumentar | `pode_invocar_procedure` — só os `true` justificam profiling de chamada |

Essa última linha é o motivo de o dossiê vir antes de qualquer trabalho de
runtime: ele diz **quais pontos justificam o custo** de instrumentar.

## 8. Por que o cruzamento com runtime saiu de escopo

Registrado para a decisão não se perder, e porque a versão anterior deste
backlog apontava para o caminho errado.

O erro da versão anterior era tratar "SQL dinâmico" como **um** buraco. São
dois, e cada um exige um instrumento diferente:

| buraco | exemplo | instrumento |
|---|---|---|
| **código** invocado dinamicamente | `EXECUTE IMMEDIATE 'BEGIN pkg.proc; END;'` | `DBMS_HPROF` |
| **dado** acessado dinamicamente | `EXECUTE IMMEDIATE 'SELECT ... FROM ' \|\| v_tab` | `V$SQL` / `DBA_HIST_SQLTEXT` / trace 10046 |

O motivo é uma propriedade do instrumento, não de nenhum código analisado:
`DBMS_HPROF` é profiler de **PL/SQL** — grava chamadas de subprograma e não
enumera as tabelas que um SQL tocou. Um perfil de execução, por mais completo,
nunca fecha o buraco de acesso a dado.

Qual dos dois predomina varia por base: uma que usa dinâmico para despachar
procedure precisa de `DBMS_HPROF`; uma que usa para montar query precisa de
`V$SQL`. A maioria terá os dois. *Na amostra, 2 dos 3 sítios eram acesso a
dado — ilustra a distinção, não estabelece proporção.*

Nenhum dos dois instrumentos cabe neste trabalho: ambos exigem executar o
processo. E o dossiê é pré-requisito dos dois — sem ele, a fase de runtime não
sabe o que procurar (`chave_correlacao`), contra o que comparar
(`categoria_provada`), nem quais sítios sequer justificam instrumentar
(`pode_invocar_procedure`).

## 9. O que continua irredutível

Depois deste trabalho, continuam sem resposta — e o dossiê tem que dizer isso
em vez de fingir:

- Valor de lacuna que vem de tabela de configuração ou input de usuário.
- Qual ramo condicional foi montado em cada execução (o dossiê lista as
  variantes possíveis; não diz qual ocorre).
- Dispatch de `OVERRIDING`: o grafo lista candidatos, nada estático diz qual
  roda.
- Chamadas ao processo vindas de fora do fechamento (job, scheduler, outra
  aplicação).
- Correção de valor na migração — é teste diferencial, controle diferente.

## 10. Defeitos conhecidos e NÃO corrigidos

Herdados do documento anterior; continuam válidos e fora deste escopo.

### 10.1 Modo `flow` não detecta ref cursor dinâmico

`plsqlflow/report.py:45`:

```python
DYNSQL_STMT_TYPES = {"EXECUTE IMMEDIATE", "OPEN FOR", "DBMS_SQL.PARSE"}
```

`ALL_STATEMENTS.TYPE` nunca vale `'OPEN FOR'` — vale `'OPEN'` puro (provado
contra o banco na L29 do `PKG_DYNAMIC_EVALUATOR`). O modo `flow` portanto não
reporta esse SQL dinâmico. Correção é de uma linha, mas `report.py` está
congelado por golden test do contrato `plsqlflow-py`: exige contrato próprio
com regeneração do golden.

Situação por modo: `depgraph --granular` correto (desambigua pelo texto-fonte,
com desempate para dinâmico); `depgraph` objeto correto por sobre-inclusão
(`startswith("OPEN")`); `flow` errado.

### 10.2 "Chamado por" vazio em nó não-resolvido

O `to_ref` de aresta `CALL` para nó não-resolvido usa a signature inteira,
enquanto `node_ref`/`node_filename` usam o sufixo curto de 8 caracteres. As
strings nunca batem, então `## Chamado por` sai vazia em todo nó
`UNKNOWN.UNKNOWN.*`. Não é omissão do fato — a chamada continua em
`edges.jsonl` e na seção `## Chama` do chamador. Fix mecânico: unificar as
duas fontes de identidade em `_unresolved_ref`/`_ensure_unresolved_node`.

### 10.3 Travessia de função (T-03) superconta RETURN de subprograma aninhado

Achado na 2ª rodada de revisão cega do contrato `dynsql-dossie`, depois da
correção do defeito bloqueante da 1ª rodada (contagem de `RETURN` quebrada
quando a própria expressão continha `IS`/`AS` — `RETURN v IS NOT NULL;`,
`RETURN (SELECT c AS x FROM d);` — corrigido ancorando a exclusão do
cabeçalho no casamento gramatical `FUNCTION nome(params) RETURN tipo
IS/AS`, não mais em heurística de "primeiro `;` antes do primeiro `IS`/
`AS`").

A correção resolveu o defeito original, mas `_return_statement_start_lines`
(`plsqlflow/dynsite_template.py`) varre o texto INTEIRO recebido como
`function_source_lines`, sem escopo por profundidade de aninhamento. Um
subprograma local (`FUNCTION`/`PROCEDURE` declarado na seção declarativa da
função externa) tem o próprio `RETURN` contado junto com o da função
externa — **supercontagem**, nunca subcontagem: uma função com exatamente 1
`RETURN` genuíno mais um subprograma aninhado com o seu próprio `RETURN`
(inclusive `RETURN;` vazio de uma `PROCEDURE` aninhada) é contada como 2, e
a travessia (backlog 4.2.1) recusa quando deveria atravessar.

**Falha para o lado seguro**: é o mesmo sentido de erro que a regra de
desempate já manda tomar na dúvida (recusar em vez de atravessar), nunca o
oposto (nunca fabrica uma reconstrução a partir do RETURN errado). Por isso
é registrado como não-bloqueante, não como o defeito da 1ª rodada.

**Dormente na integração atual**: `plsqlflow/cli.py` (T-07,
`_build_dynamic_sql_records`) chama `resolve_gaps(template,
function_sources={})` — nenhuma função é passada para travessia hoje, então
este defeito não tem como aparecer no dossiê gerado enquanto essa limitação
existir. Só passa a importar quando um contrato futuro ligar
`function_sources` a fonte real.

Fix (não feito aqui, fora do escopo desta rodada): escopar
`_return_statement_start_lines` pela profundidade de aninhamento de
subprograma — parar de contar `RETURN` assim que a varredura entra na
declaração de um `FUNCTION`/`PROCEDURE` local (mesmo espírito de
`_function_header_spans`, mas delimitando um span de CORPO aninhado inteiro
a excluir, não só a linha de cabeçalho) e só voltar a contar `RETURN` da
função externa depois do `END <nome do aninhado>;` correspondente. Precisa
entrar junto com a tarefa que ligar `function_sources` a dado real — antes
disso não há caso de teste vivo para provar a correção contra o pipeline de
verdade.

## 11. Não-objetivos

- Não resolver o alvo de nenhum SQL dinâmico.
- Não classificar severidade ou risco — o dossiê entrega fatos, a priorização
  é de quem lê.
- Não executar nada: nem o SQL encontrado, nem o subprograma que o contém,
  nem o processo.
- Não consultar `V$SQL`, `DBA_HIST_*`, `DBMSHP_*` ou qualquer fonte de
  runtime.
- Não emitir parecer de segurança sobre concatenação crua — registrar o fato
  e apontar para `plsql-review`.
- Não alterar `plsqlflow/graph.py` nem `report.py` (congelados por golden
  test).

## 12. Decisões pendentes antes de virar contrato

1. ~~O dossiê sai sempre ou sob flag?~~ **Decidido — sempre.** Coerente com
   "omissão é o defeito caro": um dossiê que só sai sob flag é um dossiê que
   não sai para quem não sabe que precisa dele, e quem não sabe que tem SQL
   dinâmico no processo é exatamente quem mais precisa saber. Consequência para
   o contrato: sem sítio dinâmico no fechamento, os arquivos saem **vazios e
   declarados** (`0 sitios`), nunca ausentes — arquivo ausente é indistinguível
   de skill que não rodou.
2. ~~Atravessa chamada de função ou para?~~ **Decidido** — regra em 4.2.1:
   atravessa só com `RETURN` único e reconstruível, sem encadeamento; caso
   contrário para e registra (4.3). Nunca enumera variantes, nunca escolhe
   uma. Falta só fixar no contrato o teste que prova a regra nos dois
   sentidos: função de `RETURN` único é atravessada, função de 2+ `RETURN`
   sai como `RETORNO_DE_FUNCAO` com `motivo` preenchido.
3. ~~`call_sites` fica no fechamento ou varre o schema?~~ **Decidido** — regra
   em 4.3.1: a lista fica no fechamento (é a resposta certa para a pergunta do
   mapa, não economia), o campo se chama `call_sites_no_fechamento`, e entra um
   sinalizador `chamadores_fora_do_fechamento` vindo de `ALL_DEPENDENCIES` em
   grão objeto. Falta fixar no contrato o teste que prova os dois lados: lacuna
   resolvida por literal **dentro** do fechamento continua resolvida mesmo
   existindo chamador externo passando variável, e o sinalizador sai `true`
   nesse mesmo caso.
4. ~~`SQL-DINAMICO.md` à parte ou seção do `INDEX.md`?~~ **Decidido** — à
   parte, com a divisão de responsabilidade de 5.1 e a regra de que o `.md` é
   gerado do `.jsonl` canônico. A lição do contrato anterior (informação em
   dois lugares vira omissão quando alguém lê só um) é atendida por derivação,
   não por arquivo único: existe uma fonte da verdade e todo o resto é projeção
   dela, com link de volta. Falta fixar no contrato o teste de que o node `.md`
   e o `INDEX.md` nunca contradizem o `.jsonl` — a reconciliação que a
   `COBERTURA` já faz para arestas, aplicada aos sítios.
5. ~~Qual a segunda base de validação?~~ **Decidido** — fixture sintética +
   `GESTAO_OO`, só. Outras bases quando houver necessidade.

   **O que isso custa, registrado para não virar surpresa**: o desenho deste
   documento saiu dos 3 sítios do `GESTAO_OO`, então validar contra ele é
   parcialmente circular — prova que a implementação faz o que o desenho diz,
   não que o desenho generaliza. E a lição do contrato anterior continua de pé:
   dos 6 bloqueantes achados em 7 rodadas de revisão cega, **nenhum** veio de
   fixture sintética; todos vieram de fonte real inesperada.

   Três coisas reduzem o risco dentro do escopo escolhido, e precisam entrar no
   contrato como exigência, não como boa intenção:

   - **A fixture sintética é derivada da seção 4.6, não da amostra.** Ela é a
     única fonte de não-circularidade disponível aqui; se for escrita olhando
     para o `PKG_DYNAMIC_EVALUATOR`, o contrato fica sem nenhuma. Cada linha da
     tabela de 4.6 vira pelo menos um caso, incluindo as formas que a amostra
     não tem (`DBMS_SQL`, DDL dinâmico, `BULK COLLECT`, sítio em `LOOP`,
     literal 100% estático, objeto sem PL/Scope).
   - **Rodar contra o fechamento inteiro do `GESTAO_OO`, não contra os 3 sítios
     conhecidos.** O fechamento tem 27 objetos e 5 packages; só 3 sítios foram
     examinados à mão. O resto do schema é fonte real ainda não vista pelo
     desenho — é a parte não-circular que já está disponível de graça.
   - **Revisão cega sobre a fonte.** Verificador independente lê o PL/SQL e
     lista os sítios por conta própria, depois compara com o dossiê. É o
     mecanismo que achou os 6 bloqueantes; funciona mesmo em schema de onde o
     desenho veio, porque o verificador não viu o desenho.

   A generalização fica **não provada** — declarada, não escondida. Quando
   aparecer a primeira base nova, o esperado é achar defeito; isso será
   confirmação da limitação registrada aqui, não regressão.

## 13. Origem e limite da amostra

Levantado durante e após a execução ao vivo do contrato `depgraph-granular`
contra `GESTAO_OO` (Oracle 21c local, 5 packages, hierarquia de tipos com
`OVERRIDING`, 1 view com INSTEAD OF trigger, 3 sítios de SQL dinâmico).
Somente `SELECT`; nenhuma alteração de dado, nenhuma execução do processo
analisado.

**O que essa amostra é**: prova de que o padrão existe, é encontrável e é
tratável; e a fonte dos exemplos que tornam este documento legível.

**O que ela não é**: base estatística, catálogo de formas, nem referência de
implementação. Um contrato escrito para fazer os 3 sítios do
`PKG_DYNAMIC_EVALUATOR` passarem estaria pronto rápido e errado — cobriria 3
das formas da seção 4.6 e nenhuma das outras. A regra de dimensionamento é a
linguagem, não o exemplo.

**A validação também fica limitada a ela** (decisão 5): fixture sintética mais
este mesmo schema, por escolha de escopo. Consequência a carregar adiante — o
resultado do contrato será *"funciona no que foi testado"*, e não *"funciona em
PL/SQL"*. A primeira base nova provavelmente achará defeito; está previsto
aqui.
