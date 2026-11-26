import json
from datetime import UTC, datetime

import httpx
import pytest

from app.hubspot.client import ClienteHubSpot
from app.hubspot.schemas import definicoes_propriedades_controle
from app.utils.retry import ErroPermanente, LimitadorTaxa
from tests.helpers import fixture_json


def _cliente(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    limitador = LimitadorTaxa(limite=1000, janela=1.0)
    return ClienteHubSpot(http, "token-de-teste", limitador=limitador), http


async def test_ler_contato_envia_token_e_propriedades():
    pedidos = []

    def handler(request):
        pedidos.append(request)
        return httpx.Response(200, json=fixture_json("hubspot/contato_com_cnpj.json"))

    cliente, http = _cliente(handler)
    async with http:
        props = await cliente.ler_contato("101", ["email", "cnpj", "email"])
    assert props["cnpj"] == "19.131.243/0001-97"
    assert pedidos[0].url.path == "/crm/v3/objects/contacts/101"
    assert pedidos[0].url.params["properties"] == "cnpj,email"
    assert pedidos[0].headers["Authorization"] == "Bearer token-de-teste"


async def test_atualizar_contato_e_empresa_usam_patch():
    pedidos = []

    def handler(request):
        pedidos.append(request)
        return httpx.Response(200, json={"id": "1", "properties": {}})

    cliente, http = _cliente(handler)
    async with http:
        await cliente.atualizar_contato("101", {"city": "SAO PAULO"})
        await cliente.atualizar_empresa("7", {"city": "SAO PAULO"})
        await cliente.ler_empresa("7", ["city"])
    assert [(p.method, p.url.path) for p in pedidos] == [
        ("PATCH", "/crm/v3/objects/contacts/101"),
        ("PATCH", "/crm/v3/objects/companies/7"),
        ("GET", "/crm/v3/objects/companies/7"),
    ]
    assert json.loads(pedidos[0].content) == {"properties": {"city": "SAO PAULO"}}


async def test_criar_nota_associa_ao_contato():
    pedidos = []

    def handler(request):
        pedidos.append(request)
        return httpx.Response(201, json=fixture_json("hubspot/nota_criada.json"))

    cliente, http = _cliente(handler)
    async with http:
        nota_id = await cliente.criar_nota("101", "<p>oi</p>", datetime(2026, 11, 26, tzinfo=UTC))
    corpo = json.loads(pedidos[0].content)
    assert nota_id == "90001"
    assert corpo["properties"] == {"hs_timestamp": "2026-11-26T00:00:00.000Z", "hs_note_body": "<p>oi</p>"}
    assert corpo["associations"][0]["to"] == {"id": "101"}
    assert corpo["associations"][0]["types"][0]["associationTypeId"] == 202


async def test_propriedades_listar_e_criar():
    pedidos = []

    def handler(request):
        pedidos.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=fixture_json("hubspot/propriedades_contato.json"))
        return httpx.Response(201, json={})

    cliente, http = _cliente(handler)
    definicao = definicoes_propriedades_controle("origem_enriquecimento", "data_enriquecimento")[1]
    async with http:
        nomes = await cliente.listar_propriedades_contato()
        await cliente.criar_propriedade_contato(definicao)
    assert {"email", "cnpj", "city"} <= nomes
    assert json.loads(pedidos[1].content)["type"] == "date"


async def test_contato_inexistente_vira_erro_permanente():
    cliente, http = _cliente(lambda r: httpx.Response(404))
    async with http:
        with pytest.raises(ErroPermanente):
            await cliente.ler_contato("999", ["email"])


async def test_429_e_repetido():
    respostas = [httpx.Response(429), httpx.Response(200, json={"properties": {"email": None}})]
    cliente, http = _cliente(lambda r: respostas.pop(0))
    async with http:
        assert await cliente.ler_contato("1", ["email"]) == {"email": None}
