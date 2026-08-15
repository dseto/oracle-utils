-- MODO DINAMICO /plsql-flow - caminho realmente executado (pos DBMS_HPROF.ANALYZE).
-- Arvore pai->filho com contagem de chamadas e tempo, para sobrepor ao grafo estatico.
-- Binds: :runid (retornado por START_PROFILING/ANALYZE)
SELECT pci.parentsymid,
       pf.owner  AS parent_owner,
       pf.module AS parent_module,
       pf.function AS parent_function,
       pci.childsymid,
       cf.owner  AS child_owner,
       cf.module AS child_module,
       cf.function AS child_function,
       pci.calls,
       ROUND(pci.subtree_elapsed_time / 1000, 2) AS subtree_ms,
       ROUND(pci.function_elapsed_time / 1000, 2) AS self_ms
FROM   dbmshp_parent_child_info pci
JOIN   dbmshp_function_info pf
       ON pf.runid = pci.runid AND pf.symbolid = pci.parentsymid
JOIN   dbmshp_function_info cf
       ON cf.runid = pci.runid AND cf.symbolid = pci.childsymid
WHERE  pci.runid = :runid
ORDER  BY pci.subtree_elapsed_time DESC;
