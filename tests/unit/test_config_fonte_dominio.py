import pytest
from pydantic import ValidationError

from app.config import Settings
from app.fontes.provedor_dominio import PRESETS

BASE = dict(hubspot_token="t", hubspot_webhook_secret="s", database_url="x")
PERSONALIZADO = dict(
    fonte_dominio_ativa=True,
    provedor_dominio_preset="personalizado",
    provedor_dominio_url="https://api.provedor.test/v1/empresas/{dominio}",
    provedor_dominio_api_key="k",
    provedor_dominio_mapa_campos={"setor": "empresa.setor"},
)


def test_desligada_por_padrao_sem_exigir_nada():
    s = Settings(**BASE)
    assert s.fonte_dominio_ativa is False
    assert s.limite_consumo_mensal_provedor == 1000
    assert s.provedor_dominio_preset == "apollo"


def test_apollo_so_precisa_da_chave():
    s = Settings(**BASE, fonte_dominio_ativa=True, provedor_dominio_api_key="k")
    assert s.provedor_dominio_nome == "apollo"
    assert s.provedor_dominio_url == (
        "https://api.apollo.io/api/v1/organizations/enrich?domain={dominio}"
    )
    assert (s.provedor_dominio_header_auth, s.provedor_dominio_prefixo_auth) == ("x-api-key", "")
    assert s.provedor_dominio_mapa_campos["num_funcionarios"] == "organization.estimated_num_employees"
    assert (s.provedor_dominio_limite_taxa, s.provedor_dominio_janela_taxa_segundos) == (600, 3600.0)
    assert s.mapa_propriedades["nome_empresa"] == "company"


def test_apollo_sem_chave_falha():
    with pytest.raises(ValidationError, match="PROVEDOR_DOMINIO_API_KEY"):
        Settings(**BASE, fonte_dominio_ativa=True)


def test_variavel_explicita_sobrescreve_o_preset():
    s = Settings(
        **BASE, provedor_dominio_nome="apollo_br", provedor_dominio_mapa_campos={"setor": "x"},
        provedor_dominio_limite_taxa=50,
    )
    assert s.provedor_dominio_nome == "apollo_br"
    assert s.provedor_dominio_mapa_campos == {"setor": "x"}
    assert s.provedor_dominio_limite_taxa == 50
    assert s.provedor_dominio_header_auth == "x-api-key"
    assert PRESETS["apollo"]["nome"] == "apollo"  # preset não é alterado


def test_personalizado_completo():
    s = Settings(**BASE, **PERSONALIZADO)
    assert s.provedor_dominio_nome == "provedor_dominio"
    assert s.provedor_dominio_prefixo_auth == "Bearer "
    assert str(s.provedor_dominio_confianca) == "0.85"


@pytest.mark.parametrize("faltando", ["provedor_dominio_url", "provedor_dominio_api_key",
                                      "provedor_dominio_mapa_campos"])
def test_personalizado_exige_configuracao(faltando):
    with pytest.raises(ValidationError, match=faltando.upper()):
        Settings(**BASE, **{k: v for k, v in PERSONALIZADO.items() if k != faltando})


def test_url_sem_marcador_de_dominio():
    with pytest.raises(ValidationError, match="dominio"):
        Settings(**BASE, **{**PERSONALIZADO, "provedor_dominio_url": "https://api.provedor.test/x"})


def test_preset_desconhecido():
    with pytest.raises(ValidationError):
        Settings(**BASE, provedor_dominio_preset="clearbit")


def test_valores_vem_do_ambiente(monkeypatch):
    monkeypatch.setenv("PROVEDOR_DOMINIO_MAPA_CAMPOS", '{"setor":"a.b"}')
    monkeypatch.setenv("LIMITE_CONSUMO_MENSAL_PROVEDOR", "50")
    monkeypatch.setenv("PROVEDOR_DOMINIO_PREFIXO_AUTH", "")
    s = Settings(**BASE, provedor_dominio_preset="personalizado")
    assert s.provedor_dominio_mapa_campos == {"setor": "a.b"}
    assert s.limite_consumo_mensal_provedor == 50
    assert s.provedor_dominio_prefixo_auth == ""
