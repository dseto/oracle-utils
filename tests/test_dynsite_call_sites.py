"""Testes de `plsqlflow.extract.fetch_dependentes_batch` /
`plsqlflow.dynsite_origin.attach_call_sites` (T-05, contrato dynsql-dossie).

T-05 e a metade final da lacuna `PARAMETRO_FORMAL` (T-04, `dynsite_origin`):
para essa origem, o mapa lista os sitios de chamada do subprograma **dentro
do fechamento** (`call_sites_no_fechamento` -- backlog 4.3.1, o nome carrega
o escopo) e sinaliza chamador fora dele via `ALL_DEPENDENCIES`, em grao
objeto (`sql/flow/dependentes_batch.sql`, inverso de `deps_direct.sql`).

Duas responsabilidades SEPARADAS, cada uma com teste proprio nesta suite:
  (a) `fetch_dependentes_batch` -- abre conexao (fake, aqui), busca quem
      depende de um objeto. Mesmo padrao de fake connection/cursor de
      tests/test_depgraph_batch_extract.py.
  (b) `attach_call_sites` -- PURA, sem conexao, recebe listas de string ja
      resolvidas (call sites do fechamento, dependentes em grao objeto,
      objetos do fechamento) e decide o sinalizador. E o que se testa contra
      o cenario dos "dois lados" (caso 3, o mais importante desta tarefa).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from plsqlflow import db, extract, queries
from plsqlflow.dynsite_origin import (
    CHAMADORES_FORA_FONTE,
    ORIGEM_PARAMETRO_FORMAL,
    ORIGEM_VARIAVEL_DE_PACOTE,
    LacunaOrigin,
    attach_call_sites,
)

REPO = Path(__file__).resolve().parent.parent
SQL_FLOW_DIR = REPO / "sql" / "flow"
SQL_FILE = "dependentes_batch.sql"


# --------------------------------------------------------------- fakes
#
# Copia local do fake connection/cursor de tests/test_depgraph_batch_extract.py
# (mesmo padrao ja em uso no repo para testar fetchers de extract.py sem
# abrir conexao real).


class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.description = None
        self.executed = None
        self.closed = False

    def execute(self, sql, binds=None):
        self.executed = (sql, binds)
        columns = list(self._rows[0].keys()) if self._rows else []
        self.description = [(col.upper(),) for col in columns]
        self._tuples = [tuple(row[col] for col in columns) for row in self._rows]

    def fetchall(self):
        return self._tuples

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = FakeCursor(self._rows)
        return self.last_cursor


DEPENDENTES_BATCH_ROWS = [
    {
        "OWNER": "APP",
        "NAME": "PKG_BATCH",
        "TYPE": "PACKAGE BODY",
        "REFERENCED_NAME": "PKG_CORE",
        "DEPENDENCY_TYPE": "HARD",
    },
    {
        "OWNER": "APP",
        "NAME": "RPT_ADHOC",
        "TYPE": "PACKAGE BODY",
        "REFERENCED_NAME": "PKG_CORE",
        "DEPENDENCY_TYPE": "HARD",
    },
]


# ===========================================================================
# (1) fetch_dependentes_batch contra fake connection -- teste de extracao
# ===========================================================================


def test_fetch_dependentes_batch_binds_and_maps_dataclass():
    conn = FakeConnection(DEPENDENTES_BATCH_ROWS)
    rows = extract.fetch_dependentes_batch(conn, "app", "PKG_CORE")

    assert len(rows) == 2
    assert all(isinstance(r, extract.DependentesBatchRow) for r in rows)
    assert rows[0].owner == "APP"
    assert rows[0].name == "PKG_BATCH"
    assert rows[0].referenced_name == "PKG_CORE"
    assert rows[1].name == "RPT_ADHOC"

    sql, binds = conn.last_cursor.executed
    assert binds == {"owner": "app", "object_list": "PKG_CORE"}
    # db.run_query tira o ";" final (convencao SQLcl) antes de executar.
    assert sql == queries.QUERY_TEXT[SQL_FILE].rstrip().rstrip(";")


def test_fetch_dependentes_batch_object_list_optional():
    conn = FakeConnection([])
    extract.fetch_dependentes_batch(conn, "APP", None)
    _, binds = conn.last_cursor.executed
    assert binds == {"owner": "APP", "object_list": None}


def test_fetch_dependentes_batch_run_query_only_select_guard():
    conn = FakeConnection([])
    db.run_query(conn, queries.QUERY_TEXT[SQL_FILE], {"owner": "APP", "object_list": None})
    assert conn.last_cursor is not None
    assert conn.last_cursor.closed is True


# ===========================================================================
# helper: LacunaOrigin PARAMETRO_FORMAL minimo, como classify_gap_origin (T-04)
# produziria para uma lacuna resolvida a um parametro formal.
# ===========================================================================


def _origem_parametro_formal(nome="P_FILTRO", posicao=1) -> LacunaOrigin:
    return LacunaOrigin(
        ref="L0",
        origem=ORIGEM_PARAMETRO_FORMAL,
        motivo=None,
        dominio=None,
        dominio_prova=None,
        detalhe={"nome": nome, "tipo": "VARCHAR2", "posicao": posicao},
    )


# ===========================================================================
# (2) call_sites_no_fechamento preenchido corretamente, nome exato do campo
# ===========================================================================


def test_attach_call_sites_preenche_call_sites_no_fechamento_com_nome_exato():
    origin = _origem_parametro_formal()

    resultado = attach_call_sites(
        origin,
        call_sites_no_fechamento=["APP.PKG_BATCH.SP_RODAR#118"],
        dependentes_objeto=["APP.PKG_BATCH"],
        fechamento_objetos=["APP.PKG_CORE", "APP.PKG_BATCH"],
    )

    # nome do campo tem que ser EXATAMENTE este -- backlog 4.3.1: "call_sites"
    # sozinho seria lido como "os sitios de chamada", sem o escopo.
    assert "call_sites_no_fechamento" in resultado.detalhe
    assert resultado.detalhe["call_sites_no_fechamento"] == ["APP.PKG_BATCH.SP_RODAR#118"]
    assert "call_sites" not in {
        k for k in resultado.detalhe if k != "call_sites_no_fechamento"
    }
    assert resultado.detalhe["chamadores_fora_fonte"] == CHAMADORES_FORA_FONTE


# ===========================================================================
# (3) O TESTE DOS DOIS LADOS -- o mais importante desta tarefa.
#
# Cenario: dentro do fechamento, o UNICO call site (APP.PKG_BATCH.SP_RODAR)
# passa um LITERAL para o parametro que origina a lacuna -- este fato (a
# lacuna esta resolvida NESTE processo) nao pode mudar so porque existe
# outro objeto, fora do fechamento (APP.RPT_ADHOC), que tambem chama o
# mesmo subprograma passando uma VARIAVEL. Prova exigida (spec.md, criterio
# de T-05): call_sites_no_fechamento sai SO com o de dentro (a resolucao por
# literal continua de pe, intocada) E chamadores_fora_do_fechamento sai
# True NO MESMO registro -- nenhum dos dois fatos apaga o outro.
# ===========================================================================


def test_dois_lados_literal_dentro_do_fechamento_permanece_e_sinalizador_externo_liga():
    origin = _origem_parametro_formal(nome="P_STATUS", posicao=1)

    # ALL_DEPENDENCIES (via fetch_dependentes_batch, ja materializado como
    # lista de "OWNER.NAME"): dois objetos dependem de APP.PKG_CORE (o
    # objeto que contem o subprograma da lacuna) -- PKG_BATCH (que passa
    # literal no unico call site do fechamento) e RPT_ADHOC (fora do
    # fechamento do mapa, passa variavel).
    dependentes_objeto = ["APP.PKG_BATCH", "APP.RPT_ADHOC"]

    # O fechamento do mapa (dado pronto, vindo do motor do procgraph) so
    # inclui PKG_CORE e PKG_BATCH -- RPT_ADHOC nunca foi alcancado a partir
    # da raiz do processo mapeado.
    fechamento_objetos = ["APP.PKG_CORE", "APP.PKG_BATCH"]

    # Sitios de chamada do fechamento (ja resolvidos por quem chama, T-07 no
    # futuro): so o de PKG_BATCH -- o unico call site dentro do fechamento,
    # e ele passa literal 'ATIVO' para P_STATUS (fato estabelecido por quem
    # monta esta lista; attach_call_sites nao decide isso, so preserva).
    call_sites_no_fechamento = ["APP.PKG_BATCH.SP_RODAR#118"]

    resultado = attach_call_sites(
        origin,
        call_sites_no_fechamento=call_sites_no_fechamento,
        dependentes_objeto=dependentes_objeto,
        fechamento_objetos=fechamento_objetos,
    )

    # Lado 1: a lista de sitios de chamada continua SO com o de dentro do
    # fechamento -- o chamador externo (RPT_ADHOC) nunca entra na lista, e o
    # que ja estava resolvido (literal, dentro do fechamento) nao some.
    assert resultado.detalhe["call_sites_no_fechamento"] == ["APP.PKG_BATCH.SP_RODAR#118"]
    assert "APP.RPT_ADHOC" not in " ".join(resultado.detalhe["call_sites_no_fechamento"])

    # Lado 2: o sinalizador liga, no MESMO registro, porque RPT_ADHOC
    # (dependente real de PKG_CORE) esta fora do fechamento.
    assert resultado.detalhe["chamadores_fora_do_fechamento"] is True
    assert resultado.detalhe["chamadores_fora_fonte"] == CHAMADORES_FORA_FONTE

    # Os dois fatos coexistem no mesmo objeto -- nao sao dois registros
    # concorrentes nem um substitui o outro.
    assert resultado.origem == ORIGEM_PARAMETRO_FORMAL


# ===========================================================================
# (4) Fechamento completo (nenhum dependente fora) -> sinalizador False
# ===========================================================================


def test_fechamento_completo_sem_dependente_fora_sinalizador_false():
    origin = _origem_parametro_formal()

    resultado = attach_call_sites(
        origin,
        call_sites_no_fechamento=["APP.PKG_BATCH.SP_RODAR#118"],
        # Todos os dependentes reais de PKG_CORE ja estao dentro do
        # fechamento -- nenhum de fora.
        dependentes_objeto=["APP.PKG_BATCH"],
        fechamento_objetos=["APP.PKG_CORE", "APP.PKG_BATCH"],
    )

    assert resultado.detalhe["chamadores_fora_do_fechamento"] is False
    # Prova que o sinalizador nao e True por padrao/bug: mesmo dependente
    # vazio (nenhum dependente visto em ALL_DEPENDENCIES) tem que sair False.
    resultado_vazio = attach_call_sites(
        origin,
        call_sites_no_fechamento=[],
        dependentes_objeto=[],
        fechamento_objetos=["APP.PKG_CORE"],
    )
    assert resultado_vazio.detalhe["chamadores_fora_do_fechamento"] is False


# ===========================================================================
# (5) Origem que NAO e PARAMETRO_FORMAL -- campos de call_sites nao se aplicam
# ===========================================================================


def test_origem_nao_parametro_formal_nao_ganha_campos_de_call_sites():
    origin = LacunaOrigin(
        ref="L1",
        origem=ORIGEM_VARIAVEL_DE_PACOTE,
        motivo=None,
        dominio=None,
        dominio_prova=None,
        detalhe={"nome": "G_FILTRO", "subprogramas_que_atribuem": ["SP_INIT"]},
    )

    resultado = attach_call_sites(
        origin,
        call_sites_no_fechamento=["APP.PKG_BATCH.SP_RODAR#118"],
        dependentes_objeto=["APP.RPT_ADHOC"],
        fechamento_objetos=["APP.PKG_CORE"],
    )

    # Escolha documentada: os campos ficam AUSENTES de detalhe (nao None) --
    # a lacuna nao veio de parametro, entao a nocao de "quem chama o
    # subprograma" simplesmente nao se aplica a ela.
    assert "call_sites_no_fechamento" not in resultado.detalhe
    assert "chamadores_fora_do_fechamento" not in resultado.detalhe
    assert "chamadores_fora_fonte" not in resultado.detalhe
    # detalhe original preservado intacto.
    assert resultado.detalhe == {"nome": "G_FILTRO", "subprogramas_que_atribuem": ["SP_INIT"]}
    assert resultado is origin or resultado == origin


# ===========================================================================
# (6) sql/flow/dependentes_batch.sql existe, registrado, sintaticamente coerente
# ===========================================================================


def test_dependentes_batch_sql_existe_no_disco():
    assert (SQL_FLOW_DIR / SQL_FILE).exists()


def test_dependentes_batch_sql_registrado_no_registro_central():
    assert SQL_FILE in queries.QUERY_NAMES
    on_disk = (SQL_FLOW_DIR / SQL_FILE).read_text(encoding="utf-8")
    assert queries.QUERY_TEXT[SQL_FILE] == on_disk


def test_dependentes_batch_sql_e_inverso_de_deps_direct_grao_objeto():
    text = (SQL_FLOW_DIR / SQL_FILE).read_text(encoding="utf-8").lower()
    assert "all_dependencies" in text
    # Inverso de deps_direct(_batch).sql: filtra por referenced_owner/
    # referenced_name (o alvo), devolve owner/name do dependente.
    assert "d.referenced_owner" in text
    assert "d.referenced_name" in text
    assert ":owner" in text
    assert ":object_list" in text


def test_dependentes_batch_sql_documenta_grao_objeto_e_sinalizador():
    text = (SQL_FLOW_DIR / SQL_FILE).read_text(encoding="utf-8").lower()
    assert "grao objeto" in text or "grão objeto" in text
    assert "sinalizador" in text


def test_dependentes_batch_sql_somente_select():
    text = (SQL_FLOW_DIR / SQL_FILE).read_text(encoding="utf-8")
    lines = [ln.split("--", 1)[0].strip() for ln in text.splitlines()]
    code = " ".join(ln for ln in lines if ln)
    upper = code.upper()
    assert upper.strip().startswith("SELECT")
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "CREATE ", "ALTER ", "DROP ", "TRUNCATE "):
        assert forbidden not in upper
