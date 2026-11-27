"""Worker com a fonte por domínio: limite mensal, prioridade por confiança e
registro de cada consulta (base das métricas)."""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.models import EnriquecimentoConsulta
from app.fontes.brasilapi import FonteBrasilAPI
from app.fontes.provedor_dominio import FonteProvedorDominio
from app.servicos.consumo import consumo_do_mes, reservar
from app.servicos.worker import Worker, montar_fontes
from app.utils.retry import LimitadorTaxa
from tests.helpers import fixture_json
from tests.integration.test_worker import (
    BrasilAPIFalsa,
    HubSpotFalso,
    _campos,
    _contato,
    _evento,
    _liberar,
    _novo_evento,
)

MAPA_CAMPOS = {
    "razao_social": "empresa.nome",
    "setor": "empresa.setor",
    "num_funcionarios": "empresa.funcionarios",
}
MAPA_PROPRIEDADES = {
    "razao_social": "company", "municipio": "city", "uf": "state",
    "setor": "industry", "num_funcionarios": "numberofemployees",
}


class ProvedorFalso:
    def __init__(self):
        self.status = 200
        self.chamadas = 0

    def __call__(self, request):
        self.chamadas += 1
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, json=fixture_json("provedor_dominio/empresa_por_dominio.json"))


@pytest.fixture
def settings():
    return Settings(
        hubspot_token="t", hubspot_webhook_secret="s", database_url="x", max_tentativas=3,
        mapa_propriedades=MAPA_PROPRIEDADES,
    )


@pytest.fixture
def provedor():
    return ProvedorFalso()


@pytest.fixture
async def montar(engine, settings, provedor):
    brasilapi = BrasilAPIFalsa()

    def roteador(request):
        return (brasilapi if request.url.host == "brasilapi.com.br" else provedor)(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(roteador)) as http:
        def _montar(hubspot, limite=10):
            fontes = [
                FonteBrasilAPI(http, limitador=LimitadorTaxa(1000, 1.0)),
                FonteProvedorDominio(
                    http, nome="provedor_x", url="https://api.provedor.test/{dominio}",
                    api_key="k", mapa_campos=MAPA_CAMPOS, limite_mensal=limite,
                    limitador=LimitadorTaxa(1000, 1.0),
                ),
            ]
            return Worker(sessionmaker(bind=engine), hubspot, fontes, settings)

        yield _montar


def _consultas(sessao, evento_id):
    sessao.expire_all()
    stmt = select(EnriquecimentoConsulta).where(EnriquecimentoConsulta.evento_id == evento_id)
    return {c.fonte: c for c in sessao.scalars(stmt)}


async def test_fonte_dominio_complementa_e_cnpj_tem_prioridade(montar, sessao, provedor):
    hubspot = HubSpotFalso({"101": _contato()}, propriedades=["numberofemployees"])
    evento_id = _novo_evento(sessao)

    await montar(hubspot).executar_ciclo()

    contato = hubspot.contatos["101"]
    assert contato["company"] == "OPEN KNOWLEDGE BRASIL"  # CNPJ (1.00) vence domínio (0.85)
    assert contato["numberofemployees"] == "120"  # só o provedor tem
    assert contato["origem_enriquecimento"] == "brasilapi,provedor_x"
    campos = _campos(sessao, evento_id)
    assert campos["num_funcionarios"].confianca == Decimal("0.85")
    consultas = _consultas(sessao, evento_id)
    assert consultas["brasilapi"].resultado == consultas["provedor_x"].resultado == "com_dados"
    assert consumo_do_mes(sessao) == {"provedor_x": 1}
    assert provedor.chamadas == 1


async def test_limite_atingido_pula_a_fonte_sem_parar(montar, sessao, provedor):
    reservar(sessao, "provedor_x", 2)
    reservar(sessao, "provedor_x", 2)
    sessao.commit()
    hubspot = HubSpotFalso({"101": _contato()})
    evento_id = _novo_evento(sessao)

    await montar(hubspot, limite=2).executar_ciclo()

    assert provedor.chamadas == 0
    assert _evento(sessao, evento_id).status == "concluido"
    assert hubspot.contatos["101"]["company"] == "OPEN KNOWLEDGE BRASIL"
    consulta = _consultas(sessao, evento_id)["provedor_x"]
    assert consulta.resultado == "limite_atingido" and "limite mensal de 2" in consulta.motivo
    assert consumo_do_mes(sessao) == {"provedor_x": 2}


async def test_dominio_generico_nao_consulta_nem_gasta(montar, sessao, provedor):
    hubspot = HubSpotFalso({"101": _contato(email="ana@gmail.com")})
    evento_id = _novo_evento(sessao)
    await montar(hubspot).executar_ciclo()
    assert provedor.chamadas == 0 and consumo_do_mes(sessao) == {}
    assert set(_consultas(sessao, evento_id)) == {"brasilapi"}


async def test_falha_do_provedor_registra_erro_e_reprocessa_sem_duplicar(montar, sessao, provedor):
    provedor.status = 503
    hubspot = HubSpotFalso({"101": _contato()})
    evento_id = _novo_evento(sessao)
    worker = montar(hubspot)

    await worker.executar_ciclo()
    consulta = _consultas(sessao, evento_id)["provedor_x"]
    assert (consulta.resultado, consulta.motivo) == ("erro", "ErroTransitorio")
    assert _evento(sessao, evento_id).status == "pendente"

    provedor.status = 404
    _liberar(sessao, evento_id)
    await worker.executar_ciclo()
    consultas = _consultas(sessao, evento_id)
    assert len(consultas) == 2 and consultas["provedor_x"].resultado == "sem_dados"
    assert consultas["provedor_x"].motivo is None
    assert _evento(sessao, evento_id).status == "concluido"
    assert consumo_do_mes(sessao) == {"provedor_x": 2}  # cada tentativa conta


async def test_mesma_propriedade_por_fontes_diferentes_nao_vira_campo_manual(engine, sessao):
    """BrasilAPI grava company (razao_social); na reexecução, nome_empresa da
    Apollo vê o valor do próprio serviço, não um valor manual."""
    from app.fontes.provedor_dominio import PRESETS

    apollo = fixture_json("provedor_dominio/apollo_organizacao.json")
    brasilapi = BrasilAPIFalsa()

    def roteador(request):
        return brasilapi(request) if request.url.host == "brasilapi.com.br" else httpx.Response(200, json=apollo)

    settings = Settings(hubspot_token="t", hubspot_webhook_secret="s", database_url="x")
    hubspot = HubSpotFalso({"101": _contato()}, propriedades=["website", "country"])
    evento_id = _novo_evento(sessao)
    async with httpx.AsyncClient(transport=httpx.MockTransport(roteador)) as http:
        fontes = [
            FonteBrasilAPI(http, limitador=LimitadorTaxa(1000, 1.0)),
            FonteProvedorDominio(
                http, nome="apollo", url=PRESETS["apollo"]["url"], api_key="k",
                mapa_campos=PRESETS["apollo"]["mapa_campos"], limite_mensal=10,
                limitador=LimitadorTaxa(1000, 1.0),
            ),
        ]
        worker = Worker(sessionmaker(bind=engine), hubspot, fontes, settings)
        await worker.executar_ciclo()
        _liberar(sessao, evento_id, status="pendente")
        await worker.executar_ciclo()

    contato = hubspot.contatos["101"]
    assert contato["company"] == "OPEN KNOWLEDGE BRASIL"
    assert contato["website"] == "http://www.apollo.io"
    assert contato["country"] == "United States"
    assert contato["origem_enriquecimento"] == "apollo,brasilapi"
    campos = _campos(sessao, evento_id)
    assert campos["nome_empresa"].motivo == "confianca_nao_maior"
    assert not any(c.motivo == "campo_manual" for c in campos.values())
    assert "preservados" not in hubspot.notas[0][1]


def test_montar_fontes_respeita_a_flag():
    base = dict(hubspot_token="t", hubspot_webhook_secret="s", database_url="x")
    http = httpx.AsyncClient()
    assert [f.nome for f in montar_fontes(http, Settings(**base))] == ["brasilapi"]
    ativa = Settings(
        **base, fonte_dominio_ativa=True, provedor_dominio_api_key="k",
        limite_consumo_mensal_provedor=42,
    )
    fontes = montar_fontes(http, ativa)
    assert [f.nome for f in fontes] == ["brasilapi", "apollo"]
    assert fontes[0].limite_mensal is None and fontes[1].limite_mensal == 42
    assert fontes[1]._limitador.limite == 200 and fontes[1]._limitador.janela == 3600.0
