from decimal import Decimal

import httpx
import pytest

from app.fontes.base import DadosLead
from app.fontes.brasilapi import FonteBrasilAPI, extrair_campos
from app.utils.retry import ErroPermanente, ErroTransitorio, LimitadorTaxa
from tests.helpers import fixture_json

CNPJ = "19131243000197"
DADOS = DadosLead(hubspot_contact_id="101", cnpj=CNPJ)


def _fonte(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FonteBrasilAPI(http, limitador=LimitadorTaxa(limite=1000, janela=1.0)), http


def test_extrai_campos_da_resposta_gravada():
    campos = {c.campo: c for c in extrair_campos(fixture_json(f"brasilapi/cnpj_{CNPJ}.json"))}
    assert campos["razao_social"].valor == "OPEN KNOWLEDGE BRASIL"
    assert campos["municipio"].valor == "SAO PAULO"
    assert campos["uf"].valor == "SP"
    assert campos["cep"].valor == "01311902"
    assert campos["endereco"].valor == "AVENIDA PAULISTA, 37"
    assert campos["cnae_principal"].valor == "Atividades de associações de defesa de direitos sociais"
    assert campos["situacao_cadastral"].valor == "ATIVA"
    assert all(c.confianca == Decimal("1.00") for c in campos.values())


def test_nao_extrai_socios_nem_telefones():
    valores = " ".join(c.valor for c in extrair_campos(fixture_json(f"brasilapi/cnpj_{CNPJ}.json")))
    assert "SOCIO" not in valores
    assert "1123851939" not in valores


def test_campos_vazios_sao_omitidos():
    campos = extrair_campos({"razao_social": "X", "nome_fantasia": "", "cep": None, "numero": "1"})
    assert [c.campo for c in campos] == ["razao_social"]


def test_aplicavel_so_com_cnpj():
    fonte, _ = _fonte(lambda r: httpx.Response(200))
    assert fonte.aplicavel(DADOS)
    assert not fonte.aplicavel(DadosLead(hubspot_contact_id="1", dominio="x.com"))


async def test_consulta_usa_url_e_timeout_de_10s():
    pedidos = []

    def handler(request):
        pedidos.append(request)
        return httpx.Response(200, json=fixture_json(f"brasilapi/cnpj_{CNPJ}.json"))

    fonte, http = _fonte(handler)
    async with http:
        campos = await fonte.consultar(DADOS)
    assert pedidos[0].url == f"https://brasilapi.com.br/api/cnpj/v1/{CNPJ}"
    assert pedidos[0].extensions["timeout"]["read"] == 10.0
    assert len(campos) == 9


async def test_cnpj_nao_encontrado_retorna_vazio():
    fonte, http = _fonte(
        lambda r: httpx.Response(404, json=fixture_json("brasilapi/cnpj_nao_encontrado.json"))
    )
    async with http:
        assert await fonte.consultar(DADOS) == []


async def test_sem_cnpj_nao_chama_api():
    fonte, http = _fonte(lambda r: pytest.fail("não deveria chamar"))
    async with http:
        assert await fonte.consultar(DadosLead(hubspot_contact_id="1")) == []


async def test_falha_transitoria_e_repetida_ate_dar_certo():
    respostas = [httpx.Response(503), httpx.Response(429),
                 httpx.Response(200, json=fixture_json(f"brasilapi/cnpj_{CNPJ}.json"))]
    fonte, http = _fonte(lambda r: respostas.pop(0))
    async with http:
        campos = await fonte.consultar(DADOS)
    assert respostas == [] and campos


async def test_timeout_e_repetido_e_depois_propaga():
    chamadas = []

    def handler(request):
        chamadas.append(1)
        raise httpx.ReadTimeout("lento", request=request)

    fonte, http = _fonte(handler)
    async with http:
        with pytest.raises(httpx.ReadTimeout):
            await fonte.consultar(DADOS)
    assert len(chamadas) == 4


async def test_5xx_persistente_vira_erro_transitorio():
    fonte, http = _fonte(lambda r: httpx.Response(500))
    async with http:
        with pytest.raises(ErroTransitorio):
            await fonte.consultar(DADOS)


async def test_400_vira_erro_permanente_sem_retry():
    chamadas = []

    def handler(request):
        chamadas.append(1)
        return httpx.Response(400)

    fonte, http = _fonte(handler)
    async with http:
        with pytest.raises(ErroPermanente):
            await fonte.consultar(DADOS)
    assert len(chamadas) == 1
