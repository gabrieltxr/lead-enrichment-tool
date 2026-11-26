"""Worker de ponta a ponta: banco real, HubSpot falso em memória e BrasilAPI
servida a partir da resposta gravada."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.models import EnriquecimentoCampo, EnriquecimentoEvento
from app.fontes.brasilapi import FonteBrasilAPI
from app.servicos.worker import Worker
from app.utils.retry import ErroPermanente, LimitadorTaxa
from scripts.reprocessar import reprocessar
from tests.helpers import fixture_json

CNPJ_FORMATADO = "19.131.243/0001-97"


class HubSpotFalso:
    def __init__(self, contatos=None, propriedades=None):
        self.contatos = contatos or {}
        self.propriedades = set(propriedades or ["email", "cnpj", "company", "city", "state",
                                                 "zip", "address", "industry"])
        self.atualizacoes, self.notas, self.criadas = [], [], []
        self.falhar_atualizacao = False

    async def ler_contato(self, contact_id, propriedades):
        if contact_id not in self.contatos:
            raise ErroPermanente(f"HTTP 404 em /crm/v3/objects/contacts/{contact_id}")
        return {p: self.contatos[contact_id].get(p) for p in propriedades}

    async def atualizar_contato(self, contact_id, props):
        if self.falhar_atualizacao:
            raise RuntimeError("HubSpot fora")
        self.atualizacoes.append((contact_id, dict(props)))
        self.contatos[contact_id].update(props)

    async def criar_nota(self, contact_id, corpo):
        self.notas.append((contact_id, corpo))
        return str(90000 + len(self.notas))

    async def listar_propriedades_contato(self):
        return set(self.propriedades)

    async def criar_propriedade_contato(self, definicao):
        self.criadas.append(definicao["name"])
        self.propriedades.add(definicao["name"])


class BrasilAPIFalsa:
    def __init__(self):
        self.status = 200
        self.chamadas = 0

    def __call__(self, request):
        self.chamadas += 1
        if self.status != 200:
            return httpx.Response(self.status)
        cnpj = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json=fixture_json(f"brasilapi/cnpj_{cnpj}.json"))


@pytest.fixture
def settings():
    return Settings(
        hubspot_token="t", hubspot_webhook_secret="s", database_url="x", max_tentativas=3
    )


@pytest.fixture
def brasilapi():
    return BrasilAPIFalsa()


@pytest.fixture
async def montar(engine, sessao, settings, brasilapi):
    async with httpx.AsyncClient(transport=httpx.MockTransport(brasilapi)) as http:
        fonte = FonteBrasilAPI(http, limitador=LimitadorTaxa(limite=1000, janela=1.0))

        def _montar(hubspot):
            return Worker(sessionmaker(bind=engine), hubspot, [fonte], settings)

        yield _montar


def _novo_evento(sessao, contact_id="101"):
    ev = EnriquecimentoEvento(hubspot_contact_id=contact_id)
    sessao.add(ev)
    sessao.commit()
    return ev.id


def _evento(sessao, evento_id):
    sessao.expire_all()
    return sessao.get(EnriquecimentoEvento, evento_id)


def _campos(sessao, evento_id):
    sessao.expire_all()
    stmt = select(EnriquecimentoCampo).where(EnriquecimentoCampo.evento_id == evento_id)
    return {c.campo: c for c in sessao.scalars(stmt)}


def _liberar(sessao, evento_id, status=None):
    """Simula a passagem do tempo de backoff (e, opcionalmente, um reprocessamento)."""
    valores = {"proxima_tentativa_em": datetime.now(UTC) - timedelta(seconds=1)}
    if status:
        valores["status"] = status
    sessao.execute(update(EnriquecimentoEvento).where(EnriquecimentoEvento.id == evento_id).values(**valores))
    sessao.commit()


def _contato(**props):
    base = {"email": "contato@exemplo-empresa.com.br", "cnpj": CNPJ_FORMATADO}
    return {**base, **props}


async def test_contato_com_cnpj_enriquecido_de_ponta_a_ponta(montar, sessao):
    hubspot = HubSpotFalso({"101": _contato()})
    evento_id = _novo_evento(sessao)

    assert await montar(hubspot).executar_ciclo() == 1

    contato = hubspot.contatos["101"]
    assert contato["company"] == "OPEN KNOWLEDGE BRASIL"
    assert contato["city"] == "SAO PAULO" and contato["state"] == "SP"
    assert contato["zip"] == "01311902"
    assert contato["origem_enriquecimento"] == "brasilapi"
    assert contato["data_enriquecimento"] == datetime.now(UTC).date().isoformat()
    assert len(hubspot.notas) == 1 and "company" in hubspot.notas[0][1]
    assert "OPEN KNOWLEDGE" not in hubspot.notas[0][1]  # nota sem valores
    ev = _evento(sessao, evento_id)
    assert (ev.status, ev.tentativas, ev.erro) == ("concluido", 1, None)
    assert ev.processado_em is not None and ev.hubspot_nota_id == "90001"
    campos = _campos(sessao, evento_id)
    assert campos["razao_social"].aplicado and campos["razao_social"].motivo == "campo_vazio"
    assert campos["razao_social"].confianca == Decimal("1.00")
    assert campos["nome_fantasia"].motivo == "sem_mapeamento" and not campos["nome_fantasia"].aplicado


async def test_idempotencia_processar_duas_vezes_nao_duplica(montar, sessao):
    hubspot = HubSpotFalso({"101": _contato()})
    evento_id = _novo_evento(sessao)
    worker = montar(hubspot)

    await worker.executar_ciclo()
    _liberar(sessao, evento_id, status="pendente")
    await worker.executar_ciclo()

    assert len(hubspot.notas) == 1
    assert len(hubspot.atualizacoes) == 1
    total = sessao.execute(text("SELECT count(*) FROM enriquecimento_campo")).scalar()
    assert total == len(_campos(sessao, evento_id)) == 9
    campos = _campos(sessao, evento_id)
    assert campos["razao_social"].aplicado and campos["razao_social"].motivo == "ja_aplicado"
    assert _evento(sessao, evento_id).status == "concluido"


async def test_campo_manual_e_preservado(montar, sessao):
    hubspot = HubSpotFalso({"101": _contato(city="Campinas", company="Nome digitado")})
    evento_id = _novo_evento(sessao)

    await montar(hubspot).executar_ciclo()

    contato = hubspot.contatos["101"]
    assert contato["city"] == "Campinas" and contato["company"] == "Nome digitado"
    assert contato["state"] == "SP"  # campo vazio continua sendo preenchido
    gravadas = hubspot.atualizacoes[0][1]
    assert "city" not in gravadas and "company" not in gravadas
    campos = _campos(sessao, evento_id)
    assert campos["municipio"].motivo == "campo_manual" and not campos["municipio"].aplicado
    assert "preservados" in hubspot.notas[0][1]


async def test_falha_da_api_externa_gera_retry(montar, sessao, brasilapi):
    brasilapi.status = 503
    hubspot = HubSpotFalso({"101": _contato()})
    evento_id = _novo_evento(sessao)
    worker = montar(hubspot)

    await worker.executar_ciclo()

    assert brasilapi.chamadas == 4  # retry com backoff dentro da fonte
    ev = _evento(sessao, evento_id)
    assert (ev.status, ev.tentativas) == ("pendente", 1)
    assert "ErroTransitorio" in ev.erro
    assert ev.proxima_tentativa_em > datetime.now(UTC) + timedelta(seconds=50)
    assert await worker.executar_ciclo() == 0  # ainda no backoff

    brasilapi.status = 200
    _liberar(sessao, evento_id)
    await worker.executar_ciclo()
    ev = _evento(sessao, evento_id)
    assert (ev.status, ev.tentativas, ev.erro) == ("concluido", 2, None)


async def test_falha_permanente_vira_erro_apos_max_tentativas(montar, sessao, brasilapi):
    brasilapi.status = 500
    hubspot = HubSpotFalso({"101": _contato()})
    evento_id = _novo_evento(sessao)
    worker = montar(hubspot)

    for _ in range(3):  # max_tentativas=3
        await worker.executar_ciclo()
        _liberar(sessao, evento_id)

    ev = _evento(sessao, evento_id)
    assert (ev.status, ev.tentativas) == ("erro", 3)
    assert ev.processado_em is not None and "HTTP 500" in ev.erro
    assert await worker.executar_ciclo() == 0
    assert hubspot.atualizacoes == [] and hubspot.notas == []


async def test_erro_permanente_vira_erro_na_primeira_tentativa(montar, sessao):
    evento_id = _novo_evento(sessao, contact_id="999")
    await montar(HubSpotFalso()).executar_ciclo()
    ev = _evento(sessao, evento_id)
    assert (ev.status, ev.tentativas) == ("erro", 1)
    assert "ErroPermanente" in ev.erro


async def test_falha_ao_gravar_no_hubspot_desfaz_auditoria(montar, sessao):
    hubspot = HubSpotFalso({"101": _contato()})
    hubspot.falhar_atualizacao = True
    evento_id = _novo_evento(sessao)
    await montar(hubspot).executar_ciclo()
    assert _campos(sessao, evento_id) == {}
    assert _evento(sessao, evento_id).status == "pendente"


async def test_contato_sem_cnpj_conclui_sem_nota(montar, sessao, brasilapi):
    hubspot = HubSpotFalso({"101": {"email": "ana@gmail.com", "cnpj": None}})
    evento_id = _novo_evento(sessao)
    await montar(hubspot).executar_ciclo()
    assert brasilapi.chamadas == 0
    assert hubspot.notas == [] and hubspot.atualizacoes == []
    assert _evento(sessao, evento_id).status == "concluido"


async def test_preparar_cria_so_propriedades_que_faltam(montar):
    hubspot = HubSpotFalso(propriedades=["email", "cnpj", "origem_enriquecimento"])
    await montar(hubspot).preparar()
    assert hubspot.criadas == ["data_enriquecimento"]


async def test_evento_reservado_nao_e_pego_por_outro_worker(montar, sessao, engine):
    evento_id = _novo_evento(sessao)
    worker = montar(HubSpotFalso({"101": _contato()}))

    with engine.connect() as outra:
        outra.execute(text("BEGIN"))
        outra.execute(text("SELECT id FROM enriquecimento_evento FOR UPDATE"))
        assert worker.reservar_lote() == []  # SKIP LOCKED
        outra.execute(text("ROLLBACK"))

    [reservado] = worker.reservar_lote()
    assert reservado.id == evento_id and reservado.tentativas == 1
    assert worker.reservar_lote() == []  # reserva vale até vencer
    assert _evento(sessao, evento_id).status == "processando"
    _liberar(sessao, evento_id)  # reserva venceu (worker caiu)
    assert [e.id for e in worker.reservar_lote()] == [evento_id]


def test_reprocessar_devolve_eventos_com_erro(sessao):
    com_erro = _novo_evento(sessao, "1")
    outro = _novo_evento(sessao, "2")
    sessao.execute(
        update(EnriquecimentoEvento).values(status="erro", tentativas=5, erro="x")
    )
    sessao.commit()

    assert reprocessar(sessao, [com_erro], dry_run=True) == [com_erro]
    assert _evento(sessao, com_erro).status == "erro"
    assert reprocessar(sessao, [com_erro]) == [com_erro]
    ev = _evento(sessao, com_erro)
    assert (ev.status, ev.tentativas, ev.erro) == ("pendente", 0, None)
    assert _evento(sessao, outro).status == "erro"
    assert reprocessar(sessao, None) == [outro]


def test_cli_reprocessar(sessao, engine, monkeypatch, capsys):
    import app.db.session as db_session
    from scripts.reprocessar import main

    evento_id = _novo_evento(sessao, "3")
    sessao.execute(update(EnriquecimentoEvento).values(status="erro"))
    sessao.commit()
    monkeypatch.setattr(db_session, "get_sessionmaker", lambda: sessionmaker(bind=engine))

    assert main(["--todos", "--dry-run"]) == 0
    assert "1 evento(s) seriam reprocessados" in capsys.readouterr().out
    assert main(["--ids", str(evento_id)]) == 0
    assert "voltaram para a fila" in capsys.readouterr().out
    assert _evento(sessao, evento_id).status == "pendente"
