---
slug: dotenv-conn
approved_by: daniel.rubens.seto@gmail.com
approved_at: 2026-08-15T22:34:10Z
stop_conditions:
  - "Senha aparecer em stdout, em evidencia de teste, em log ou na linha de comando de um processo (visivel em process list) em qualquer implementacao -- parar e redesenhar, nao mitigar"
  - "Qualquer necessidade de DML/DDL ou de conexao real ao banco (o contrato e 100% offline; toda prova usa .env temporario e dry-run)"
  - "3 falhas consecutivas do mesmo verify_cmd"
  - "Teste existente fora da superficie declarada precisar de mudanca de assercao -- devolver ao humano, nao ampliar superficie por conta propria"
---

# Spec: credenciais de conexao via .env no diretorio do consumidor

## Resumo executivo
Quem usa o oracle-utils a partir de outro projeto passa a poder deixar as
credenciais de conexao num arquivo `.env` do proprio projeto, em vez de
exportar variaveis de ambiente na mao a cada sessao. Todas as portas de
entrada que abrem conexao (CLI Python `flow`/`depgraph`, wrapper PowerShell
de fallback e os testes live) leem o mesmo `.env`, com a mesma regra: variavel
ja definida no ambiente real sempre ganha do arquivo.

## Escopo
Tres consumidores de credencial existem hoje e cada um resolve conexao de um
jeito:

1. `plsqlflow/db.py` (CLI `flow` e `depgraph`) -- le `PLSQLFLOW_USER`/
   `PLSQLFLOW_PWD`/`PLSQLFLOW_DSN` do ambiente, ou alias em
   `tools/flow-connections.json` + `PLSQLFLOW_PWD_<ALIAS>`. Nesta sessao foi
   adicionado um `load_dotenv()` interino em tempo de import -- errado por
   dois motivos: resolve o `.env` relativo ao pacote (nao ao diretorio de quem
   invoca) e polui o ambiente de qualquer processo que importe o modulo,
   testes inclusive. O contrato SUBSTITUI esse interino pelo desenho correto:
   carga lazy dentro de `resolve_connection_params`, somente quando o chamador
   nao injeta `env` explicito (testes que injetam dict continuam hermeticos),
   procurando o `.env` a partir do diretorio corrente do consumidor
   (`find_dotenv(usecwd=True)`) e nunca sobrescrevendo variavel ja definida
   (`override=False`). O caminho por alias se beneficia de graca: o
   `PLSQLFLOW_PWD_<ALIAS>` pode vir do mesmo `.env`.

2. `scripts/run-query.ps1` (fallback sem MCP) -- hoje so aceita conexao salva
   do SQLcl ou string EZConnect posicional. Passa a aceitar `-Connection env`:
   resolve `PLSQLFLOW_USER`/`PLSQLFLOW_PWD`/`PLSQLFLOW_DSN` do ambiente do
   processo e, para o que faltar, de um `.env` no diretorio corrente (parser
   PS 5.1 proprio, ASCII no codigo). Decisao de seguranca: a credencial NUNCA
   vai na linha de comando do SQLcl (visivel em process list) -- vai como
   linha `connect user/pwd@dsn` dentro do script temporario ja usado hoje,
   com `sql -S /nolog`, apagado no finally. Novo switch `-DryRun` imprime
   usuario e DSN resolvidos (nunca a senha) e sai antes de chamar SQLcl -- e
   o que torna o script provavel por teste sem banco e sem SQLcl.

3. `tests/conftest.py` (testes live gated) -- hoje so le
   `.harness/scratch/dev_creds.json`. Passa a tambem carregar o `.env` da
   raiz do repo (mesma regra: ambiente real ganha), entao maquina com `.env`
   preenchido roda os 2 testes live sem passo manual.

Documentacao acompanha: SKILL.md de `plsql-flow` e `oracle-dependency-graph`
e o CLAUDE.md documentam o `.env`; `.gitignore` ja ignora `.env` (entrada
adicionada pelo usuario nesta sessao -- falta so o newline final).

## Criterios de aceitacao
- CLI resolve credencial de `.env` no diretorio corrente do consumidor,
  ambiente real ganha do arquivo, e chamador que injeta `env` explicito nao
  sofre efeito colateral nenhum do dotenv: `pytest tests/test_db_dotenv.py -q`
- `run-query.ps1 -Connection env -DryRun` resolve usuario/DSN de `.env`
  temporario sem imprimir senha em nenhuma hipotese:
  `pytest tests/test_run_query_env.py -q`
- Suite completa verde, com os testes live rodando nesta maquina (o `.env`
  local tem credencial de dev): `pytest -q -rs`

## Nao-objetivos
- Trocar os nomes das variaveis (`PLSQLFLOW_*` fica como esta).
- Conexoes salvas do SQLcl MCP (`connmgr`) -- gerenciadas pelo SQLcl, dotenv
  nao se aplica.
- Declarar `python-dotenv`/`oracledb` como dependencia formal no
  `pyproject.toml` -- o repo nao gerencia dependencias Python hoje (nem
  `oracledb` esta declarado); formalizar isso e demanda separada.
- Criptografia, keyring ou qualquer cofre de credencial.

## Unknowns
- (nenhum -- profile sem unknowns)
