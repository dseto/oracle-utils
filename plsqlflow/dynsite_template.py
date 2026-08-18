"""Reconstrucao de template do SQL dinamico com lacunas nomeadas (T-02,
contrato dynsql-dossie).

Modulo PURO: nenhuma linha aqui abre conexao, executa o SQL analisado ou
toca rede -- so texto-fonte ja lido por quem chama (mesmo padrao de
`plsqlflow/dynsite.py` e `plsqlflow/dynsql.py`: dataclass `frozen=True`,
funcoes deterministicas sobre estrutura de dados de entrada).

Diferenca de proposito em relacao a `dynsql.py` (docs/backlog-sql-dinamico-
estatico.md, secao 4.2): aquele modulo resolve -- responde "consigo montar o
SQL 100%, sim ou nao" -- e para (`unresolved_ref`) assim que encontra
qualquer coisa que nao seja literal ou constante de pacote conhecida. Este
modulo DOCUMENTA a estrutura, mesmo parcial: cada atribuicao relevante a
variavel dentro do subprograma envolvente e coletada, EM ORDEM, e remontada
numa sequencia de partes -- literal ou lacuna nomeada (`L0`, `L1`, ...) --
nunca abortando por causa de uma lacuna nao-literal.

Conceitos-chave:

1. **Atribuicoes encadeadas**: `v := 'lit'; v := v || 'mais';` sao DUAS
   atribuicoes que contribuem para o texto final. Quando o primeiro token da
   atribuicao e a propria variavel (auto-referencia, `v := v || ...`), isso
   significa "continue a partir do que ja foi acumulado" -- o token de
   auto-referencia e descartado, o resto append. Quando o primeiro token NAO
   e a variavel, a atribuicao SOBRESCREVE o que veio antes (semantica real
   de `:=`) -- a sequencia acumulada e reiniciada a partir dali.

2. **Trecho condicional**: quando uma atribuicao que contribui para o texto
   final esta dentro de um `IF`/`ELSIF` ainda nao fechado (balanco simples
   de `IF`/`ELSIF`/`END IF`, mesmo espirito de `dynsite._is_in_loop`), cada
   parte que ela produz carrega o texto da condicao em `TemplatePart.
   condicional`. Isso e so um FATO registrado -- nao torna a reconstrucao
   parcial por si so (backlog 4.2: a lista de "limites honestos" que forcam
   `parcial` NAO inclui estar dentro de um IF).

3. **Lacuna**: qualquer token da concatenacao que nao seja um literal de
   string. Um identificador simples (`p_tabela`) e uma expressao inteira
   (`TO_CHAR(v_id)`) sao os dois lacuna -- a diferenca e so o texto
   registrado em `raw_expr` (o token inteiro, sempre). Quando o token tem a
   FORMA de uma chamada de funcao (`<identificador ou nome.qualificado>(...)`
   cobrindo o token inteiro), `via_funcao` recebe o nome da funcao -- backlog
   4.2.1 "quando a cadeia bate numa funcao": a reconstrucao PODE precisar
   entrar na funcao para saber o que ela devolve, e este modulo NUNCA entra
   (isso e T-03). Vale tanto para `v_sql := fn_montar(...)` (unico token,
   RHS inteiro) quanto para `'lit' || DBMS_ASSERT.ENQUOTE_NAME(p) || 'lit2'`
   (um token entre varios) -- em qualquer posicao, a cadeia "bateu numa
   funcao" e precisa registrar, nao adivinhar o que ela retorna.

4. **`reconstrucao: completa` vs `parcial`**: T-02 NAO classifica origem de
   lacuna (isso e T-04) -- entao a presenca de lacunas, por si so, NUNCA
   torna a reconstrucao parcial. `parcial` reflete só os limites estruturais
   do proprio T-02 (backlog 4.2, "Limites honestos da reconstrucao"):
   nenhuma atribuicao encontrada, a atribuicao mais antiga encontrada ja e
   auto-referencia (origem anterior fora do subprograma, invisivel daqui),
   alguma atribuicao que contribui para o resultado final esta dentro de um
   `LOOP` (a repeticao nao e determinavel estaticamente), ou alguma lacuna
   tem `via_funcao` preenchido (a cadeia bateu numa funcao e este modulo nao
   atravessa). Regra de desempate do contrato, sem excecao: na duvida entre
   completa e parcial, e parcial (backlog secao 6).

Reimplementacoes proprias e minimas (nao importa funcao privada de outro
modulo, mesmo padrao de `dynsite.py`): scanner de concatenacao consciente de
string (`_split_concat_with_offsets`, mesmo espirito de `dynsql._split_
concat`, mas rastreando o offset de cada token para computar a LINHA de
origem) e coletor de statement multi-linha consciente de string (`_gather_
statement_lines`, mesmo espirito de `dynsql._gather_statement`). Reusa
`lexical.strip_comments_and_literals` para limpar comentario antes de
procurar `IF`/`LOOP`/inicio de atribuicao -- o texto ORIGINAL (nao limpo) e
usado para extrair o conteudo real de cada atribuicao (literais precisam do
texto exato, nao do texto com literais em branco).

Limitacao documentada (compartilhada com `dynsql._split_concat` e com o
resto do modulo -- deliberadamente nao corrigida aqui, fora de escopo): o
scanner de concatenacao nao rastreia profundidade de parenteses, entao um
`||` DENTRO dos argumentos de uma chamada de funcao (`SUBSTR(a || b, 1, 2)`)
seria split incorretamente. Tambem se assume que uma condicao `IF`/`ELSIF
... THEN` cabe numa unica linha de codigo (mesmo espirito de `dynsite.
_is_in_loop`, que assume `LOOP`/`END LOOP` tokenizavel por linha) -- uma
condicao multi-linha teria seu texto cortado no fim da linha do `IF`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .lexical import strip_comments_and_literals

_SUBPROGRAM_START_RE = re.compile(r"^\s*(PROCEDURE|FUNCTION)\s+\w+", re.IGNORECASE)
_LOOP_TOKEN_RE = re.compile(r"\b(END\s+LOOP|LOOP)\b", re.IGNORECASE)
_IF_TOKEN_RE = re.compile(r"\b(IF|ELSIF|THEN|END\s+IF)\b", re.IGNORECASE)

_LITERAL_RE = re.compile(r"^'((?:[^']|'')*)'$", re.DOTALL)
_FUNC_CALL_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_\$#]*(?:\s*\.\s*[A-Za-z_][A-Za-z0-9_\$#]*)*)\s*\((.*)\)$",
    re.DOTALL,
)

# T-03 (travessia de funcao, backlog 4.2.1): distingue um RETURN-statement
# (`RETURN <expr>;`, dentro do corpo) do RETURN de assinatura de funcao
# (`FUNCTION f(...) RETURN <tipo> IS/AS`) -- ver `_return_statement_start_
# lines` e `_function_header_spans`.
_RETURN_TOKEN_RE = re.compile(r"\bRETURN\b", re.IGNORECASE)
_IS_AS_TOKEN_RE = re.compile(r"\b(IS|AS)\b", re.IGNORECASE)
_FUNCTION_NAME_RE = re.compile(r"\bFUNCTION\s+[A-Za-z_][A-Za-z0-9_\$#]*", re.IGNORECASE)

RECONSTRUCAO_COMPLETA = "completa"
RECONSTRUCAO_PARCIAL = "parcial"

# Prefixo EXATO usado pela razao de "cadeia bate em chamada de funcao" mais
# abaixo (`reconstruct_template`) -- `resolve_gaps` (T-03) usa este mesmo
# prefixo para filtrar essa razao fora de `reconstrucao_motivo` antes de
# recalcular (a razao e recomputada a partir do estado POS-travessia, nunca
# reaproveitada tal como veio de T-02 sozinho).
_FUNCAO_REASON_PREFIX = "cadeia bate em chamada de funcao para "


@dataclass(frozen=True)
class TemplatePart:
    """UM pedaco do template reconstruido, em ordem de aparicao no SQL
    remontado. `kind="literal"` carrega o texto em `text` (`ref` e None);
    `kind="lacuna"` carrega a referencia estavel em `ref` (`text` e None,
    ver `TemplateGap` para o conteudo). `condicional` e o texto da condicao
    IF/ELSIF que produz este trecho, ou None quando o trecho nao esta
    condicionado."""

    kind: str  # "literal" | "lacuna"
    text: Optional[str]
    ref: Optional[str]
    line: int
    condicional: Optional[str]


@dataclass(frozen=True)
class TemplateGap:
    """UMA lacuna nomeada -- nunca muda, sempre com o texto original do
    token preservado em `raw_expr`. `via_funcao` e o nome da funcao quando o
    token inteiro tem forma de chamada de funcao (backlog 4.2.1); None
    caso contrario. `em_loop` e verdadeiro quando a atribuicao que produziu
    esta lacuna esta dentro de um `LOOP ... END LOOP` ainda nao fechado."""

    ref: str
    raw_expr: str
    line: int
    via_funcao: Optional[str]
    em_loop: bool


@dataclass(frozen=True)
class ReconstructedTemplate:
    variavel: str
    parts: List[TemplatePart]
    gaps: List[TemplateGap]
    reconstrucao: str  # "completa" | "parcial"
    reconstrucao_motivo: Optional[str]


@dataclass(frozen=True)
class _RawToken:
    kind: str  # "literal" | "gap"
    text: Optional[str]  # so para "literal" -- ja desescapado
    raw_expr: Optional[str]  # so para "gap" -- token bruto (com wrapper, se houver)
    via_funcao: Optional[str]
    line: int


def _index_by_line(source_lines: Sequence[str]) -> Dict[int, str]:
    return {index + 1: text for index, text in enumerate(source_lines)}


def _subprogram_start_line(clean_by_line: Dict[int, str], line: int) -> int:
    candidate = line
    while candidate > 0:
        text = clean_by_line.get(candidate, "")
        if _SUBPROGRAM_START_RE.match(text):
            return candidate
        candidate -= 1
    return 1


def _loop_state_by_line(
    clean_by_line: Dict[int, str], start_line: int, end_line: int
) -> Dict[int, bool]:
    """Para cada linha em [start_line, end_line], True quando o balanco de
    `LOOP`/`END LOOP` acumulado ate ali (inclusive) esta aberto -- mesmo
    espirito de `dynsite._is_in_loop`, reimplementado aqui em vez de
    importado (funcao privada de outro modulo)."""
    balance = 0
    result: Dict[int, bool] = {}
    for line_no in range(start_line, end_line + 1):
        text = clean_by_line.get(line_no, "")
        for match in _LOOP_TOKEN_RE.finditer(text):
            if match.group(1).upper().startswith("END"):
                balance -= 1
            else:
                balance += 1
        result[line_no] = balance > 0
    return result


def _if_condition_by_line(
    clean_by_line: Dict[int, str], start_line: int, end_line: int
) -> Dict[int, Optional[str]]:
    """Para cada linha em [start_line, end_line], o texto da(s) condicao(oes)
    IF/ELSIF ainda aberta(s) naquele ponto (juntas com " AND " quando
    aninhadas), ou None fora de qualquer IF. Balanco simples por linha via
    `IF`/`ELSIF`/`THEN`/`END IF` (mesmo espirito de `dynsite._is_in_loop`
    para `LOOP`/`END LOOP`) -- assume que `IF`/`ELSIF ... THEN` cabe numa
    unica linha (limitacao documentada no docstring do modulo)."""
    stack: List[str] = []
    result: Dict[int, Optional[str]] = {}
    for line_no in range(start_line, end_line + 1):
        text = clean_by_line.get(line_no, "")
        pending_start: Optional[int] = None
        for match in _IF_TOKEN_RE.finditer(text):
            token = match.group(1).upper()
            if token == "IF":
                pending_start = match.end()
            elif token == "ELSIF":
                if stack:
                    stack.pop()
                pending_start = match.end()
            elif token == "THEN":
                if pending_start is not None:
                    stack.append(text[pending_start : match.start()].strip())
                    pending_start = None
            else:  # "END IF" (qualquer variante de espacamento casada pelo grupo)
                if stack:
                    stack.pop()
        result[line_no] = " AND ".join(stack) if stack else None
    return result


def _gather_statement_lines(by_line: Dict[int, str], start_line: int) -> List[Tuple[int, str]]:
    """Junta linhas do texto ORIGINAL (nao limpo -- literais precisam do
    conteudo real) a partir de `start_line` ate o `;` que fecha o statement,
    fora de string. Reimplementacao propria e minima (mesmo espirito de
    `dynsql._gather_statement`, nao importada por ser funcao privada de
    outro modulo)."""
    if not by_line:
        return []
    parts: List[Tuple[int, str]] = []
    max_line = max(by_line)
    in_string = False
    line_no = start_line
    while line_no <= max_line:
        text = by_line.get(line_no, "")
        parts.append((line_no, text))
        idx = 0
        length = len(text)
        terminated = False
        while idx < length:
            ch = text[idx]
            if in_string:
                if ch == "'":
                    if idx + 1 < length and text[idx + 1] == "'":
                        idx += 1
                    else:
                        in_string = False
            else:
                if ch == "'":
                    in_string = True
                elif ch == ";":
                    terminated = True
                    break
            idx += 1
        if terminated:
            break
        line_no += 1
    return parts


def _join_with_offsets(parts: Sequence[Tuple[int, str]]) -> Tuple[str, List[Tuple[int, int, int]]]:
    segments: List[str] = []
    offset_to_line: List[Tuple[int, int, int]] = []
    pos = 0
    for line_no, text in parts:
        segments.append(text)
        offset_to_line.append((pos, pos + len(text), line_no))
        pos += len(text)
    return "".join(segments), offset_to_line


def _line_at_offset(offset_to_line: Sequence[Tuple[int, int, int]], offset: int) -> int:
    for start, end, line_no in offset_to_line:
        if start <= offset < end:
            return line_no
    if offset_to_line:
        return offset_to_line[-1][2]
    return 1


def _split_concat_with_offsets(expr: str) -> List[Tuple[str, int]]:
    """Mesmo scanner char-a-char consciente de string de `dynsql._split_
    concat`, mas retornando tambem o offset (dentro de `expr`) de onde cada
    token comeca -- usado para localizar a LINHA de origem de cada pedaco
    depois. Reimplementacao propria (funcao privada de outro modulo, nao
    importada)."""
    tokens: List[Tuple[str, int]] = []
    current: List[str] = []
    seg_start = 0
    in_string = False
    idx = 0
    length = len(expr)

    def _flush(raw_chars: List[str], start: int) -> Tuple[str, int]:
        raw = "".join(raw_chars)
        stripped = raw.strip()
        lstrip_len = len(raw) - len(raw.lstrip())
        return stripped, start + lstrip_len

    while idx < length:
        ch = expr[idx]
        if in_string:
            current.append(ch)
            if ch == "'":
                if idx + 1 < length and expr[idx + 1] == "'":
                    current.append(expr[idx + 1])
                    idx += 1
                else:
                    in_string = False
            idx += 1
            continue
        if ch == "'":
            in_string = True
            current.append(ch)
            idx += 1
            continue
        if expr[idx : idx + 2] == "||":
            tokens.append(_flush(current, seg_start))
            current = []
            idx += 2
            seg_start = idx
            continue
        current.append(ch)
        idx += 1
    tokens.append(_flush(current, seg_start))
    return tokens


def _unescape_literal(raw_inner: str) -> str:
    return raw_inner.replace("''", "'")


def _classify_token(token_text: str, line: int) -> _RawToken:
    literal_match = _LITERAL_RE.match(token_text)
    if literal_match:
        return _RawToken(
            kind="literal", text=_unescape_literal(literal_match.group(1)), raw_expr=None,
            via_funcao=None, line=line,
        )
    func_match = _FUNC_CALL_RE.match(token_text)
    via_funcao = func_match.group(1).strip() if func_match else None
    return _RawToken(kind="gap", text=None, raw_expr=token_text, via_funcao=via_funcao, line=line)


def _find_assignment_lines(
    clean_by_line: Dict[int, str], subprogram_start: int, exec_line: int, var_name: str
) -> List[int]:
    assign_re = re.compile(r"^\s*{}\s*:=".format(re.escape(var_name)), re.IGNORECASE)
    return [
        line_no
        for line_no in range(subprogram_start, exec_line)
        if assign_re.match(clean_by_line.get(line_no, ""))
    ]


def reconstruct_template(
    source_lines: Sequence[str], exec_line: int, var_name: str
) -> ReconstructedTemplate:
    """Reconstroi o template do SQL montado em `var_name` ate a linha
    `exec_line` (a linha do sitio de execucao -- `EXECUTE IMMEDIATE`/
    `OPEN ... FOR`/`DBMS_SQL.PARSE`, NAO incluida na varredura de
    atribuicoes). `source_lines` e texto-fonte de UM objeto PL/SQL, indice 0
    = linha 1 (mesma convencao de `dynsql.rows_from_lines` e `dynsite.
    classify_dynamic_site`).

    Puro e deterministico: nenhuma conexao, nenhuma execucao do SQL
    analisado, nenhuma tentativa de atravessar chamada de funcao (isso e
    T-03). Ver docstring do modulo para as regras de atribuicao encadeada,
    condicional, lacuna e do desempate completa/parcial."""
    by_line = _index_by_line(source_lines)
    clean_lines, _literals = strip_comments_and_literals(source_lines)
    clean_by_line = _index_by_line(clean_lines)
    max_line = max(by_line) if by_line else exec_line

    subprogram_start = _subprogram_start_line(clean_by_line, exec_line)
    loop_by_line = _loop_state_by_line(clean_by_line, subprogram_start, max_line)
    if_by_line = _if_condition_by_line(clean_by_line, subprogram_start, max_line)

    assignment_lines = _find_assignment_lines(clean_by_line, subprogram_start, exec_line, var_name)

    items: List[_RawToken] = []
    contributing_lines: List[int] = []
    found_any = False
    first_is_self_ref = False

    for assign_line in assignment_lines:
        stmt_parts = _gather_statement_lines(by_line, assign_line)
        full_text, offset_to_line = _join_with_offsets(stmt_parts)
        colon_idx = full_text.find(":=")
        if colon_idx == -1:
            continue
        rhs_start = colon_idx + 2
        rhs_text = full_text[rhs_start:]
        if rhs_text.endswith(";"):
            rhs_text = rhs_text[:-1]

        raw_tokens = [(text, off) for text, off in _split_concat_with_offsets(rhs_text) if text]
        if not raw_tokens:
            continue

        first_token, _first_off = raw_tokens[0]
        is_self_ref = first_token.upper() == var_name.upper()
        if not found_any:
            found_any = True
            first_is_self_ref = is_self_ref

        effective_tokens = raw_tokens[1:] if is_self_ref else raw_tokens
        if not is_self_ref:
            items = []
            contributing_lines = []
        contributing_lines.append(assign_line)

        for token_text, token_off in effective_tokens:
            abs_offset = rhs_start + token_off
            token_line = _line_at_offset(offset_to_line, abs_offset)
            items.append(_classify_token(token_text, token_line))

    parts: List[TemplatePart] = []
    gaps: List[TemplateGap] = []
    gap_index = 0
    for raw in items:
        condicional = if_by_line.get(raw.line)
        if raw.kind == "literal":
            parts.append(
                TemplatePart(
                    kind="literal", text=raw.text, ref=None, line=raw.line, condicional=condicional
                )
            )
        else:
            ref = "L{}".format(gap_index)
            gap_index += 1
            em_loop = loop_by_line.get(raw.line, False)
            parts.append(
                TemplatePart(
                    kind="lacuna", text=None, ref=ref, line=raw.line, condicional=condicional
                )
            )
            gaps.append(
                TemplateGap(
                    ref=ref, raw_expr=raw.raw_expr, line=raw.line,
                    via_funcao=raw.via_funcao, em_loop=em_loop,
                )
            )

    reasons: List[str] = []
    if not found_any:
        reasons.append(
            "nenhuma atribuicao a {} encontrada no subprograma (linhas {}-{})".format(
                var_name, subprogram_start, exec_line - 1
            )
        )
    if first_is_self_ref:
        reasons.append(
            "primeira atribuicao encontrada a {} ja e auto-referencia -- origem anterior "
            "nao localizada dentro do subprograma".format(var_name)
        )
    loop_lines = sorted({line for line in contributing_lines if loop_by_line.get(line, False)})
    if loop_lines:
        reasons.append(
            "atribuicao dentro de LOOP na(s) linha(s) {} -- repeticao nao e determinavel "
            "estaticamente".format(", ".join(str(l) for l in loop_lines))
        )
    funcao_gaps = [gap for gap in gaps if gap.via_funcao]
    if funcao_gaps:
        detalhe = ", ".join("{} ({})".format(gap.ref, gap.via_funcao) for gap in funcao_gaps)
        reasons.append(
            "cadeia bate em chamada de funcao para {} -- nao atravessada nesta tarefa "
            "(ver T-03)".format(detalhe)
        )

    reconstrucao = RECONSTRUCAO_PARCIAL if reasons else RECONSTRUCAO_COMPLETA
    motivo = "; ".join(reasons) if reasons else None

    return ReconstructedTemplate(
        variavel=var_name, parts=parts, gaps=gaps, reconstrucao=reconstrucao,
        reconstrucao_motivo=motivo,
    )


# ---------------------------------------------------------------------------
# T-03 (contrato dynsql-dossie): travessia de funcao quando a cadeia de
# montagem do SQL bate numa chamada (backlog 4.2.1). Ver docstring do modulo
# para o resto do vocabulario (atribuicao encadeada, condicional, lacuna).
#
# Regra do backlog, citada literalmente (nao reformulada -- ver
# docs/backlog-sql-dinamico-estatico.md secao 4.2.1):
#
#   Atravessa se, e somente se, a funcao tem EXATAMENTE UM RETURN, esse
#   RETURN e reconstruivel pelas MESMAS regras de 4.2, e nao ha uma SEGUNDA
#   travessia encadeada. Qualquer condicao que falhe -> para e registra.
#
# Resultado e sempre BINARIO: atravessa com certeza, ou nao atravessa nunca.
# Nunca existe logica de "escolher qual RETURN usar entre varios" -- ver
# tests/test_dynsite_travessia.py, teste
# `test_nunca_escolhe_entre_multiplos_return_nenhum_texto_vaza` para a prova
# negativa explicita.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FunctionTraversalResult:
    """Resultado de UMA tentativa de travessia (`traverse_function_gap`).

    `traversed=True`: `parts`/`gaps` substituem a lacuna original (ver
    `resolve_gaps`); `motivo_nao_atravessado` e None.
    `traversed=False`: `parts`/`gaps` sao None; `motivo_nao_atravessado`
    SEMPRE preenchido (nunca None silencioso -- regra do contrato)."""

    traversed: bool
    parts: Optional[List[TemplatePart]]
    gaps: Optional[List[TemplateGap]]
    motivo_nao_atravessado: Optional[str]


def _join_lines_padded(parts: Sequence[Tuple[int, str]]) -> Tuple[str, List[Tuple[int, int, int]]]:
    """Como `_join_with_offsets`, mas insere um espaco entre cada linha antes
    de concatenar. Usado SO para a varredura de classificacao de RETURN
    (`_return_statement_start_lines`), que precisa achar limite de palavra
    (`\\bIS\\b`, `\\bRETURN\\b`, `;`) ao longo do corpo INTEIRO da funcao --
    sem o espaco, uma linha terminando em `IS` seguida de `BEGIN` sem
    indentacao colaria ("ISBEGIN"), quebrando o limite de palavra. Onde o
    texto EXATO importa (extracao do literal da expressao do RETURN),
    continua-se usando `_join_with_offsets` sem padding, como o resto do
    modulo ja faz."""
    segments: List[str] = []
    offset_to_line: List[Tuple[int, int, int]] = []
    pos = 0
    for line_no, text in parts:
        padded = text + " "
        segments.append(padded)
        offset_to_line.append((pos, pos + len(padded), line_no))
        pos += len(padded)
    return "".join(segments), offset_to_line


def _skip_ws(text: str, pos: int) -> int:
    length = len(text)
    while pos < length and text[pos].isspace():
        pos += 1
    return pos


def _find_matching_paren(text: str, open_idx: int) -> Optional[int]:
    """`text[open_idx]` deve ser `(`. Devolve o offset do `)` que fecha,
    respeitando aninhamento (ex.: `p_x IN NUMBER(10,2)` dentro da lista de
    parametros) -- scan por profundidade, nao regex nao-guloso (que pararia
    no primeiro `)`, errado quando ha parenteses aninhados antes do
    verdadeiro fechamento). None quando o texto acaba sem fechar."""
    depth = 0
    idx = open_idx
    length = len(text)
    while idx < length:
        ch = text[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return idx
        idx += 1
    return None


def _function_header_spans(joined: str) -> List[Tuple[int, int]]:
    """Spans `[inicio, fim)` do texto LIMPO/PADDED (mesmo `joined` produzido
    por `_join_lines_padded`) que cobrem o CABECALHO de cada `FUNCTION`
    encontrada -- do token `FUNCTION` ate o `IS`/`AS` que o fecha. Usado por
    `_return_statement_start_lines` para EXCLUIR o `RETURN` da assinatura
    (`FUNCTION f(...) RETURN <tipo> IS/AS`) da contagem de RETURN-statement,
    por CASAMENTO ESTRUTURAL -- nao por heuristica de "IS/AS antes do
    primeiro `;` encontrado em qualquer lugar do texto que segue".

    Correcao do defeito achado em revisao cega (T-03): a heuristica antiga
    assumia que nenhum RETURN-statement genuino contem `IS`/`AS` na propria
    expressao antes do seu `;` de fechamento -- falso em PL/SQL (`RETURN v
    IS NOT NULL;`, `RETURN (SELECT c AS x FROM d);`, `RETURN v IS OF
    (tipo);`). Quando um `;` de OUTRO statement mais adiante no texto ficava
    entre o RETURN-statement genuino e o proximo IS/AS, o RETURN genuino era
    descartado como se fosse assinatura -- subcontando RETURNs distintos.

    Esta funcao ancora a busca no formato GRAMATICAL do cabecalho: nome da
    funcao, lista de parametros opcional (consumida por casamento de
    parenteses, nao regex), e so ENTAO o token `RETURN` -- sem NENHUM `;`
    entre esse `RETURN` e o `IS`/`AS` que o segue (e so o nome do tipo entre
    eles). Um RETURN-statement no corpo nunca aparece nessa posicao exata
    (logo apos o nome da funcao/lista de parametros), entao nunca e
    capturado aqui, INDEPENDENTE do que a expressao dele contenha."""
    spans: List[Tuple[int, int]] = []
    for name_match in _FUNCTION_NAME_RE.finditer(joined):
        pos = _skip_ws(joined, name_match.end())
        if pos < len(joined) and joined[pos] == "(":
            close_idx = _find_matching_paren(joined, pos)
            if close_idx is None:
                continue
            pos = close_idx + 1
        pos = _skip_ws(joined, pos)
        return_match = _RETURN_TOKEN_RE.match(joined, pos)
        if return_match is None:
            continue
        after_return = return_match.end()
        semi_idx = joined.find(";", after_return)
        is_as_match = _IS_AS_TOKEN_RE.search(joined, after_return)
        if is_as_match is None:
            continue
        if semi_idx != -1 and semi_idx < is_as_match.start():
            continue
        spans.append((name_match.start(), is_as_match.end()))
    return spans


def _return_statement_start_lines(clean_by_line: Dict[int, str]) -> List[int]:
    """Linhas onde comeca uma ocorrencia de RETURN que e STATEMENT (`RETURN
    <expr>;`), nao a assinatura da funcao (`FUNCTION f(...) RETURN <tipo>
    IS/AS`). Varre o texto LIMPO (comentario/literal em branco, mesmo padrao
    do resto do modulo) do corpo INTEIRO passado -- um RETURN dentro de
    `ELSE RETURN ...;` tambem conta, nao precisa comecar a linha.

    Classificacao: um RETURN cujo offset cai dentro de um span de cabecalho
    (`_function_header_spans`) e a assinatura, nunca conta. TODO outro
    RETURN conta como statement, desde que um `;` seja alcancavel a partir
    dele -- sem olhar `IS`/`AS` nenhum, essa e exatamente a distincao que
    causava a subcontagem (ver docstring de `_function_header_spans`)."""
    parts = sorted(clean_by_line.items())
    joined, offset_to_line = _join_lines_padded(parts)
    header_spans = _function_header_spans(joined)
    starts: List[int] = []
    for match in _RETURN_TOKEN_RE.finditer(joined):
        if any(start <= match.start() < end for start, end in header_spans):
            continue
        tail = joined[match.end():]
        semi_idx = tail.find(";")
        if semi_idx == -1:
            continue
        starts.append(_line_at_offset(offset_to_line, match.start()))
    return starts


def _extract_return_expr(
    by_line: Dict[int, str], start_line: int
) -> Optional[Tuple[str, int, List[Tuple[int, int, int]]]]:
    """A partir da linha onde uma ocorrencia RETURN-statement foi
    identificada, junta o texto RAW do statement (`_gather_statement_lines`,
    mesmo padrao usado por `reconstruct_template` para o RHS de uma
    atribuicao), acha o proprio token `RETURN` dentro dele (pode nao estar
    no inicio da linha -- ex.: `ELSE RETURN x;`) e devolve
    `(expr_text_sem_ponto_e_virgula, offset_absoluto_do_rhs, offset_to_line)`
    -- prontos para o MESMO tokenizador de 4.2 (`_split_concat_with_
    offsets`). None quando o token RETURN nao e localizavel no texto
    juntado (nao deveria acontecer dado que `start_line` ja veio de uma
    ocorrencia RETURN confirmada -- defensivo)."""
    stmt_parts = _gather_statement_lines(by_line, start_line)
    full_text, offset_to_line = _join_with_offsets(stmt_parts)
    return_match = _RETURN_TOKEN_RE.search(full_text)
    if return_match is None:
        return None
    rhs_start = return_match.end()
    rhs_text = full_text[rhs_start:]
    if rhs_text.endswith(";"):
        rhs_text = rhs_text[:-1]
    return rhs_text, rhs_start, offset_to_line


def traverse_function_gap(
    gap: TemplateGap,
    function_source_lines: Sequence[str],
) -> FunctionTraversalResult:
    """Decide UMA lacuna com `via_funcao` preenchido: atravessa (substitui a
    lacuna pelo template reconstruido do que a funcao retorna) ou para e
    registra (backlog 4.2.1). `function_source_lines` e o codigo-fonte da
    FUNCTION apontada por `gap.via_funcao`, JA ISOLADO por quem chama --
    este modulo nao busca no banco nem em outro objeto (integracao futura,
    T-07); indice 0 = linha 1 do CORPO da funcao (mesma convencao do resto
    do modulo).

    Nunca atravessa mais de 1 nivel: se o RETURN encontrado ele mesmo chama
    outra funcao, a lacuna nova resultante carrega `via_funcao` preenchido
    SEM que este modulo tente atravessa-la -- isso e esperado e correto
    (backlog: "sem uma segunda travessia encadeada"), nao um bug."""
    if not gap.via_funcao:
        return FunctionTraversalResult(
            traversed=False, parts=None, gaps=None,
            motivo_nao_atravessado="lacuna nao tem via_funcao preenchido -- nada a atravessar",
        )

    by_line = _index_by_line(function_source_lines)
    if not by_line:
        return FunctionTraversalResult(
            traversed=False, parts=None, gaps=None,
            motivo_nao_atravessado="codigo-fonte da funcao {} vazio".format(gap.via_funcao),
        )
    clean_lines, _literals = strip_comments_and_literals(function_source_lines)
    clean_by_line = _index_by_line(clean_lines)
    max_line = max(by_line)

    return_starts = _return_statement_start_lines(clean_by_line)
    if len(return_starts) == 0:
        return FunctionTraversalResult(
            traversed=False, parts=None, gaps=None,
            motivo_nao_atravessado="funcao sem RETURN encontrado",
        )
    if len(return_starts) > 1:
        return FunctionTraversalResult(
            traversed=False, parts=None, gaps=None,
            motivo_nao_atravessado="{} RETURN distintos".format(len(return_starts)),
        )

    extracted = _extract_return_expr(by_line, return_starts[0])
    if extracted is None:
        return FunctionTraversalResult(
            traversed=False, parts=None, gaps=None,
            motivo_nao_atravessado=(
                "RETURN nao reconstruivel: token RETURN nao localizado no statement"
            ),
        )
    rhs_text, rhs_start, offset_to_line = extracted
    if not rhs_text.strip():
        return FunctionTraversalResult(
            traversed=False, parts=None, gaps=None,
            motivo_nao_atravessado="RETURN nao reconstruivel: expressao vazia",
        )

    raw_tokens = [(text, off) for text, off in _split_concat_with_offsets(rhs_text) if text]
    if not raw_tokens:
        return FunctionTraversalResult(
            traversed=False, parts=None, gaps=None,
            motivo_nao_atravessado="RETURN nao reconstruivel: nenhum token na expressao",
        )

    loop_by_line = _loop_state_by_line(clean_by_line, 1, max_line)
    if_by_line = _if_condition_by_line(clean_by_line, 1, max_line)

    result_parts: List[TemplatePart] = []
    result_gaps: List[TemplateGap] = []
    gap_index = 0
    for token_text, token_off in raw_tokens:
        abs_offset = rhs_start + token_off
        token_line = _line_at_offset(offset_to_line, abs_offset)
        raw_token = _classify_token(token_text, token_line)
        condicional = if_by_line.get(token_line)
        if raw_token.kind == "literal":
            result_parts.append(
                TemplatePart(
                    kind="literal", text=raw_token.text, ref=None,
                    line=token_line, condicional=condicional,
                )
            )
        else:
            # ref filho do gap original -- unico por construcao (o ref pai
            # nunca contem "."), rastreavel ate a lacuna que originou a
            # travessia.
            ref = "{}.{}".format(gap.ref, gap_index)
            gap_index += 1
            em_loop = loop_by_line.get(token_line, False)
            result_parts.append(
                TemplatePart(
                    kind="lacuna", text=None, ref=ref, line=token_line, condicional=condicional,
                )
            )
            result_gaps.append(
                TemplateGap(
                    ref=ref, raw_expr=raw_token.raw_expr, line=token_line,
                    via_funcao=raw_token.via_funcao, em_loop=em_loop,
                )
            )

    return FunctionTraversalResult(
        traversed=True, parts=result_parts, gaps=result_gaps, motivo_nao_atravessado=None,
    )


def _non_funcao_reasons(motivo: Optional[str]) -> List[str]:
    """Extrai de `reconstrucao_motivo` (string ja unida por "; ") as razoes
    que NAO sao a de "cadeia bate em chamada de funcao" -- essas continuam
    valendo depois de `resolve_gaps` (sao fatos estruturais sobre a cadeia
    de atribuicoes do proprio T-02, independentes de travessia). A razao de
    funcao e sempre RECOMPUTADA do zero a partir do estado pos-travessia
    (nunca reaproveitada tal como veio)."""
    if not motivo:
        return []
    return [clause for clause in motivo.split("; ") if not clause.startswith(_FUNCAO_REASON_PREFIX)]


def resolve_gaps(
    template: ReconstructedTemplate,
    function_sources: Dict[str, Sequence[str]],
) -> ReconstructedTemplate:
    """Percorre `template.gaps` (SO os do nivel original -- nunca os
    introduzidos por uma travessia, o que garante no maximo 1 nivel de
    travessia a partir de cada gap original) e, para cada um com
    `via_funcao` preenchido cuja fonte esteja em `function_sources`, tenta
    atravessar via `traverse_function_gap`. Substitui a lacuna pelas partes
    resultantes no lugar certo de `template.parts` quando atravessa; quando
    nao atravessa (fonte ausente ou `traverse_function_gap` recusa), a
    lacuna fica como estava.

    Recalcula `reconstrucao`/`reconstrucao_motivo`: as razoes estruturais de
    T-02 (nenhuma atribuicao encontrada, auto-referencia, loop na propria
    atribuicao do template pai) continuam valendo tal como vieram -- nao
    dependem de travessia. A razao de funcao e refeita do zero: lacuna
    atravessada com sucesso deixa de contar; lacuna nao atravessada
    (fonte ausente, ou `traverse_function_gap` recusou) continua contando,
    agora com o motivo ESPECIFICO (nao generico) de por que nao atravessou;
    lacuna nova introduzida pela travessia que ela mesma tem `via_funcao`
    (encadeamento de 2o nivel, nunca atravessado) tambem conta, com motivo
    proprio citando o limite de 1 nivel; lacuna nova com `em_loop=True`
    (RETURN atravessado que estava dentro de LOOP no corpo da funcao) vira
    razao nova, mesma regra de T-02 para loop."""
    gaps_by_ref = {gap.ref: gap for gap in template.gaps}
    new_parts: List[TemplatePart] = []
    final_gaps: List[TemplateGap] = []
    reasons = _non_funcao_reasons(template.reconstrucao_motivo)

    for part in template.parts:
        if part.kind != "lacuna":
            new_parts.append(part)
            continue
        gap = gaps_by_ref.get(part.ref)
        if gap is None:
            new_parts.append(part)
            continue
        if not gap.via_funcao:
            new_parts.append(part)
            final_gaps.append(gap)
            continue

        source = function_sources.get(gap.via_funcao)
        if source is None:
            new_parts.append(part)
            final_gaps.append(gap)
            reasons.append(
                "lacuna {} ({}) nao atravessada: codigo-fonte da funcao nao "
                "disponivel neste fechamento".format(gap.ref, gap.via_funcao)
            )
            continue

        result = traverse_function_gap(gap, source)
        if not result.traversed:
            new_parts.append(part)
            final_gaps.append(gap)
            reasons.append(
                "lacuna {} ({}) nao atravessada: {}".format(
                    gap.ref, gap.via_funcao, result.motivo_nao_atravessado
                )
            )
            continue

        new_parts.extend(result.parts)
        final_gaps.extend(result.gaps)
        for new_gap in result.gaps:
            if new_gap.via_funcao:
                reasons.append(
                    "lacuna {} (via travessia de {}) aponta para outra funcao ({}) "
                    "-- travessia se limita a 1 nivel a partir da lacuna original "
                    "{}".format(new_gap.ref, gap.via_funcao, new_gap.via_funcao, gap.ref)
                )
            if new_gap.em_loop:
                reasons.append(
                    "lacuna {} (via travessia de {}) esta dentro de LOOP no corpo "
                    "da funcao -- repeticao nao e determinavel estaticamente".format(
                        new_gap.ref, gap.via_funcao
                    )
                )

    reconstrucao = RECONSTRUCAO_PARCIAL if reasons else RECONSTRUCAO_COMPLETA
    motivo = "; ".join(reasons) if reasons else None

    return ReconstructedTemplate(
        variavel=template.variavel,
        parts=new_parts,
        gaps=final_gaps,
        reconstrucao=reconstrucao,
        reconstrucao_motivo=motivo,
    )
