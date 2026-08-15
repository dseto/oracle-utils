---
slug: fix-plsql-flow-evidence-test
approved_by: Daniel Seto
approved_at: 2026-08-15T19:40:00Z
stop_conditions:
  - "3 falhas consecutivas do mesmo verify_cmd sem progresso"
  - "remover os 2 testes fizer o total de passed cair abaixo do valor atual (86), sinal de regressao de cobertura nao prevista"
---

# Spec: Remover testes de evidencia efemera em tests/test_plsql_flow.py (T-05)

## Resumo executivo
Dois testes automatizados estao quebrados de forma permanente e vao voltar a
quebrar a cada novo contrato fechado neste repositorio, porque checam um
arquivo que o proprio processo de encerramento de contrato apaga. A correcao
remove esses dois testes, ja que o que eles verificavam continua provado por
outros testes que ja existem e nao dependem de arquivo temporario.

## Escopo
`tests/test_plsql_flow.py::test_e2e_evidence_exists` e
`::test_e2e_evidence_content` (linhas ~197-213) leem
`.harness/scratch/plsql-flow-evidence.md`. Esse arquivo era a evidencia e2e
gravada durante a implementacao do contrato `plsql-flow` (T-05 da epoca), mas
`.harness/scratch/` e varrido pelo `harness finish` ao encerrar QUALQUER
contrato (comportamento por design do plugin harness-creator, ver
`.harness/hooks/boundary_guard.py`) — o arquivo nao sobrevive ao merge de
nenhum contrato, nem do proprio nem de contratos seguintes. Isso ja se repetiu
apos o contrato `plsqlflow-py` mais recente ser fechado.

Os dois testes devem ser removidos de `tests/test_plsql_flow.py` (constante
`EVIDENCE` incluida), com o docstring do modulo atualizado explicando o
motivo e apontando onde o comportamento real continua coberto:
`tests/test_plsqlflow_golden.py` (mermaid com recursao, trigger
`trg_flow_demo`, SQL dinamico irresoluvel e overload do FLOW_DEMO, comparado
byte a byte contra fixtures commitadas `tests/fixtures/flow_demo_golden.mmd`
e `flow_demo_extract.json`) e `tests/test_plsqlflow_skill.py` (convencao da
skill v2).

## Critérios de aceitação
- `pytest tests/test_plsql_flow.py -q` — nenhuma falha, e nenhum teste do
  arquivo le `.harness/scratch/plsql-flow-evidence.md`.
- `pytest -q -rs` (suite completa) — 0 failed; total de passed igual ou
  maior que o atual (86 passed, 1 skipped; skip identificado pela flag
  `-rs` para permitir baseline de skips conhecidos do harness).

## Não-objetivos
- Nao alterar `tests/test_plsqlflow_skill.py` — ele grava sua propria
  evidencia (`.harness/scratch/plsqlflow-py-evidence.md`) em tempo de
  execucao, dentro do proprio teste `live`; nao depende de estado
  persistido entre contratos, entao nao tem o mesmo problema.
- Nao alterar o pacote `plsqlflow/` nem `.claude/skills/plsql-flow/SKILL.md`.
- Nao criar arquivo de evidencia permanente novo em `docs/` — decisao
  tomada de que a evidencia so fazia sentido durante o ciclo de
  implementacao do contrato original, e o comportamento real ja tem prova
  permanente via testes.

## Unknowns
(nenhum)
