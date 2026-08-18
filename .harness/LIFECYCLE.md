# Agent Session Lifecycle — Detalhe dos 17 Passos

Este arquivo é o detalhe de progressive disclosure do bloco "Agent Session
Lifecycle" em `AGENTS.md`. Cada passo abaixo corresponde ao ciclo de 17
passos descrito no `docs/project/ROADMAP.md` (Fase 2 — "Execução Autônoma no Raio de
Impacto"): a sessão nasce sabendo onde parou, trabalha dentro do contrato
aprovado e só devolve o controle ao humano em estado retomável.

1. **Ler `AGENTS.md`.** Primeiro passo de toda sessão: carregar a
   governança compilada (permissions, hooks, este próprio lifecycle) antes
   de tocar em qualquer arquivo do projeto.

2. **Rodar `harness health` (§7.2 do design).** Pergunta, numa passada só, se
   este projeto está em condições de trabalhar. São as três formas de o
   harness estar desprotegido **em silêncio** que o §8.3 nomeia junto:

   - **ferramenta indisponível** — o executável de algum `verify_cmd` do
     contrato não resolve, ou o módulo dele não importa. Sem esta pergunta, o
     defeito chega ao loop disfarçado de teste vermelho.
   - **governança desalinhada** — hook com interpretador irresolúvel (a tool
     call passa sem gate nenhum), `.claude/settings.local.json` ausente num
     repositório que parece governado, `.harness/` compilado com outra versão.
     É o que o `harness doctor` sempre soube achar, e só falava quando
     perguntado.
   - **proteção desligada** — o kill-switch ativo: os hooks em no-op. Neste
     repositório isso durou quatro dias sem ninguém ver.

   **Exit 2 é parada.** Ambiente quebrado é falha de **infraestrutura** (§8.3),
   e a resposta dela é oposta à da falha estrutural: não se autocorrige, não
   melhora tentando de novo, e o loop **não conserta** o próprio harness — se o
   problema for de governança ou de proteção, pare e escale ao humano.
   Dependência que falta se instala com `.harness/init.sh`/`.harness/init.ps1`,
   gerado a partir do profile; o health check constata que faltou, não resolve.

   Na sessão iniciada pelo Claude Code o hook `SessionStart` já injeta este
   veredito sozinho, antes de tudo — inclusive antes da reconciliação do passo
   5, porque corrigir registro num ambiente que não responde produz trabalho
   que ninguém consegue verificar. Rode o comando à mão quando o aviso não
   chegou: sessão retomada, execução fora do Claude Code, ou hook desinstalado.

3. **Ler `.harness/progress.md`.** Resumo do estado da sessão anterior — o
   que já foi feito, o que ficou pendente, o que quebrou. Evita retrabalho
   e recontagem de contexto pelo humano.

4. **Ler `feature_list.json`.** Lista de features do plano aprovado, cada
   uma com seu status (`pending`/`done`) e critério de verificação
   (`verify_cmd`).

5. **Rodar `harness reconcile`.** Compara o que o repositório DECLARA com o
   que ele TEM, e devolve as divergências em JSON (exit 0 = íntegro, 2 = há
   divergência, 1 = não foi possível checar). São quatro tipos, e nenhum
   deles apareceria num `git log` — que era o que este passo pedia antes:

   - `evidence_stale` — o `files_hash` gravado na prova não bate com o
     conteúdo atual dos `files[]`: a tarefa está marcada como feita, mas o
     código mudou depois da prova. Rode `harness verify <id>` de novo.
   - `evidence_missing` — tarefa com `passes: true` e nenhum arquivo de
     evidência, ou seja, marcada à mão.
   - `progress_contract_mismatch` — o `.harness/progress.md` descreve um
     contrato diferente do `feature_list.json`. É o resumo que você acabou de
     ler no passo 3; se ele é de outra demanda, tudo que você concluiu dele
     está errado. Rode `harness compile-session` para regenerá-lo.
   - `tree_residue` / `killswitch_active` — sobra de outro contexto na
     working tree, ou o harness rodando em no-op.

   Na sessão iniciada pelo Claude Code o hook `SessionStart` já injeta este
   relatório sozinho, e o passo é a conferência de que ele foi lido. Rode o
   comando à mão quando o aviso não chegou — sessão retomada, execução fora
   do Claude Code, ou hook desinstalado. **Divergência não é ruído a
   registrar: é trabalho a fazer antes de escolher uma fatia.** Seguir em
   cima de anotação errada é como o trabalho da sessão anterior se perde.

6. **Escolher exatamente UMA feature pendente.** Disciplina de escopo: a
   sessão trabalha em uma única feature por vez, nunca em paralelo dentro
   da mesma sessão — isso mantém o raio de impacto pequeno e revisável.

   **Colar o placar (contrato `placar-de-andamento`).** Rode e cole no chat,
   como saiu:

       harness status --brief

   Nas TRÊS fronteiras, e só nelas: na **abertura de cada iteração** (antes
   de rodar a prova do passo 9), na **transição de fatia** (aqui, ao escolher
   a próxima feature) e em qualquer **parada** (veredito do disjuntor,
   escalada, fim de sessão). Placar a cada tool call é ruído que mata o
   sinal — a cadência é a fronteira, não o evento.

   O bloco responde as quatro perguntas que a enxurrada de tool calls não
   responde: onde estou (tarefa quantas de quantas), o que está sendo feito
   agora, está indo bem (tentativa n de quantas, a última prova passou?) e o
   que vem a seguir. Quem acompanha a sessão sem dominar harness engineering
   não tem outra leitura do andamento.

   **Proibido redigir o placar de cabeça.** A saída é montada por código a
   partir do `feature_list.json`, do rastro de tentativas e da evidência —
   COLE o que o comando imprimiu, não escreva a sua versão dele. Placar
   auto-relatado é self-report, e self-report não vale como prova de
   andamento neste repositório: um agente que narra "vou bem" enquanto a
   prova está vermelha é exatamente o que o placar existe para tornar
   impossível.

   Os outros dois renders da MESMA fonte de dados são do humano, não do
   agente: `harness status --panel` (com `--watch N`) num segundo terminal, e
   a statusline que `compile-session` instala na barra do Claude Code.
   `harness status` sem flag continua sendo o JSON estruturado de sempre.

7. **Planejar a implementação da feature escolhida.** Antes de editar
   código, esboçar a abordagem: quais arquivos mudam, que testes cobrem a
   mudança, qual é o critério de pronto.

   Descartou uma alternativa por razão NÃO ÓBVIA, ou tomou uma decisão que
   restringe as iterações seguintes? Registre:

       harness decide "<título curto>" --decision "<o que foi decidido>" --why "<a razão, incluindo a alternativa descartada>"

   O registro é append-only (`.harness/decisions.md`) e as decisões recentes
   chegam sozinhas no contexto da próxima sessão. Sem isso, a sessão de daqui
   a duas semanas "descobre" e tenta de novo o caminho que esta aqui descartou
   por bom motivo — o motivo não estava em lugar nenhum que ela lesse. Não é
   ADR: três linhas bastam, e decisão óbvia não precisa de registro nenhum.

8. **Implementar a mudança dentro do raio de impacto declarado.** Editar
   apenas os arquivos ligados à feature escolhida — o `boundary_guard`
   (Fase 2) nega qualquer edição fora dessa superfície.

9. **Rodar `verify_cmd` da tarefa.** Comando de verificação vindo do
   contrato (build, lint, suíte de teste) — a prova executável de que a
   implementação funciona.

   Verde nesta tarefa não significa verde no repositório: ela pode ter
   quebrado uma tarefa já concluída. Por isso o `harness verify` faz também a
   **re-prova incremental** (§6 do design) — re-roda o `verify_cmd` das
   tarefas já `passes: true` que compartilham ARQUIVO com esta, a interseção
   declarada em `files[]`, nunca a suíte inteira (suíte completa é o gate
   final; dentro do loop ela só encarece a volta).

   Leia o exit code:

   - exit code 0 — nada acoplado regrediu. Siga.
   - exit code 2 — **regressão**: alguma tarefa concluída voltou a falhar. Ela já foi
     rebaixada para `passes: false`, com a tentativa registrada, e o
     `harness supervise` volta a devolvê-la. Conserte antes de escolher outra
     fatia: o diff suspeito ainda tem o tamanho de uma iteração, e é aqui que
     o conserto é barato.
   - exit code 1 — erro de execução do próprio comando (o de sempre).

   Um item `SEM VEREDITO` na saída é falha de ambiente (timeout, prova no
   runtime floor), não regressão: ninguém é rebaixado, mas aquela prova
   **não** foi confirmada — trate como falha de infraestrutura (passo 10).
   `--no-reproof` desliga a checagem; desligar custa exatamente a detecção de
   regressão entre fatias.

   **Métrica opcional (§4.3, contrato `convergencia-opt-in`).** Uma tarefa
   ganha o bullet `metric` (e um `target` de comparação, ex.: `>= 0.85`) no
   `Plans.md` quando as DUAS condições valem: (a) "meio pronto" é mensurável
   por um número que um comando imprime — similaridade visual, contagem de
   erros de lint, testes passando numa migração grande; (b) uma iteração
   pode PIORAR o artefato sem que o `verify_cmd` mude de veredito (ele
   continua vermelho, mas o resultado se afastou do alvo). Se qualquer uma
   falhar — teste passa/não-passa já cobre tudo, ou piora é impossível/
   irrelevante — a tarefa fica binária como sempre foi: bugfix com teste de
   regressão não precisa de `metric`; fidelidade visual, sim.

   Quando `metric` está presente, `harness verify` roda o comando logo
   depois do `verify_cmd`, passe ou falhe, e grava o valor no rastro de
   trajetória — sem passo manual, sem afetar o exit code desta tarefa.
   **Regra de ouro: a métrica GUIA o loop, quem decide "pronto" continua
   sendo só o `verify_cmd`.** Bater o alvo (`target_met`) nunca vira
   `passes` — é informativo, não um atalho.

10. **Se falhar: consultar o disjuntor e obedecer o veredito.** Loop de
    autocorreção (Fase 3): o agente conserta a própria falha e testa de
    novo, sem envolver o humano — mas não indefinidamente, e não por
    julgamento próprio sobre quando desistir.

    Antes de qualquer contagem, `harness verify` já tenta sozinho: um
    `verify_cmd` que falha com sinal reconhecidamente TRANSIENTE (timeout de
    aplicação, erro de rede/conexão — §8.1) tenta de novo até 3× com uma
    pausa curta entre tentativas, sem envolver você e sem gravar nada
    enquanto ainda houver tentativa sobrando — retry não é correção, é
    repetição. Se algum retry passar, a falha nem chega a existir no rastro.
    Isso é automático; não há passo manual aqui.

    Toda falha TERMINAL de `harness verify` (estrutural de primeira, ou
    transiente que esgotou os 3 retries) grava a tentativa em
    `.harness/attempts/<contrato>/<id>.jsonl` (erro cru, exit code,
    assinatura da falha, classificação). A cada vermelho, rode:

        harness budget --feature <id>

    e siga o `verdict`:

    - `continue` — corrija e re-rode o `verify_cmd`.
    - `stop_same_failure` — a MESMA falha se repetiu até o teto. O que está
      errado é a abordagem, não a execução: **mude de estratégia** (e diga
      qual, e por quê, ao reportar) ou escale. Insistir aqui é queimar o
      budget repetindo o que já não funcionou.
    - `stop_iterations` — o teto de tentativas desde o último verde
      estourou. Pare, registre o estado em `.harness/progress.md` e devolva
      o controle ao humano.
    - `stop_transient_exhausted` — o retry automático do §8.1 esgotou e o
      erro continua transiente. **Não é bug de lógica** — é o §8.3 batendo
      por outra porta ("mesmo erro transiente 3× → reclassificar como
      infra"): nunca healing automático, sempre parada + escalada. Não tente
      "corrigir" um `Connection refused` editando código.
    - `stop_worsening` — só para tarefa com `metric`/`target` (§4.3): as
      últimas 2 medições da trajetória pioraram frente ao melhor valor já
      registrado. O veredito nomeia o melhor estado (valor, commit) — retome
      dali, não continue empilhando por cima do que já piorou. O harness
      **não** reverte nada sozinho; agir é seu.
    - `stop_plateau` — idem, mas as últimas 3 medições não bateram um novo
      recorde (oscilação inclusa: subir e descer sem nunca superar o pico
      cai aqui, não em `stop_worsening`). Troque de abordagem ou escale com
      a curva registrada.

    Os tetos vêm, nesta ordem, das `stop_conditions:` TIPADAS do frontmatter
    do `spec.md` ativo (`{type: consecutive_verify_failures, n: 3}`,
    `{type: same_failure_signature, n: 3}`) e, na ausência delas, de
    `governance.budget.max_green_iterations` do `.harness/harness.yaml`.
    `stop_transient_exhausted` não usa teto nenhum — a primeira vez que
    acontece já é a resposta.

    As `stop_conditions:` escritas em PROSA continuam valendo como condição
    adicional — elas cobrem o que nenhuma contagem pega, como o sinal de
    impossibilidade ("a dependência não existe", "o requisito é
    contraditório"). Essas são lidas por
    `harness.contract.get_stop_conditions` e interpretadas por você; parar
    por uma delas é acerto, não desistência, e não precisa esperar teto
    nenhum.

    **Falha por DEPENDÊNCIA HUMANA não é nada disso — e não se resolve
    tentando de novo.** Quando o que trava a fatia é uma ação que só uma
    pessoa pode fazer (editar `.harness/harness.yaml` ou outro arquivo do
    plano de controle, instalar uma ferramenta, fornecer uma credencial,
    liberar um acesso), o vermelho não está te dizendo "corrija o código".
    Nenhum teto de tentativa vai ajudar: a parede é a mesma na tentativa 1 e
    na 21. **Não repita a tentativa.** Declare a parada:

        harness block <id> --needs "a ação concreta que cabe à pessoa"

    O `--needs` é obrigatório e é o texto que a pessoa vai ler no placar, no
    `progress.md` e no fecho da demanda — escreva a ação (arquivo, linha,
    comando), não o sintoma. Se houver um arquivo específico sendo esperado,
    acrescente `--watch <caminho>`: o bloqueio sai sozinho quando ele mudar.

    Declarada a parada, o harness inteiro passa a respeitá-la: `harness
    supervise` deixa de oferecer a fatia, o aviso de fim de sessão para de
    cobrar verificação dela, o placar mostra AGUARDANDO VOCÊ com a ação, e
    `harness finish` não encerra a demanda. Siga para outra fatia, se
    houver — a parada não paralisa o resto do contrato.

    A fatia volta a andar por três caminhos, e só por eles: a pessoa roda
    `harness unblock <id>`, o arquivo de `--watch` muda, ou `harness verify`
    passa. Não há expiração por tempo, de propósito: bloqueio que caduca
    sozinho volta a empurrar trabalho contra a mesma parede, com atraso.

    **Em qualquer parada, use o campo `escalation` da saída de `harness
    budget` — não escreva a mensagem de escalada à mão.** Ele já vem com as
    seis partes que o §8 exige, na ordem que ele exige (o que estava sendo
    tentado, o que foi tentado, o último erro cru, a classificação, o estado
    da spine, a sugestão de próximo passo); `null` quando o veredito é
    `continue`, texto pronto para copiar em qualquer outro veredito.

11. **Registrar a prova (evidência da verificação bem-sucedida).** Grava a
    evidência de que `verify_cmd` passou (timestamp, comando, hash) — é o
    que autoriza marcar a feature como concluída no passo 13.

12. **Atualizar `.harness/progress.md` com o estado atual.** Documenta o que
    foi feito nesta sessão, para que a próxima sessão (passo 3) retome sem
    perder contexto.

13. **Marcar a feature concluída em `feature_list.json`.** Só acontece com
    evidência fresca do passo 11 — marcar sem evidência é enfraquecer a
    garantia que todo o lifecycle existe para proteger.

14. **Documentar o que ficou quebrado, e anotar a fricção que apareceu.**
    Transparência: se algo ficou incompleto ou quebrado, isso é registrado
    explicitamente — nunca escondido atrás de um commit "limpo".

    Bateu numa fricção durante a sessão — regra que barrou demais, critério
    ambíguo, mensagem de erro que não ajudou, o mesmo erro pela terceira vez?
    Anote no momento em que aconteceu, uma linha, sem interromper o trabalho:

        harness lesson "<a fricção observada>" --fix "<melhoria candidata no harness/skill/critério>"

    **O agente anota; quem compila é o humano.** Não feche um item, não
    "aplique" a lição editando o harness, não abra issue por conta própria:
    auto-modificação do harness pelo próprio agente é a camada mais perigosa
    do design e não vale o risco. As lições em aberto aparecem no
    `harness finish` (campo `open_lessons`) — é ali que a pessoa as encontra.

15. **Mostrar o trabalho a quem não o escreveu.** Duas metades, e o §12 do
    design de loop engineering as junta no mesmo item de checklist porque são
    a mesma ideia com dois destinatários: antes de commitar, a entrega é
    olhada por alguém que não a produziu.

    **(a) A verificação independente — camada 3 (§6/§9.1).** As camadas 1 e 2
    provam que o teste passa; o teste foi escrito pela mesma cabeça que
    escreveu o código. Aqui entra o único ponto de independência que o design
    chama de obrigatório:

    1. `harness blind package` monta `.harness/scratch/blind-package.md` a
       partir do contrato — `desc`, `files[]` e `verify_cmd` de cada tarefa.
    2. Despache **esse arquivo, como está**, para um subagente novo — um
       verificador com contexto limpo, que não implementou nada disto.
       NÃO resuma a conversa, NÃO explique o que você
       fez, NÃO mande `.harness/work/<slug>/spec.md`, `.harness/progress.md`,
       `.harness/decisions.md`, `.harness/lessons.md` nem o `git log`: são o
       raciocínio de quem implementou, e o verificador que os lê valida as
       mesmas suposições que produziram o erro. O pacote é montado por código
       exatamente para você não precisar redigir esse prompt.
    3. O veredito volta com `harness blind verdict --pass|--fail --evidence
       "<o quê e onde>"`. Exit 2 é veredito de reprovação — resultado legítimo
       do passo, não falha.

    Reprovado: **o verificador não conserta**. O veredito volta ao loop, que
    decide o que fazer; quem corrige é quem implementa, e depois disso um
    veredito novo é registrado (o anterior fica no histórico). O `harness
    finish` do passo 16 bloqueia sem veredito, com veredito reprovado, e com
    veredito anterior ao código atual.

    **(b) A apresentação ao humano.** Este passo deixou de ser um gate: o
    ciclo tem UM pedido humano, a aprovação do contrato, e ela já autoriza o
    trabalho até o push. O que o passo continua exigindo é VISIBILIDADE — a
    sessão reporta, em mensagem clara e direta (não sub-entendida em log), o
    que mudou. Mostrar só o identificador da feature (`T-01`) e o JSON cru do
    `verify_cmd` **não é suficiente** — ninguém acompanha o que foi feito só
    com isso. Por feature, a mensagem PRECISA conter: (a) descrição funcional
    em linguagem natural do comportamento que mudou (não o nome do arquivo,
    não o comando — o que o teste efetivamente cobre), e (b) link direto
    `file:line` do teste que prova o critério, para o humano abrir e ler sem
    caçar. Além disso: o que ficou quebrado, se houver (passo 14).

16. **Commit e push na branch do contrato.**

    **Antes do commit, PERGUNTE ao desenvolvedor sobre docs/CHANGELOG/versão
    (contrato `setup-fail-closed-sem-init`, T-07).** A saída de `harness
    finish` traz o campo `docs_version` — versão corrente do pacote
    (`harness.__version__`), se o CHANGELOG tem entrada para ela, e se os
    marcadores de versão da documentação estão coerentes. Esse campo é
    puramente INFORMATIVO: nunca aparece em `blockers`, nunca muda o exit
    code de `harness finish`. Três garantias, sempre:

    - **Nunca fazer a atualização sozinho.** Editar CHANGELOG/versão/
      marcadores sem perguntar primeiro responde por conta própria a uma
      decisão que é do desenvolvedor.
    - **Nunca pular a pergunta** — mesmo quando `docs_version` já veio
      coerente, ou quando parece óbvio que não é o caso. Óbvio para o
      agente não é consentimento do humano.
    - **"Não" é resposta legítima.** Recusar a atualização não é pendência:
      segue direto para o commit, sem ela.

    Coerência com a convenção já vigente do projeto: o `chore` de
    versão/CHANGELOG normalmente é feito pelo humano, direto na `main`, fora
    do ciclo do harness. Esta pergunta não muda essa convenção — ela só
    abre uma exceção OPCIONAL: se o desenvolvedor disser sim, a atualização
    entra no MESMO commit da branch do contrato e é revisada no PR junto com
    o resto da entrega; se disser não, o `chore` de versão/CHANGELOG segue
    para depois, no terminal do humano, como sempre foi.

    Respondida a pergunta (sim ou não), o commit local (`git add`/`git
    commit`) e o `git push` da branch do contrato acontecem sem pedir
    autorização adicional — mas NÃO incondicionalmente. As duas
    pré-condições abaixo são o que substitui o antigo gate humano, e sem
    elas o agente para e chama a pessoa:

    - `harness finish` sai com `blockers: []` — o que já implica toda tarefa
      com `passes: true` e evidência cujo `files_hash` bate com o arquivo
      atual, isto é, prova que descreve o código que está sendo commitado;
    - nenhum `verify_cmd` vermelho.

    O push é só da branch do contrato (`contract/<slug>`) para ela mesma: o
    runtime floor do `boundary_guard` já restringe exatamente a isso — sem
    `--force`, sem refspec explícito, nunca a partir de branch protegida.
    Commit em `main` continua barrado, e o `chore` de versão/CHANGELOG segue
    sendo do humano, no terminal dele.

    **O agente NUNCA abre, aprova ou mergeia Pull Request.** Expor o trabalho
    para revisão e merge é decisão humana deliberada. O que o agente entrega é
    o trabalho pronto para isso: rode `harness pr-draft`, que monta o corpo do
    PR a partir do contrato e imprime o comando `gh pr create` exato, e
    repasse os dois ao humano.

17. **Deixar a working tree limpa.** Fim de sessão: nenhuma mudança solta
    fora de commit, nenhum arquivo temporário esquecido — o handoff para a
    próxima sessão (ou para o humano) começa de um estado previsível.
