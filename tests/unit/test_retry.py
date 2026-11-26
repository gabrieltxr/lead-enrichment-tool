import asyncio

import httpx
import pytest

from app.utils.retry import (
    ErroPermanente,
    ErroTransitorio,
    LimitadorTaxa,
    backoff_evento,
    politica_http,
    verificar_resposta,
)


def _resposta(status):
    return httpx.Response(status, request=httpx.Request("GET", "https://x.test/caminho"))


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_status_transitorios(status):
    with pytest.raises(ErroTransitorio):
        verificar_resposta(_resposta(status))


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_status_permanentes(status):
    with pytest.raises(ErroPermanente):
        verificar_resposta(_resposta(status))


def test_status_ok_passa():
    assert verificar_resposta(_resposta(200)).status_code == 200


def test_mensagem_de_erro_nao_inclui_query_string():
    resp = httpx.Response(500, request=httpx.Request("GET", "https://x.test/c?email=a@b.com"))
    with pytest.raises(ErroTransitorio) as exc:
        verificar_resposta(resp)
    assert "a@b.com" not in str(exc.value)


def test_backoff_evento_cresce_e_tem_teto():
    assert [backoff_evento(t) for t in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert backoff_evento(20) == 3600


async def _executar(politica, funcao):
    async for tentativa in politica:
        with tentativa:
            return funcao()


async def test_politica_repete_erro_transitorio_e_desiste():
    chamadas = []

    def falha():
        chamadas.append(1)
        raise ErroTransitorio("x")

    with pytest.raises(ErroTransitorio):
        await _executar(politica_http(tentativas=3), falha)
    assert len(chamadas) == 3


async def test_politica_nao_repete_erro_permanente():
    chamadas = []

    def falha():
        chamadas.append(1)
        raise ErroPermanente("x")

    with pytest.raises(ErroPermanente):
        await _executar(politica_http(tentativas=3), falha)
    assert len(chamadas) == 1


async def test_limitador_espera_quando_janela_esta_cheia(monkeypatch):
    agora = [0.0]
    esperas = []

    async def sleep_falso(segundos):
        esperas.append(segundos)
        agora[0] += segundos

    monkeypatch.setattr(asyncio, "sleep", sleep_falso)
    limitador = LimitadorTaxa(limite=2, janela=10.0, relogio=lambda: agora[0])
    await limitador.aguardar()
    await limitador.aguardar()
    assert esperas == []
    await limitador.aguardar()
    assert esperas == [10.0]
