# Grafo de dependências: GESTAO.FLOW_DEMO

## Estatísticas
- edges: 9
- leaves: 3
- max_depth: 20
- max_objects: 500
- needs_recompile: 0
- nodes: 5
- not_expanded: 0

## Fechamento transitivo
- GESTAO.FLOW_DEMO [PACKAGE]
- GESTAO.FLOW_DEMO_AUDIT [TABLE] (leaf)
- GESTAO.FLOW_DEMO_LOG [TABLE] (leaf)
- GESTAO.TRG_FLOW_DEMO_LOG [TRIGGER]
- SYS.STANDARD [PACKAGE] (leaf)

## PONTOS CEGOS
### SQL dinâmico não resolvido
- GESTAO.FLOW_DEMO L31 [partial] -> GESTAO.FLOW_DEMO_LOG
