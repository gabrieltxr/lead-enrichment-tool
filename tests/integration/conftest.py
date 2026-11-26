"""Fixtures de integração: exigem um PostgreSQL apontado por TEST_DATABASE_URL.

Cada sessão de testes recria o schema rodando as migrações do Alembic, então
o banco de teste não deve ser o mesmo da aplicação.
"""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_session
from app.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.integration


def alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = url
    return cfg


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL não definida; testes de integração ignorados")
    eng = create_engine(TEST_DATABASE_URL)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(alembic_config(TEST_DATABASE_URL), "head")
    yield eng
    eng.dispose()


@pytest.fixture
def sessao(engine: Engine) -> Iterator[Session]:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE enriquecimento_campo, enriquecimento_evento RESTART IDENTITY"))
    with sessionmaker(bind=engine)() as s:
        yield s


@pytest.fixture
def client(engine: Engine, sessao: Session) -> Iterator[TestClient]:
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)

    def _sessao_teste() -> Iterator[Session]:
        with fabrica() as s:
            yield s

    app.dependency_overrides[get_session] = _sessao_teste
    with TestClient(app, base_url="https://teste.local") as c:
        yield c
    app.dependency_overrides.clear()
