---
slug: dynsql-dossie
approved_by: daniel.rubens.seto@gmail.com
approved_at: 2026-08-17T12:00:00Z
stop_conditions:
  - "Precisar executar o SQL dinamico encontrado, o subprograma que o contem, ou o processo analisado -- parar e devolver ao humano"
  - "Precisar consultar V$SQL, DBA_HIST_*, DBMSHP_* ou qualquer fonte de runtime -- parar e devolver ao humano"
  - "Precisar afirmar qual e o alvo de um fragmento dinamico nao resolvido -- parar e devolver ao humano"
  - "Precisar alterar plsqlflow/graph.py ou plsqlflow/report.py (congelados por golden test do contrato plsqlflow-py) -- parar e devolver ao humano"
  - "Precisar de base de validacao alem da fixture sintetica e do schema GESTAO_OO -- parar e devolver ao humano"
  - "3 falhas consecutivas da mesma suite de teste -- parar e devolver ao humano"
---

# Spec: dossiê estático dos pontos de SQL dinâmico

## Resumo executivo

Quando o mapa de processo encontra um comando SQL montado em tempo de
execução, hoje ele diz apenas "existe um aqui, não sei o que faz". Quem vai
reescrever o processo em outra tecnologia fica sem saber se aquilo é um
detalhe ou um pedaço inteiro do processo que sumiu do mapa.

Esta demanda faz o mapa registrar, para cada um desses pontos, tudo que o
código-fonte e o compilador Oracle conseguem provar: que tipo de comando
aquilo obrigatoriamente é, qual o texto já conhecido, quais pedaços faltam,
de onde cada pedaço vem e onde procurar o resto.

O resultado não é a resposta — é o dossiê que permite responder depois, com
dados de execução, sem ter que reler o código do zero. E declara o que ficou
incompleto em vez de omitir.

## Escopo

Desenho completo e aprovado em `docs/backlog-sql-dinamico-estatico.md`,
incluindo as 5 decisões já fechadas. O que segue é o recorte executável.

Para cada sítio de SQL dinâmico alcançado pelo fechamento do
`depgraph --granular`, produzir um registro com:

1. **Categoria provada pela forma do sítio.** `OPEN ... FOR` só aceita query;
   `EXECUTE IMMEDIATE ... INTO a,b,c` só aceita query de linha única com 3
   colunas; bind `OUT` implica bloco anônimo ou DML com `RETURNING`. Cada
   conclusão vem acompanhada da prova. Onde a forma não restringe, sai
   `NAO_DETERMINAVEL` — nunca um palpite.
2. **Template reconstruído** a partir das atribuições à variável, preservando
   o texto literal, marcando cada lacuna, os trechos condicionais e se o
   sítio está dentro de um `LOOP`.
3. **Travessia de função** só quando inequívoca: `RETURN` único e
   reconstruível, sem encadeamento. Caso contrário para e registra a função,
   os argumentos e o motivo.
4. **Procedência de cada lacuna** — literal, parâmetro formal, variável de
   pacote, coluna de tabela, retorno de função, outro sítio dinâmico, ou não
   determinado com motivo obrigatório. Mais o domínio quando `DBMS_ASSERT`
   prova que o valor é nome de objeto.
5. **Sítios de chamada do fechamento** (não do schema inteiro), mais um
   sinalizador de chamadores externos derivado de `ALL_DEPENDENCIES`.
6. **Chave de correlação** (prefixo literal, pronta para `LIKE`) e
   `source_fingerprint`, para a fase de runtime consumir sem reconstruir.

Saída em dois arquivos: `dynamic_sql.jsonl` canônico e `SQL-DINAMICO.md`
gerado a partir dele. O dossiê sai sempre — sem sítio no fechamento, sai
vazio e declarado, nunca ausente.

Tudo é leitura: `ALL_SOURCE`, `ALL_IDENTIFIERS`, `ALL_STATEMENTS`,
`ALL_ARGUMENTS`, `ALL_DEPENDENCIES`. Somente `SELECT`.

## Critérios de aceitação

- Cada forma de sítio da seção 4.6 do backlog — incluindo `DBMS_SQL.PARSE`,
  DDL dinâmico, `BULK COLLECT INTO`, `EXECUTE IMMEDIATE` sem `INTO`/`USING`,
  sítio em `LOOP`, literal 100% estático e objeto sem PL/Scope — produz
  categoria e prova, e nenhuma sai do dossiê sem registro:
  `pytest tests/test_dynsite_categoria.py -q`
- O template preserva literal, lacuna, trecho condicional e `em_loop`, e
  reconstrução incompleta nunca sai marcada como completa:
  `pytest tests/test_dynsite_template.py -q`
- Função de `RETURN` único e reconstruível é atravessada; função de 2+
  `RETURN` sai como `RETORNO_DE_FUNCAO` com `motivo` preenchido, sem enumerar
  variantes e sem escolher uma: `pytest tests/test_dynsite_travessia.py -q`
- Cada lacuna sai com origem classificada, e nenhuma sai `NAO_DETERMINADO`
  sem `motivo`; wrapper `DBMS_ASSERT` prova o domínio com a linha:
  `pytest tests/test_dynsite_origem.py -q`
- Lacuna resolvida por literal dentro do fechamento continua resolvida mesmo
  existindo chamador externo passando variável, e o sinalizador
  `chamadores_fora_do_fechamento` sai `true` nesse mesmo caso:
  `pytest tests/test_dynsite_call_sites.py -q`
- `SQL-DINAMICO.md` é derivado do `dynamic_sql.jsonl` e a chave de correlação
  reproduz o prefixo literal do template:
  `pytest tests/test_dynsite_dossie.py -q`
- O dossiê sai sempre, vazio e declarado quando não há sítio; node `.md` e
  `INDEX.md` citam o sítio sem contradizer o `.jsonl`; o modo objeto e o modo
  `flow` seguem intactos: `pytest tests/test_dynsite_integracao.py -q`
- Suíte completa e lint limpos: `pytest -q` e `ruff check .`

## Não-objetivos

- Não resolver o alvo de nenhum SQL dinâmico.
- Não classificar severidade ou risco — o dossiê entrega fatos, a priorização
  é de quem lê.
- Não executar nada: nem o SQL encontrado, nem o subprograma que o contém,
  nem o processo analisado.
- Não consultar `V$SQL`, `DBA_HIST_*`, `DBMSHP_*` ou qualquer fonte de
  runtime; não instrumentar nada.
- Não emitir parecer de segurança sobre concatenação crua — registrar o fato
  e apontar para `plsql-review`.
- Não alterar `plsqlflow/graph.py` nem `plsqlflow/report.py` (congelados por
  golden test do contrato `plsqlflow-py`); o defeito conhecido do modo `flow`
  (seção 10.1 do backlog) continua sem correção.
- Não corrigir a seção "Chamado por" vazia em nó não-resolvido (seção 10.2 do
  backlog).
- Não estender o dossiê ao modo objeto (`depgraph` sem `--granular`) — o
  trabalho é sobre o mapa em grão subprograma.
- Não validar contra base além da fixture sintética e do schema `GESTAO_OO`
  (decisão 5 do backlog): a generalização fica declaradamente não provada.

## Unknowns

- Nenhum. O profile do repo não reportou `unknowns[]`, e as 5 decisões
  pendentes do backlog foram fechadas pelo usuário antes deste contrato.
