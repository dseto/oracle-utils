# AGENTS.md — Diretrizes para Agentes

<!-- harness:lifecycle:begin -->
## Agent Session Lifecycle (gerado — 17 passos, docs/project/ROADMAP.md Fase 2)

1. Ler `AGENTS.md`.
2. Rodar `harness health` e parar se o ambiente não responder — é falha de
   infraestrutura (§8.3), não teste vermelho: não melhora tentando de novo.
   Dependência faltando se instala com `.harness/init.sh`/`.harness/init.ps1`.
3. Ler `.harness/progress.md`.
4. Ler `feature_list.json`.
5. Rodar `harness reconcile` e resolver toda divergência antes de seguir —
   estado declarado que não bate com o repositório envenena a sessão inteira.
6. Escolher exatamente UMA feature pendente — e colar `harness status --brief`
   no chat ao trocar de fatia, na abertura de cada iteração e em qualquer
   parada. A saída é montada por código: cole, nunca redija.
7. Planejar a implementação da feature escolhida — alternativa descartada por
   razão não óbvia vira `harness decide`.
8. Implementar a mudança dentro do raio de impacto declarado.
9. Rodar `verify_cmd` da tarefa — o `harness verify` ainda re-prova sozinho as
   tarefas concluídas que compartilham arquivo com esta; exit 2 = regressão a
   consertar antes de seguir. Tarefa com `metric` opcional (§4.3) também mede
   a trajetória logo depois, passe ou falhe — a métrica GUIA, quem decide
   `passes` continua sendo só o `verify_cmd`.
10. Se falhar (falha transiente já tenta de novo sozinha, 3× — não conta;
    tarefa com métrica também pode parar por piora/platô da trajetória):
    consultar `harness budget --feature <id>` e obedecer o veredito —
    autocorrigir e re-rodar só enquanto ele disser `continue`; em qualquer
    parada, usar o campo `escalation` da saída pronto, sem escrever à mão.
11. Registrar a prova (evidência da verificação bem-sucedida).
12. Atualizar `.harness/progress.md` com o estado atual.
13. Marcar a feature concluída em `feature_list.json`.
14. Documentar o que ficou quebrado, e anotar a fricção da sessão com
    `harness lesson` — o agente anota, quem compila é o humano.
15. Mostrar o trabalho a quem não o escreveu: `harness blind package` →
    despachar o pacote para um verificador com contexto limpo →
    `harness blind verdict`. E apresentar o que será commitado — por feature,
    descrição funcional em linguagem natural do que mudou, e link `file:line`
    do teste que prova.
16. Commit e push na branch do contrato, condicionados a `harness finish`
    com `blockers: []`. O PR é do humano: entregue o `harness pr-draft`.
17. Deixar a working tree limpa.

Detalhe de cada passo: ver `.harness/LIFECYCLE.md`.
<!-- harness:lifecycle:end -->

<!-- harness:begin -->
## Governança do Harness (gerado — edite .harness/harness.yaml e rode `harness compile`)

Política de aprovação: **auto**. Rede (WebFetch/WebSearch/curl)
sempre exige aprovação humana.

1. TDD recomendado (enforcement desligado nesta configuração).
2. **Orçamento (orientação)**: alvo de ~500,000 tokens
   por tarefa e 120 tool calls. O Claude Code não
   expõe contagem de tokens a hooks — este teto é disciplina, não enforcement;
   se a tarefa estourar muito, pare e replaneje com o humano.
3. **Artefatos temporários de verificação** (screenshots, dumps de rede,
   HTML de debug, JSON de resposta de API): salve SEMPRE em
   `.harness/scratch/` — única área liberada para arquivos que não pertencem
   a nenhuma tarefa do contrato. A pasta é auto-ignorada pelo git e apagável
   a qualquer momento; nunca referencie nada dela em código e nunca salve
   esses artefatos na raiz do repositório.
<!-- harness:end -->
