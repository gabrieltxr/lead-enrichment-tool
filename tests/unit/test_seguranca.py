"""Regressões da varredura de segurança (config, assinatura, domínio)."""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.hubspot.assinatura import requisicao_autentica
from app.servicos.normalizacao import dominio_do_email

BASE = dict(hubspot_token="t", hubspot_webhook_secret="s", database_url="x")
PERSONALIZADO = dict(
    fonte_dominio_ativa=True,
    provedor_dominio_preset="personalizado",
    provedor_dominio_api_key="k",
    provedor_dominio_mapa_campos={"setor": "empresa.setor"},
)


@pytest.mark.parametrize("campo", ["hubspot_webhook_secret", "hubspot_token"])
@pytest.mark.parametrize("valor", ["", "   "])
def test_segredo_vazio_e_recusado(campo, valor):
    with pytest.raises(ValidationError, match="não pode ser vazio"):
        Settings(**{**BASE, campo: valor})


@pytest.mark.parametrize("url", [
    "http://api.provedor.test/v1/{dominio}",  # chave sem TLS
    "https://{dominio}/api",  # host controlado pelo lead
    "https://{dominio}.provedor.test/api",
    "https://api.provedor.test@{dominio}/x",
    "ftp://api.provedor.test/{dominio}",
])
def test_url_do_provedor_insegura_e_recusada(url):
    with pytest.raises(ValidationError, match="PROVEDOR_DOMINIO_URL"):
        Settings(**BASE, **PERSONALIZADO, provedor_dominio_url=url)


def test_url_do_provedor_com_dominio_no_caminho_ou_query_e_aceita():
    for url in ("https://api.provedor.test/v1/{dominio}", "https://api.provedor.test/x?d={dominio}"):
        assert Settings(**BASE, **PERSONALIZADO, provedor_dominio_url=url).provedor_dominio_url == url


@pytest.mark.parametrize("cabecalho", [
    {"X-HubSpot-Signature-v3": "assinatura-é-inválida", "X-HubSpot-Request-Timestamp": "1"},
    {"X-HubSpot-Signature": "çççç"},
])
def test_assinatura_fora_de_ascii_e_rejeitada_sem_excecao(cabecalho):
    assert requisicao_autentica(
        segredo="s", metodo="POST", uri="https://x/w", corpo=b"[]", headers=cabecalho,
        janela_segundos=10**12, versoes_aceitas=frozenset({"v1", "v3"}),
    ) is False


@pytest.mark.parametrize("email", [
    "a@10.0.0.1", "a@169.254.169.254", "a@127.0.0.1", "a@empresa..com",
    "a@-empresa.com", "a@empresa-.com", "a@empresa.123", f"a@{'x' * 64}.com",
])
def test_dominio_invalido_nao_vira_consulta(email):
    assert dominio_do_email(email) is None


@pytest.mark.parametrize("email,esperado", [
    ("a@minha-empresa.com.br", "minha-empresa.com.br"),
    ("a@xn--empresa-9za.com", "xn--empresa-9za.com"),
    ("a@empresa.io", "empresa.io"),
])
def test_dominio_valido_continua_aceito(email, esperado):
    assert dominio_do_email(email) == esperado
