"""Configuração comum dos testes.

Os valores abaixo são fictícios e existem só para os testes; nenhum token real
deve aparecer aqui.
"""

import os

os.environ.setdefault("HUBSPOT_TOKEN", "token-de-teste")
os.environ.setdefault("HUBSPOT_WEBHOOK_SECRET", "segredo-de-teste")
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg2://invalido@localhost:1/nada"),
)
os.environ["WEBHOOK_URL_PUBLICA"] = ""

import pytest  # noqa: E402
from tenacity import wait_none  # noqa: E402


@pytest.fixture(autouse=True)
def _sem_espera_no_retry(monkeypatch):
    """Retry sem backoff real nos testes (a política continua a mesma)."""
    import app.utils.retry as retry

    monkeypatch.setattr(retry, "wait_exponential_jitter", lambda **_: wait_none())
