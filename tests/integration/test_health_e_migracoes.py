from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from app.db.models import Base
from tests.integration.conftest import TEST_DATABASE_URL, alembic_config


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "banco": "ok"}


def test_migracao_cria_tabelas_e_indice(engine):
    insp = inspect(engine)
    assert {"enriquecimento_evento", "enriquecimento_campo"} <= set(insp.get_table_names())
    indices = {i["name"]: i["column_names"] for i in insp.get_indexes("enriquecimento_evento")}
    assert indices["ix_enriquecimento_evento_status_recebido_em"] == ["status", "recebido_em"]


def test_modelos_batem_com_migracoes(engine):
    with engine.connect() as conn:
        diferencas = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diferencas == []


def test_downgrade_e_upgrade(engine):
    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "base")
    assert "enriquecimento_evento" not in inspect(engine).get_table_names()
    command.upgrade(cfg, "head")
    assert "enriquecimento_evento" in inspect(engine).get_table_names()

