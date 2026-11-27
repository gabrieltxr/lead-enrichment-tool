from decimal import Decimal

import httpx
import pytest

from app.fontes.base import DadosLead
from app.fontes.provedor_dominio import FonteProvedorDominio, extrair_campos, valor_no_caminho
from app.utils.retry import ErroPermanente, ErroTransitorio, LimitadorTaxa
from tests.helpers import fixture_json

RESPOSTA = fixture_json("provedor_dominio/empresa_por_dominio.json")
MAPA = {
    "razao_social": "empresa.nome",
    "setor": "empresa.setor",
    "num_funcionarios": "empresa.funcionarios",
    "municipio": "empresa.localizacao.cidade",
    "linkedin": "empresa.linkedin",
    "tag_principal": "tags.0",
    "inexistente": "empresa.nao.existe",
}
DADOS = DadosLead(hubspot_contact_id="101", dominio="exemplo-empresa.com.br")
URL = "https://api.provedor.test/v1/empresas?dominio={dominio}"


def _fonte(handler, **kwargs):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    opcoes = dict(
        nome="provedor_x", url=URL, api_key="chave-de-teste", mapa_campos=MAPA,
        limite_mensal=10, limitador=LimitadorTaxa(limite=1000, janela=1.0),
    )
    return FonteProvedorDominio(http, **{**opcoes, **kwargs}), http


@pytest.mark.parametrize(
    ("caminho", "esperado"),
    [
        ("empresa.nome", "Exemplo Empresa Ltda"),
        ("empresa.localizacao.uf", "SP"),
        ("tags.1", "b2b"),
        ("tags.9", None),
        ("empresa.nome.x", None),
        ("nada", None),
        ("empresa.linkedin", None),
    ],
)
def test_valor_no_caminho(caminho, esperado):
    assert valor_no_caminho(RESPOSTA, caminho) == esperado


def test_extrai_campos_pelo_mapa():
    campos = {c.campo: c for c in extrair_campos(RESPOSTA, MAPA, Decimal("0.85"))}
    assert campos["razao_social"].valor == "Exemplo Empresa Ltda"
    assert campos["num_funcionarios"].valor == "120"
    assert campos["municipio"].valor == "Campinas"
    assert campos["tag_principal"].valor == "saas"
    assert "linkedin" not in campos and "inexistente" not in campos
    assert {c.confianca for c in campos.values()} == {Decimal("0.85")}


def test_confianca_do_provedor_nunca_passa_do_teto():
    assert extrair_campos(RESPOSTA, MAPA, Decimal("0.85"), "confianca")[0].confianca == Decimal("0.85")
    baixa = {**RESPOSTA, "confianca": 0.5}
    assert extrair_campos(baixa, MAPA, Decimal("0.85"), "confianca")[0].confianca == Decimal("0.50")


@pytest.mark.parametrize("valor", ["alta", None, "NaN", -3])
def test_confianca_invalida_usa_teto_ou_zero(valor):
    confianca = extrair_campos({**RESPOSTA, "confianca": valor}, MAPA, Decimal("0.85"), "confianca")[0].confianca
    assert confianca == (Decimal("0") if valor == -3 else Decimal("0.85"))


def test_valores_estruturados_e_booleanos():
    campos = extrair_campos({"a": {"b": 1}, "c": True, "d": "  "}, {"x": "a", "y": "c", "z": "d"}, Decimal("1"))
    assert [(c.campo, c.valor) for c in campos] == [("y", "true")]


async def test_consulta_envia_autenticacao_e_dominio_codificado():
    vistos = []

    def handler(request):
        vistos.append(request)
        return httpx.Response(200, json=RESPOSTA)

    fonte, http = _fonte(handler, header_auth="X-Api-Key", prefixo_auth="")
    async with http:
        campos = await fonte.consultar(DadosLead("1", dominio="a&b.com"))
    assert vistos[0].headers["X-Api-Key"] == "chave-de-teste"
    assert vistos[0].url.params["dominio"] == "a&b.com"
    assert "a%26b.com" in str(vistos[0].url)
    assert len(campos) == 5


@pytest.mark.parametrize("status", [404, 204])
async def test_dominio_desconhecido_retorna_vazio(status):
    fonte, http = _fonte(lambda r: httpx.Response(status))
    async with http:
        assert await fonte.consultar(DADOS) == []


async def test_corpo_que_nao_e_objeto_retorna_vazio():
    fonte, http = _fonte(lambda r: httpx.Response(200, json=[1, 2]))
    async with http:
        assert await fonte.consultar(DADOS) == []


async def test_erro_transitorio_repete_e_depois_falha():
    chamadas = []

    def handler(request):
        chamadas.append(1)
        return httpx.Response(503)

    fonte, http = _fonte(handler)
    async with http:
        with pytest.raises(ErroTransitorio):
            await fonte.consultar(DADOS)
    assert len(chamadas) == 3


async def test_429_seguido_de_sucesso():
    respostas = iter([httpx.Response(429), httpx.Response(200, json=RESPOSTA)])
    fonte, http = _fonte(lambda r: next(respostas))
    async with http:
        assert len(await fonte.consultar(DADOS)) == 5


async def test_chave_invalida_e_erro_permanente():
    fonte, http = _fonte(lambda r: httpx.Response(401))
    async with http:
        with pytest.raises(ErroPermanente):
            await fonte.consultar(DADOS)


async def test_timeout_e_repetido():
    chamadas = []

    def handler(request):
        chamadas.append(request.extensions.get("timeout"))
        raise httpx.ReadTimeout("lento", request=request)

    fonte, http = _fonte(handler)
    async with http:
        with pytest.raises(httpx.ReadTimeout):
            await fonte.consultar(DADOS)
    assert len(chamadas) == 3
    assert chamadas[0]["read"] == 10.0


async def test_aplicavel_so_com_dominio():
    fonte, http = _fonte(lambda r: httpx.Response(500))
    async with http:
        assert fonte.aplicavel(DADOS)
        assert not fonte.aplicavel(DadosLead("1", cnpj="19131243000197"))
        assert await fonte.consultar(DadosLead("1")) == []
    assert fonte.nome == "provedor_x" and fonte.limite_mensal == 10
