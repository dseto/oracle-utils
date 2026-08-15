-- Resolve o alvo e suas assinaturas (overloads) com parametros por posicao.
-- Uma linha por (subprograma, overload, argumento); argument_name NULL na
-- linha de retorno de function. Comparar aridade/tipos com os parametros
-- fornecidos pelo usuario para escolher o overload.
-- Binds: :owner, :object_name (package ou standalone), :subprogram (NULL p/ standalone)
-- Fallback: all_procedures/all_arguments ja sao a visao de menor privilegio.
SELECT p.object_name,
       p.procedure_name,
       p.subprogram_id,
       p.overload,
       a.position,
       a.argument_name,
       a.in_out,
       a.data_type,
       a.type_owner,
       a.type_name
FROM   all_procedures p
LEFT JOIN all_arguments a
       ON a.owner = p.owner
      AND a.object_name = NVL(p.procedure_name, p.object_name)
      AND NVL(a.package_name, '-') = CASE WHEN p.procedure_name IS NULL
                                          THEN '-' ELSE p.object_name END
      AND NVL(a.overload, '-')     = NVL(p.overload, '-')
WHERE  p.owner = UPPER(:owner)
AND    p.object_name = UPPER(:object_name)
AND    ( :subprogram IS NULL OR p.procedure_name = UPPER(:subprogram) )
ORDER  BY p.subprogram_id, a.position;
