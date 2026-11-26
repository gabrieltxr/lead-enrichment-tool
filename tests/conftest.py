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
