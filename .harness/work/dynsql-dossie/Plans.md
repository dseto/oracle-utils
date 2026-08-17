# Plans: dynsql-dossie

## [T-01] Cada ponto de SQL dinamico passa a declarar que tipo de comando ele obrigatoriamente e, com a prova, incluindo as formas que a amostra nao tinha
- files: `plsqlflow/dynsite.py`, `tests/test_dynsite_categoria.py`, `tests/fixtures/dynsite_formas.json`
- verify: `pytest tests/test_dynsite_categoria.py -q`

## [T-02] O texto do SQL montado em runtime passa a aparecer reconstruido, com os pedacos conhecidos, os que faltam e os que so entram sob condicao
- files: `plsqlflow/dynsite_template.py`, `tests/test_dynsite_template.py`
- verify: `pytest tests/test_dynsite_template.py -q`
- depends: T-01

## [T-03] Quando a montagem do SQL passa por uma funcao, o mapa so entra nela se o resultado for unico; caso contrario declara a funcao e para
- files: `plsqlflow/dynsite_template.py`, `tests/test_dynsite_travessia.py`
- verify: `pytest tests/test_dynsite_travessia.py -q`
- depends: T-02

## [T-04] Cada pedaco faltante do SQL passa a dizer de onde vem, e quando o codigo prova que e nome de objeto isso fica registrado
- files: `plsqlflow/dynsite_origin.py`, `tests/test_dynsite_origem.py`
- verify: `pytest tests/test_dynsite_origem.py -q`
- depends: T-02

## [T-05] Para pedaco que vem de parametro, o mapa lista quem chama dentro do processo e avisa quando existem chamadores fora dele
- files: `plsqlflow/dynsite_origin.py`, `plsqlflow/extract.py`, `plsqlflow/queries.py`, `sql/flow/dependentes_batch.sql`, `tests/test_dynsite_call_sites.py`
- verify: `pytest tests/test_dynsite_call_sites.py -q`
- depends: T-04

## [T-06] O dossie passa a ser gravado em duas formas -- uma para maquina e uma legivel derivada dela -- com a chave pronta para a analise seguinte
- files: `plsqlflow/dynsite_render.py`, `tests/test_dynsite_dossie.py`
- verify: `pytest tests/test_dynsite_dossie.py -q`
- depends: T-01, T-03, T-05

## [T-07] O dossie sai em toda geracao do mapa granular, vazio e declarado quando nao ha nada, e o resto do mapa aponta para ele sem contradize-lo
- files: `plsqlflow/cli.py`, `plsqlflow/procgraph_render.py`, `plsqlflow/dynsite_render.py`, `tests/test_dynsite_integracao.py`
- verify: `pytest tests/test_dynsite_integracao.py -q`
- depends: T-06
