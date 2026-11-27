import pytest
from pydantic import ValidationError

from app.config import Settings

BASE = dict(hubspot_token="t", hubspot_webhook_secret="s", database_url="x")
ATIVA = dict(
    fonte_dominio_ativa=True,
    provedor_dominio_url="https://api.provedor.test/v1/empresas/{dominio}",
    provedor_dominio_api_key="k",
    provedor_dominio_mapa_campos={"setor": "empresa.setor"},
)


def test_desligada_por_padrao_sem_exigir_nada():
    s = Settings(**BASE)
    assert s.fonte_dominio_ativa is False
    assert s.limite_consumo_mensal_provedor == 1000


def test_ativa_completa():
    s = Settings(**BASE, **ATIVA)
    assert s.provedor_dominio_nome == "provedor_dominio"
    assert str(s.provedor_dominio_confianca) == "0.85"


@pytest.mark.parametrize("faltando", ["provedor_dominio_url", "provedor_dominio_api_key",
                                      "provedor_dominio_mapa_campos"])
def test_ativa_exige_configuracao(faltando):
    with pytest.raises(ValidationError, match=faltando.upper()):
        Settings(**BASE, **{k: v for k, v in ATIVA.items() if k != faltando})


def test_url_sem_marcador_de_dominio():
    with pytest.raises(ValidationError, match="dominio"):
        Settings(**BASE, **{**ATIVA, "provedor_dominio_url": "https://api.provedor.test/x"})


def test_mapa_vem_do_ambiente_em_json(monkeypatch):
    monkeypatch.setenv("PROVEDOR_DOMINIO_MAPA_CAMPOS", '{"setor":"a.b"}')
    monkeypatch.setenv("LIMITE_CONSUMO_MENSAL_PROVEDOR", "50")
    s = Settings(**BASE)
    assert s.provedor_dominio_mapa_campos == {"setor": "a.b"}
    assert s.limite_consumo_mensal_provedor == 50
