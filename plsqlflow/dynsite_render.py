"""Montagem e renderizacao do dossie por sitio de SQL dinamico (T-06,
contrato dynsql-dossie).

Modulo PURO: nenhuma linha aqui abre conexao, executa o SQL analisado ou
toca rede -- so estrutura de dados ja produzida por T-01 (`dynsite.py`),
T-02+T-03 (`dynsite_template.py`) e T-04+T-05 (`dynsite_origin.py`), mais os
poucos campos soltos que o motor do mapa (`procgraph`, T-07) ja sabe sobre o
sitio (owner/object_name/object_type/subprogram/source_lines) e nao e
trabalho deste modulo descobrir.

Este e o modulo de MONTAGEM FINAL: junta os quatro resultados anteriores num
UNICO registro por sitio (`DynSiteRecord`) e sabe renderizar uma lista desses
registros em duas formas (docs/backlog-sql-dinamico-estatico.md, secao 5):

    `dynamic_sql.jsonl` -- forma CANONICA, uma linha JSON por sitio, fonte
    da verdade, consumida por maquina (`render_jsonl`).

    `SQL-DINAMICO.md` -- projecao LEGIVEL da MESMA informacao, ordenada por
    `pode_invocar_procedure` desc e depois por `reconstrucao` (parcial
    primeiro) -- as duas perguntas que decidem se um humano precisa
    investigar aquele ponto a mao (`render_markdown`).

REGRA DO CONTRATO, sem excecao: o `.md` e SEMPRE derivado da MESMA lista de
`DynSiteRecord` que gera o `.jsonl`, nunca escrito com dado proprio.
`render_markdown` so le campos ja presentes em `DynSiteRecord` -- nenhuma
outra fonte, nenhum recalculo que possa divergir do canonico. Duas escritas
independentes divergem na primeira manutencao (backlog secao 5); derivar por
construcao e a garantia estrutural, e `tests/test_dynsite_dossie.py` prova
isso explicitamente (todo `site_id` do `.jsonl` aparece no `.md`, nenhuma
categoria diverge -- item 9 da lista de casos obrigatorios do Plans.md).

Vazio e sempre DECLARADO, nunca ausente (backlog secao 12, decisao 1): lista
vazia de sitios ainda produz os dois textos -- `render_jsonl([])` devolve
string vazia (JSONL vazio e, por convencao, um arquivo de 0 bytes -- nao ha
"linha vazia" que fizesse sentido gravar) e `render_markdown([])` devolve um
markdown com aviso EXPLICITO ("Nenhum sitio de SQL dinamico neste
fechamento."), nunca um arquivo silenciosamente vazio sem dizer por que.
Quem grava os bytes em disco e responsabilidade de T-07 (este modulo so
devolve texto -- nenhum I/O aqui, mesmo padrao dos demais modulos puros de
`dynsql-dossie`).

============================================================================
NOMES DE CAMPO NO JSON DE SAIDA -- PORTUGUES, EXATAMENTE COMO O BACKLOG
============================================================================

`TemplatePart`/`TemplateGap` (dataclasses internos de `dynsite_template.py`)
ficam em ingles -- sao vocabulario interno entre os modulos T-02/T-03/T-06,
nao o formato de saida. A traducao acontece SO na hora de serializar
(`_serialize_template_part`): `kind` -> `tipo`, `text` -> `texto`,
`line` -> `linha`, `ref`/`condicional` mantem o nome (ja eram os nomes
usados internamente E no backlog). Uma parte `literal` carrega `texto` (sem
`ref`); uma parte `lacuna` carrega `ref` (sem `texto`) -- mesmo padrao do
exemplo em docs/backlog-sql-dinamico-estatico.md secao 5. `condicional` so
aparece quando nao-None (a maioria dos trechos nao esta dentro de IF).

============================================================================
`lacunas` -- CAMPOS FECHADOS, NAO UM ESPELHO CRU DE `LacunaOrigin.detalhe`
============================================================================

O contrato (T-06 do Plans.md) enumera uma lista FECHADA de campos para cada
lacuna: `ref`, `nome` (== `TemplateGap.raw_expr`, sempre o texto original do
token -- nunca o nome interno que `LacunaOrigin.detalhe` as vezes tambem
carrega sob a chave `"nome"`, ex.: `PARAMETRO_FORMAL`/`VARIAVEL_DE_PACOTE`),
`origem`, `tipo` (de `detalhe.get("tipo")`, quando disponivel -- pode ser
`None`), `dominio`, `dominio_prova`, o trio
`call_sites_no_fechamento`/`chamadores_fora_do_fechamento`/
`chamadores_fora_fonte` (SO quando presentes em `detalhe` -- `attach_call_sites`,
T-05, documenta que esses tres campos ficam AUSENTES, nao `None`, para
origem diferente de `PARAMETRO_FORMAL`; este modulo preserva essa distincao
em vez de forcar os tres campos sempre presentes), `via_funcao` e `em_loop`
(de `TemplateGap`, nao de `LacunaOrigin` -- sao fatos sobre a ATRIBUICAO que
produziu a lacuna, nao sobre a origem classificada dela) e `motivo`.

Escolha deliberada: NAO espalhar `LacunaOrigin.detalhe` inteiro no registro.
Alem do `"nome"` colidir com o `nome` deste modulo (valores DIFERENTES --
`detalhe["nome"]` de um `PARAMETRO_FORMAL` e o nome DECLARADO do parametro,
ex. `"P_TABELA_NOME"`; `raw_expr` e o token como apareceu no codigo, ex.
`"DBMS_ASSERT.ENQUOTE_NAME(p_tabela_nome)"` quando embrulhado -- os dois sao
fatos igualmente verdadeiros, mas so um cabe no campo `nome` do dossie),
campos como `"posicao"` (PARAMETRO_FORMAL), `"subprogramas_que_atribuem"`
(VARIAVEL_DE_PACOTE), `"funcao"` (RETORNO_DE_FUNCAO) e `"site_id"`
(OUTRO_DINAMICO) nao estao na lista fechada do contrato -- ficam disponiveis
em `LacunaOrigin.detalhe` para quem consumir `origins` diretamente antes de
`build_record`, mas nao duplicam no registro final. Um campo fechado e
testavel deterministicamente; um espelho cru do dict interno de outro modulo
faria o formato de saida mudar toda vez que T-04/T-05 acrescentasse uma
chave nova a `detalhe` para uso interno.

============================================================================
`chave_correlacao` -- prefixo literal do INICIO do template, nunca inventado
============================================================================

Percorre `template` (ja serializado) do INICIO ate a primeira parte que NAO
e `"literal"` (ou ate acabar a lista), concatenando `texto`. Duas formas de
sair `None`, ambas honestas (backlog secao 4.5 + secao 6, "declarar de menos
e o defeito caro, mas nunca inventar"): o template comeca com uma lacuna
(nenhum literal para concatenar), ou o(s) literal(is) do inicio sao string
vazia (concatenacao daria `""`, e um prefixo vazio seguido de `%` casaria
com QUALQUER SQL -- exatamente o "prefixo generico inventado" que o backlog
proibe). Quando ha texto real, o resultado e esse texto mais `%` no fim,
pronto para `LIKE` (nunca com `%` tambem no inicio -- o prefixo e ancorado
no comeco do SQL reconstruido, que E o comeco do SQL real).

============================================================================
`source_fingerprint` -- hash das MESMAS linhas usadas na reconstrucao
============================================================================

`hashlib.sha256` sobre as linhas de `source_lines` (o mesmo parametro
recebido por `classify_dynamic_site`/`reconstruct_template` -- este modulo
nao re-le nada, so faz hash do que o chamador ja tinha em maos) juntas por
`"\n"`, formatado como `"sha256:<hex>"`. Deterministico por construcao
(mesma entrada -> mesmo digest) e sensivel a QUALQUER mudanca de uma unica
linha (propriedade de hash criptografico) -- a skill futura (backlog secao
7) usa isto para detectar que o codigo-fonte mudou desde o levantamento e o
dossie esta desatualizado, sem ter que comparar o codigo inteiro linha a
linha.

============================================================================
`sanitizacao_ausente` -- campo RESERVADO, nunca populado nesta tarefa
============================================================================

O backlog (secao 4.4) descreve classificacao de dominio via `DBMS_ASSERT`
(isso E escopo do T-04/T-05, ja implementado em `dynsite_origin.py` --
aparece em `dominio`/`dominio_prova` de cada lacuna) separado de EMITIR
JUIZO DE SEGURANCA sobre concatenacao crua sem wrapper -- isso e
explicitamente um NAO-OBJETIVO do contrato `dynsql-dossie` (spec.md,
Nao-objetivos: "Nao emitir parecer de seguranca sobre concatenacao crua --
registrar o fato e apontar para plsql-review"). `sanitizacao_ausente` existe
na API publica (o contrato pede a forma completa do registro) mas
`build_record` SEMPRE devolve `[]` para este campo -- populá-lo e trabalho
de uma skill/contrato futuro (`plsql-review`), nao deste modulo.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .dynsite import DynSiteForm
from .dynsite_origin import LacunaOrigin
from .dynsite_template import RECONSTRUCAO_PARCIAL, ReconstructedTemplate, TemplateGap, TemplatePart

_CALL_SITE_DETAIL_KEYS = (
    "call_sites_no_fechamento",
    "chamadores_fora_do_fechamento",
    "chamadores_fora_fonte",
)


@dataclass(frozen=True)
class DynSiteRecord:
    """UM registro completo de sitio de SQL dinamico, pronto para
    `render_jsonl`/`render_markdown`. Ver docstring do modulo para o
    significado de cada campo e as escolhas de serializacao."""

    site_id: str
    owner: str
    object_name: str
    object_type: str
    subprogram: str
    line: int
    exec_form: str
    categoria_provada: str
    categoria_prova: str
    pode_invocar_procedure: bool
    into_arity: Optional[int]
    em_loop: bool
    variavel_montada: str
    reconstrucao: str
    reconstrucao_motivo: Optional[str]
    template: List[dict]
    lacunas: List[dict]
    chave_correlacao: Optional[str]
    source_fingerprint: str
    sanitizacao_ausente: List[str]


# ---------------------------------------------------------------------------
# Serializacao de template/lacuna -- traducao para os nomes de campo em
# portugues do formato de saida (ver docstring do modulo).
# ---------------------------------------------------------------------------


def _serialize_template_part(part: TemplatePart) -> dict:
    payload: Dict[str, object] = {"tipo": part.kind}
    if part.kind == "literal":
        payload["texto"] = part.text
    else:
        payload["ref"] = part.ref
    payload["linha"] = part.line
    if part.condicional:
        payload["condicional"] = part.condicional
    return payload


def _serialize_lacuna(gap: TemplateGap, origin: LacunaOrigin) -> dict:
    payload: Dict[str, object] = {
        "ref": gap.ref,
        "nome": gap.raw_expr,
        "origem": origin.origem,
        "tipo": origin.detalhe.get("tipo"),
        "dominio": origin.dominio,
        "dominio_prova": origin.dominio_prova,
    }
    for key in _CALL_SITE_DETAIL_KEYS:
        if key in origin.detalhe:
            payload[key] = origin.detalhe[key]
    payload["via_funcao"] = gap.via_funcao
    payload["em_loop"] = gap.em_loop
    payload["motivo"] = origin.motivo
    return payload


def _correlation_key(parts: Sequence[TemplatePart]) -> Optional[str]:
    """Prefixo literal do INICIO do template, pronto para `LIKE` -- ver
    docstring do modulo. `None` quando nao ha literal inicial nenhum, ou
    quando o unico literal inicial e string vazia (nunca inventa prefixo
    generico)."""
    prefix_chars: List[str] = []
    for part in parts:
        if part.kind != "literal":
            break
        prefix_chars.append(part.text or "")
    prefix = "".join(prefix_chars)
    if not prefix:
        return None
    return prefix + "%"


def _source_fingerprint(source_lines: Sequence[str]) -> str:
    joined = "\n".join(source_lines)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return "sha256:{}".format(digest)


def build_record(
    owner: str,
    object_name: str,
    object_type: str,
    subprogram: str,
    source_lines: Sequence[str],
    form: DynSiteForm,
    template: ReconstructedTemplate,
    origins: Dict[str, LacunaOrigin],
) -> DynSiteRecord:
    """Junta T-01 (`form`) + T-02/T-03 (`template`, ja passado por
    `resolve_gaps` se aplicavel) + T-04/T-05 (`origins`, um `LacunaOrigin`
    por `TemplateGap.ref`) num UNICO `DynSiteRecord`. `owner`/`object_name`/
    `object_type`/`subprogram` sao soltos porque quem monta ja sabe essa
    informacao do motor do mapa (`procgraph`) -- este modulo nao a
    descobre. `source_lines` e o MESMO texto-fonte ja usado para produzir
    `form`/`template` (mesma convencao indice 0 = linha 1 do resto de
    `dynsql-dossie`) -- usado aqui so para `source_fingerprint`.

    Levanta `KeyError` se `origins` nao tiver uma entrada para algum
    `template.gaps[i].ref` -- contrato da assinatura (docstring do
    parametro `origins`), nunca um `None` silencioso que produziria um
    registro de lacuna incompleto."""
    site_id = "{}.{}.{}#{}".format(owner, object_name, subprogram, form.line)

    template_serialized = [_serialize_template_part(part) for part in template.parts]

    lacunas_serialized: List[dict] = []
    for gap in template.gaps:
        origin = origins.get(gap.ref)
        if origin is None:
            raise KeyError(
                "lacuna {} sem LacunaOrigin correspondente em `origins` -- build_record "
                "espera uma entrada por TemplateGap.ref (template tem {} lacuna(s): "
                "{})".format(gap.ref, len(template.gaps), [g.ref for g in template.gaps])
            )
        lacunas_serialized.append(_serialize_lacuna(gap, origin))

    return DynSiteRecord(
        site_id=site_id,
        owner=owner,
        object_name=object_name,
        object_type=object_type,
        subprogram=subprogram,
        line=form.line,
        exec_form=form.exec_kind,
        categoria_provada=form.categoria_provada,
        categoria_prova=form.categoria_prova,
        pode_invocar_procedure=form.pode_invocar_procedure,
        into_arity=form.into_arity,
        em_loop=form.em_loop,
        variavel_montada=template.variavel,
        reconstrucao=template.reconstrucao,
        reconstrucao_motivo=template.reconstrucao_motivo,
        template=template_serialized,
        lacunas=lacunas_serialized,
        chave_correlacao=_correlation_key(template.parts),
        source_fingerprint=_source_fingerprint(source_lines),
        sanitizacao_ausente=[],
    )


# ---------------------------------------------------------------------------
# dynamic_sql.jsonl -- forma canonica (ver docstring do modulo)
# ---------------------------------------------------------------------------


def _record_payload(record: DynSiteRecord) -> dict:
    """Dict na ORDEM de chave do exemplo do backlog secao 5 -- ordem de
    insercao em Python 3.7+ ja e suficiente, nao precisa de OrderedDict."""
    return {
        "site_id": record.site_id,
        "owner": record.owner,
        "object_name": record.object_name,
        "object_type": record.object_type,
        "subprogram": record.subprogram,
        "line": record.line,
        "exec_form": record.exec_form,
        "categoria_provada": record.categoria_provada,
        "categoria_prova": record.categoria_prova,
        "pode_invocar_procedure": record.pode_invocar_procedure,
        "into_arity": record.into_arity,
        "em_loop": record.em_loop,
        "variavel_montada": record.variavel_montada,
        "reconstrucao": record.reconstrucao,
        "reconstrucao_motivo": record.reconstrucao_motivo,
        "template": record.template,
        "lacunas": record.lacunas,
        "chave_correlacao": record.chave_correlacao,
        "source_fingerprint": record.source_fingerprint,
        "sanitizacao_ausente": record.sanitizacao_ausente,
    }


def render_jsonl(records: Sequence[DynSiteRecord]) -> str:
    """Serializa `records` em JSONL -- uma linha `json.dumps` por registro,
    terminada em `"\\n"` (mesmo padrao byte-exato de
    `procgraph_render.render_edges_jsonl`). Lista vazia devolve string
    vazia (`""`) -- decisao registrada na docstring do modulo: um arquivo
    JSONL vazio e, por convencao, 0 bytes; nao ha "linha vazia" que fizesse
    sentido escrever para 0 registros. Nunca lanca, nunca devolve `None` --
    "sempre gerado, nunca ausente" (backlog secao 12, decisao 1) e
    responsabilidade de quem CHAMA esta funcao (grava o texto, mesmo
    vazio, em disco); esta funcao so garante que o texto em si nunca falta
    nem quebra."""
    if not records:
        return ""
    lines = [json.dumps(_record_payload(record), ensure_ascii=False) for record in records]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SQL-DINAMICO.md -- projecao legivel, DERIVADA (ver docstring do modulo)
# ---------------------------------------------------------------------------

_SEM_SITIOS_MENSAGEM = "Nenhum sitio de SQL dinamico neste fechamento."


def _reconstrucao_rank(reconstrucao: str) -> int:
    """`parcial` primeiro (backlog secao 5: "ordenada por
    pode_invocar_procedure desc, depois por reconstrucao (parcial
    primeiro)") -- qualquer valor que nao seja a constante de parcial
    (nunca deveria acontecer dado o contrato de `ReconstructedTemplate`,
    mas esta funcao nao adivinha) ordena depois."""
    return 0 if reconstrucao == RECONSTRUCAO_PARCIAL else 1


def _markdown_sort_key(record: DynSiteRecord):
    procedure_rank = 0 if record.pode_invocar_procedure else 1
    return (procedure_rank, _reconstrucao_rank(record.reconstrucao))


def _sim_nao(value: bool) -> str:
    return "sim" if value else "nao"


def _render_lacuna_line(lacuna: dict) -> str:
    parts = ["- {}: {} -- origem {}".format(lacuna["ref"], lacuna["nome"], lacuna["origem"])]
    if lacuna.get("dominio"):
        parts.append(" [dominio: {} ({})]".format(lacuna["dominio"], lacuna.get("dominio_prova") or "?"))
    if lacuna.get("motivo"):
        parts.append("; motivo: {}".format(lacuna["motivo"]))
    if lacuna.get("chamadores_fora_do_fechamento"):
        parts.append("; chamadores fora do fechamento: sim ({})".format(lacuna.get("chamadores_fora_fonte")))
    return "".join(parts)


def _render_site_lines(record: DynSiteRecord) -> List[str]:
    lines = [
        "## {}".format(record.site_id),
        "",
        "- categoria provada: {} -- {}".format(record.categoria_provada, record.categoria_prova),
        "- exec_form: {} | pode_invocar_procedure: {} | em_loop: {}".format(
            record.exec_form, _sim_nao(record.pode_invocar_procedure), _sim_nao(record.em_loop)
        ),
        "- reconstrucao: {}{}".format(
            record.reconstrucao,
            " -- {}".format(record.reconstrucao_motivo) if record.reconstrucao_motivo else "",
        ),
        "- variavel montada: {}".format(record.variavel_montada),
        "- chave_correlacao: {}".format(
            record.chave_correlacao if record.chave_correlacao else "(nenhuma -- template comeca em lacuna)"
        ),
        "- source_fingerprint: {}".format(record.source_fingerprint),
        "",
    ]
    lines.append("### Lacunas ({})".format(len(record.lacunas)))
    if not record.lacunas:
        lines.append("- nenhuma")
    else:
        lines.extend(_render_lacuna_line(lacuna) for lacuna in record.lacunas)
    lines.append("")
    return lines


def render_markdown(records: Sequence[DynSiteRecord]) -> str:
    """Gera `SQL-DINAMICO.md` a partir da MESMA lista de `DynSiteRecord`
    que `render_jsonl` serializa -- nunca dado proprio (ver docstring do
    modulo). Lista vazia produz cabecalho + aviso EXPLICITO (nunca arquivo
    ausente nem silenciosamente vazio, backlog secao 12 decisao 1)."""
    lines = ["# SQL Dinamico", ""]
    if not records:
        lines.append(_SEM_SITIOS_MENSAGEM)
        lines.append("")
        return "\n".join(lines)

    ordered = sorted(records, key=_markdown_sort_key)
    lines.append("{} sitio(s).".format(len(ordered)))
    lines.append("")
    for record in ordered:
        lines.extend(_render_site_lines(record))

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
