"""Classificacao de origem de lacuna do template reconstruido (T-04,
contrato dynsql-dossie).

Modulo PURO: nenhuma linha aqui abre conexao, consulta PL/Scope,
`ALL_ARGUMENTS`, `ALL_IDENTIFIERS` ou qualquer coisa em banco. Recebe listas
JA RESOLVIDAS de nomes conhecidos (parametros formais, variaveis de pacote,
sitios dinamicos conhecidos) como parametro simples -- quem chama (T-07 no
futuro) monta essas listas a partir de PL/Scope de verdade. Este modulo so
casa texto (`TemplateGap.raw_expr`) contra essas listas e produz um fato
estruturado, nunca um palpite (docs/backlog-sql-dinamico-estatico.md, secao
4.3).

Escolha de assinatura: `classify_gap_origin` importa `TemplateGap` de
`plsqlflow.dynsite_template` (T-02) em vez de redeclarar os mesmos campos
soltos. `TemplateGap` e uma classe PUBLICA (dataclass), nao uma funcao
privada -- o contrato do T-04 explicitamente libera esse import (diferente
de `dynsite_template.py`, que reimplementa scanners de `dynsql.py` por
serem funcoes privadas de outro modulo). O restante da assinatura
(`formal_params`, `package_vars`, `known_site_ids`) segue exatamente o
desenho pedido no contrato.

Escopo de `ORIGEM_COLUNA_DE_TABELA`: a constante existe (o contrato pede a
API publica completa), mas `classify_gap_origin` NUNCA a produz. A
assinatura recebida (gap + parametros formais + variaveis de pacote +
sitios dinamicos conhecidos) nao carrega proveniencia de coluna -- saber
que uma variavel foi populada por `SELECT col INTO v` em algum lugar exige
informacao que `TemplateGap` (T-02) nao registra e que esta tarefa nao
busca (nao consulta banco). Quem tiver essa informacao pronta (T-05/T-07)
monta o `LacunaOrigin` diretamente com `origem=ORIGEM_COLUNA_DE_TABELA`,
sem passar por esta funcao.

DBMS_ASSERT (secao 4.4 do backlog): quando `gap.raw_expr` tem a forma
`DBMS_ASSERT.<FUNCAO>(<argumento>)`, esta funcao reconhece o wrapper pelo
nome (case-insensitive, com ou sem espaco ao redor do `.`), extrai o
dominio provado (tabela da secao 4.4) e classifica a origem do ARGUMENTO
interno -- nao do wrapper. As duas informacoes (`dominio`/`dominio_prova`
de um lado, `origem`/`motivo`/`detalhe` do argumento do outro) sao
INDEPENDENTES: um dominio reconhecido nao exige que o argumento tenha sido
resolvido, e um argumento nao resolvido nao apaga o dominio ja provado
pelo wrapper. Quando o argumento nao e um identificador simples (uma
expressao mais complexa dentro do wrapper), a origem do argumento sai
`NAO_DETERMINADO` com motivo especifico -- o dominio do wrapper continua
preenchido do mesmo jeito.

Fora do caso DBMS_ASSERT, a ordem de resolucao de `raw_expr` (ou do
argumento interno de um wrapper) e: sitio dinamico conhecido
(`OUTRO_DINAMICO`) -> parametro formal (`PARAMETRO_FORMAL`) -> variavel de
pacote (`VARIAVEL_DE_PACOTE`) -> forma de chamada de funcao nao atravessada
(`RETORNO_DE_FUNCAO`, quando `via_funcao` esta preenchido) -> caso
contrario, `NAO_DETERMINADO` com motivo especifico dizendo o que foi
tentado. Nunca emite juizo de seguranca sobre concatenacao crua ou ausencia
de wrapper -- isso e escopo de `plsql-review` (contrato dynsql-dossie,
nao-objetivos).

Sitios de chamada para lacuna PARAMETRO_FORMAL (T-05, backlog 4.3.1): esta
funcao NAO busca quem chama o subprograma que contem a lacuna -- isso e
resolvido pelo motor do `procgraph` (fechamento do mapa) e recebido pronto.
`attach_call_sites`, no fim deste modulo, e a funcao PURA e SEPARADA que
anexa essa informacao (ja resolvida por quem chama) a um `LacunaOrigin` de
origem `PARAMETRO_FORMAL`: a lista de sitios de chamada dentro do fechamento
(`call_sites_no_fechamento`) e o sinalizador de chamador externo
(`chamadores_fora_do_fechamento`), calculado comparando dependentes de
`ALL_DEPENDENCIES` (ja buscados por `extract.fetch_dependentes_batch`,
funcao SEPARADA que abre conexao) contra o conjunto de objetos do
fechamento. `classify_gap_origin` continua sem saber nada disso -- a
composicao e feita por quem chama as duas funcoes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple

from .dynsite_template import TemplateGap

ORIGEM_PARAMETRO_FORMAL = "PARAMETRO_FORMAL"
ORIGEM_VARIAVEL_DE_PACOTE = "VARIAVEL_DE_PACOTE"
ORIGEM_COLUNA_DE_TABELA = "COLUNA_DE_TABELA"
ORIGEM_RETORNO_DE_FUNCAO = "RETORNO_DE_FUNCAO"
ORIGEM_OUTRO_DINAMICO = "OUTRO_DINAMICO"
ORIGEM_NAO_DETERMINADO = "NAO_DETERMINADO"

DOMINIO_NOME_DE_OBJETO = "NOME_DE_OBJETO"
DOMINIO_NOME_DE_SCHEMA = "NOME_DE_SCHEMA"
DOMINIO_OBJETO_EXISTENTE = "OBJETO_EXISTENTE"
DOMINIO_LITERAL_DE_TEXTO = "LITERAL_DE_TEXTO"

# Fonte declarada do sinalizador de chamadores externos (T-05, contrato
# dynsql-dossie, backlog 4.3.1) -- sempre acompanha
# `chamadores_fora_do_fechamento` em `LacunaOrigin.detalhe`, para quem le
# saber exatamente o alcance (e o limite) do sinal: grao objeto, so o que o
# usuario atual enxerga em ALL_DEPENDENCIES, nunca uma enumeracao completa.
CHAMADORES_FORA_FONTE = "ALL_DEPENDENCIES (grao objeto, visibilidade do usuario atual)"

_DBMS_ASSERT_DOMAINS = {
    "ENQUOTE_NAME": DOMINIO_NOME_DE_OBJETO,
    "SIMPLE_SQL_NAME": DOMINIO_NOME_DE_OBJETO,
    "QUALIFIED_SQL_NAME": DOMINIO_NOME_DE_OBJETO,
    "SCHEMA_NAME": DOMINIO_NOME_DE_SCHEMA,
    "SQL_OBJECT_NAME": DOMINIO_OBJETO_EXISTENTE,
    "ENQUOTE_LITERAL": DOMINIO_LITERAL_DE_TEXTO,
}

_DBMS_ASSERT_RE = re.compile(
    r"^\s*DBMS_ASSERT\s*\.\s*([A-Za-z_][A-Za-z0-9_\$#]*)\s*\((.*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_SIMPLE_IDENT_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_\$#]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_\$#]*)*$"
)


@dataclass(frozen=True)
class FormalParam:
    """UM parametro formal do subprograma que contem a lacuna. `tipo` e
    opcional (nem sempre disponivel sem PL/Scope real)."""

    name: str
    tipo: Optional[str] = None


@dataclass(frozen=True)
class PackageVarOrigin:
    """UMA variavel de pacote conhecida, com a lista (ja resolvida por quem
    chama) dos subprogramas que atribuem a ela."""

    nome: str
    subprogramas_que_atribuem: List[str]


@dataclass(frozen=True)
class LacunaOrigin:
    """Origem classificada de UMA lacuna (`TemplateGap`).

    `motivo` e OBRIGATORIO (nao-None e nao-vazio) quando `origem ==
    ORIGEM_NAO_DETERMINADO` -- invariante testada, sem excecao. Nos demais
    casos e opcional (pode carregar contexto extra ou ficar None).

    `dominio`/`dominio_prova` vem de `DBMS_ASSERT` (secao 4.4 do backlog) e
    sao independentes de `origem`/`motivo`/`detalhe`: um wrapper reconhecido
    preenche os dois campos mesmo quando o argumento interno nao foi
    resolvido.

    `detalhe` carrega os campos especificos da origem:
      - `PARAMETRO_FORMAL`: {"nome": str, "tipo": Optional[str],
        "posicao": int}  -- posicao e 1-based, indice do parametro na
        sequencia `formal_params` recebida. Quando passado por
        `attach_call_sites` (T-05), ganha tres campos a mais:
        "call_sites_no_fechamento" (List[str]), "chamadores_fora_do_fechamento"
        (bool) e "chamadores_fora_fonte" (str, sempre `CHAMADORES_FORA_FONTE`).
        Ate `attach_call_sites` rodar, esses tres campos estao AUSENTES (nao
        None) -- `classify_gap_origin` sozinha nunca os produz.
      - `VARIAVEL_DE_PACOTE`: {"nome": str, "subprogramas_que_atribuem":
        List[str]}
      - `RETORNO_DE_FUNCAO`: {"funcao": str} -- so o nome da funcao;
        argumentos/node_ref/travessia sao escopo de T-03/T-05, nao
        disponiveis nesta assinatura.
      - `OUTRO_DINAMICO`: {"site_id": str} -- o item de `known_site_ids`
        que bateu com o texto.
      - `NAO_DETERMINADO`: {} (vazio -- o motivo carrega a explicacao).
    """

    ref: str
    origem: str
    motivo: Optional[str]
    dominio: Optional[str]
    dominio_prova: Optional[str]
    detalhe: Dict[str, object]


@dataclass(frozen=True)
class _DbmsAssertMatch:
    func_label: str
    dominio: Optional[str]
    dominio_prova: Optional[str]
    arg_text: str


@dataclass(frozen=True)
class _OriginFacts:
    origem: str
    motivo: Optional[str]
    detalhe: Dict[str, object]


def _match_dbms_assert(raw_expr: str, line: int) -> Optional[_DbmsAssertMatch]:
    match = _DBMS_ASSERT_RE.match(raw_expr.strip())
    if not match:
        return None
    func_name = match.group(1).upper()
    arg_text = match.group(2).strip()
    func_label = "DBMS_ASSERT.{}".format(func_name)
    dominio = _DBMS_ASSERT_DOMAINS.get(func_name)
    dominio_prova = "{} na linha {}".format(func_label, line) if dominio else None
    return _DbmsAssertMatch(
        func_label=func_label, dominio=dominio, dominio_prova=dominio_prova, arg_text=arg_text
    )


def _find_known_site_id(text: str, known_site_ids: Sequence[str]) -> Optional[str]:
    key = text.upper()
    for site in known_site_ids:
        if site.strip().upper() == key:
            return site
    return None


def _find_formal_param(
    text: str, formal_params: Sequence[FormalParam]
) -> Optional[Tuple[int, FormalParam]]:
    key = text.upper()
    for index, param in enumerate(formal_params, start=1):
        if param.name.strip().upper() == key:
            return index, param
    return None


def _find_package_var(
    text: str, package_vars: Sequence[PackageVarOrigin]
) -> Optional[PackageVarOrigin]:
    key = text.upper()
    for pkg_var in package_vars:
        if pkg_var.nome.strip().upper() == key:
            return pkg_var
    return None


def _classify_text_facts(
    text: str,
    via_funcao: Optional[str],
    formal_params: Sequence[FormalParam],
    package_vars: Sequence[PackageVarOrigin],
    known_site_ids: Sequence[str],
    wrapper_label: Optional[str],
) -> _OriginFacts:
    """Resolve `text` (o `raw_expr` inteiro da lacuna, ou o argumento interno
    de um wrapper `DBMS_ASSERT` quando `wrapper_label` esta preenchido)
    contra as listas conhecidas. Ordem: sitio dinamico -> parametro formal
    -> variavel de pacote -> chamada de funcao nao atravessada -> nao
    determinado. Quando `wrapper_label` esta preenchido e `text` nao e um
    identificador simples, para direto em `NAO_DETERMINADO` (a expressao e
    complexa demais para resolver sem entrar nela -- fora de escopo desta
    tarefa)."""
    stripped = text.strip()

    if wrapper_label is not None and not _SIMPLE_IDENT_RE.match(stripped):
        motivo = (
            "argumento de {} e uma expressao ('{}'), nao um identificador simples -- "
            "a origem do argumento so e resolvida quando ele e um unico identificador "
            "conhecido; o dominio do wrapper fica registrado independente disso"
        ).format(wrapper_label, stripped)
        return _OriginFacts(ORIGEM_NAO_DETERMINADO, motivo, {})

    site_match = _find_known_site_id(stripped, known_site_ids)
    if site_match is not None:
        return _OriginFacts(ORIGEM_OUTRO_DINAMICO, None, {"site_id": site_match})

    param_match = _find_formal_param(stripped, formal_params)
    if param_match is not None:
        posicao, param = param_match
        return _OriginFacts(
            ORIGEM_PARAMETRO_FORMAL,
            None,
            {"nome": param.name, "tipo": param.tipo, "posicao": posicao},
        )

    pkg_match = _find_package_var(stripped, package_vars)
    if pkg_match is not None:
        return _OriginFacts(
            ORIGEM_VARIAVEL_DE_PACOTE,
            None,
            {
                "nome": pkg_match.nome,
                "subprogramas_que_atribuem": list(pkg_match.subprogramas_que_atribuem),
            },
        )

    if via_funcao:
        motivo = (
            "lacuna vem de chamada de funcao ({}) nao atravessada nesta tarefa -- "
            "travessia de RETURN unico e reconstruivel e escopo de T-03"
        ).format(via_funcao)
        return _OriginFacts(ORIGEM_RETORNO_DE_FUNCAO, motivo, {"funcao": via_funcao})

    if wrapper_label is not None:
        motivo = (
            "argumento de {} ('{}') e identificador simples mas nao bate com nenhum "
            "parametro formal, variavel de pacote ou sitio dinamico conhecido "
            "informado a classify_gap_origin"
        ).format(wrapper_label, stripped)
    else:
        motivo = (
            "'{}' nao bate com nenhum parametro formal, variavel de pacote ou sitio "
            "dinamico conhecido informado a classify_gap_origin, e nao tem forma de "
            "chamada de funcao (via_funcao vazio) para registrar como retorno de funcao"
        ).format(stripped)
    return _OriginFacts(ORIGEM_NAO_DETERMINADO, motivo, {})


def classify_gap_origin(
    gap: TemplateGap,
    formal_params: Sequence[FormalParam],
    package_vars: Sequence[PackageVarOrigin],
    known_site_ids: Sequence[str] = (),
) -> LacunaOrigin:
    """Classifica a origem de UMA lacuna (`TemplateGap`, T-02) contra listas
    JA RESOLVIDAS de nomes conhecidos. Puro e deterministico: nenhuma
    consulta a banco, nenhum I/O -- ver docstring do modulo para a ordem de
    resolucao e para o tratamento especial de `DBMS_ASSERT` (secao 4.4 do
    backlog).

    `formal_params`: parametros formais do subprograma que contem a lacuna.
    `package_vars`: variaveis de pacote conhecidas, com quem atribui.
    `known_site_ids`: nomes de variavel que sao alvo de OUTRO sitio dinamico
    ja conhecido -- quando `gap.raw_expr` bate com um destes, a origem sai
    `OUTRO_DINAMICO`.
    """
    assert_match = _match_dbms_assert(gap.raw_expr, gap.line)
    if assert_match is not None:
        facts = _classify_text_facts(
            text=assert_match.arg_text,
            via_funcao=None,
            formal_params=formal_params,
            package_vars=package_vars,
            known_site_ids=known_site_ids,
            wrapper_label=assert_match.func_label,
        )
        return LacunaOrigin(
            ref=gap.ref,
            origem=facts.origem,
            motivo=facts.motivo,
            dominio=assert_match.dominio,
            dominio_prova=assert_match.dominio_prova,
            detalhe=facts.detalhe,
        )

    facts = _classify_text_facts(
        text=gap.raw_expr,
        via_funcao=gap.via_funcao,
        formal_params=formal_params,
        package_vars=package_vars,
        known_site_ids=known_site_ids,
        wrapper_label=None,
    )
    return LacunaOrigin(
        ref=gap.ref,
        origem=facts.origem,
        motivo=facts.motivo,
        dominio=None,
        dominio_prova=None,
        detalhe=facts.detalhe,
    )


def attach_call_sites(
    origin: LacunaOrigin,
    call_sites_no_fechamento: Sequence[str],
    dependentes_objeto: Sequence[str],
    fechamento_objetos: Sequence[str],
) -> LacunaOrigin:
    """Anexa a `origin` (uma `LacunaOrigin` ja classificada por
    `classify_gap_origin`) os fatos de sitio de chamada do backlog 4.3.1
    (T-05, contrato dynsql-dossie). Funcao PURA: nenhuma consulta a banco --
    recebe tudo ja resolvido por quem chama.

    So se aplica quando `origin.origem == ORIGEM_PARAMETRO_FORMAL`; para
    qualquer outra origem devolve `origin` sem alteracao (os tres campos
    ficam AUSENTES de `detalhe`, nao `None` -- a lacuna nao veio de
    parametro, entao "quem chama o subprograma" nao e informacao que se
    aplica a ela).

    `call_sites_no_fechamento`: sitios de chamada do subprograma que contem
    a lacuna, JA RESTRITOS ao fechamento do mapa (o motor do `procgraph`
    resolve isso -- esta funcao so os anexa, nao os descobre). Vai direto
    para `detalhe["call_sites_no_fechamento"]`, sem filtragem adicional --
    nome do campo carrega o escopo (backlog 4.3.1: "`call_sites_no_fechamento`
    nao da para ler errado").

    `dependentes_objeto`: cada item e uma string `"OWNER.OBJECT_NAME"` --
    tipicamente montada a partir de `extract.fetch_dependentes_batch`
    (`"{row.owner}.{row.name}"` para cada `DependentesBatchRow`), o
    resultado (em grao OBJETO) de quem depende do(s) objeto(s) que contem o
    subprograma da lacuna.

    `fechamento_objetos`: cada item e `"OWNER.OBJECT_NAME"` de um objeto que
    JA FAZ PARTE do fechamento do mapa (dado pronto do motor do `procgraph`,
    T-07 na integracao real -- aqui e so parametro simples).

    `chamadores_fora_do_fechamento` sai `True` quando existe pelo menos um
    item de `dependentes_objeto` que NAO esta em `fechamento_objetos`
    (comparacao por texto, case-insensitive, espacos nas pontas ignorados).
    Sai `False` quando todo dependente encontrado ja esta dentro do
    fechamento -- inclusive quando `dependentes_objeto` esta vazio (nenhum
    dependente visto por `ALL_DEPENDENCIES` fora do fechamento, entao nao ha
    motivo para acender o sinalizador). `chamadores_fora_fonte` e sempre a
    constante `CHAMADORES_FORA_FONTE`, declarando a fonte e o alcance do
    sinal (nunca uma promessa de completude).

    O calculo do sinalizador e independente da resolucao da lacuna dentro do
    fechamento: mesmo com `chamadores_fora_do_fechamento=True`,
    `call_sites_no_fechamento` continua sendo so os sitios de dentro -- um
    chamador externo nunca entra nem some da lista (backlog 4.3.1, decisao
    3: os dois fatos convivem no mesmo registro sem um apagar o outro)."""
    if origin.origem != ORIGEM_PARAMETRO_FORMAL:
        return origin

    fechamento_normalizado = {item.strip().upper() for item in fechamento_objetos}
    chamadores_fora = any(
        item.strip().upper() not in fechamento_normalizado for item in dependentes_objeto
    )

    detalhe = dict(origin.detalhe)
    detalhe["call_sites_no_fechamento"] = list(call_sites_no_fechamento)
    detalhe["chamadores_fora_do_fechamento"] = chamadores_fora
    detalhe["chamadores_fora_fonte"] = CHAMADORES_FORA_FONTE
    return replace(origin, detalhe=detalhe)
