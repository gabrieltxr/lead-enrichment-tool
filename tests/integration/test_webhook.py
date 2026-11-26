import time

from sqlalchemy import select

from app.db.models import EnriquecimentoEvento
from app.hubspot.assinatura import calcular_assinatura
from tests.helpers import URL_WEBHOOK, corpo_json, evento, headers_assinados


def _postar(client, dados, headers=None):
    corpo = dados if isinstance(dados, bytes) else corpo_json(dados)
    return client.post(
        "/webhooks/hubspot", content=corpo, headers=headers or headers_assinados(corpo)
    )


def _eventos(sessao):
    return sessao.scalars(select(EnriquecimentoEvento)).all()


def test_payload_valido_salva_evento_pendente(client, sessao):
    resp = _postar(client, [evento(contact_id=101)])

    assert resp.status_code == 200
    assert resp.json() == {"recebidos": 1, "gravados": 1, "duplicados": 0, "ignorados": 0}
    [ev] = _eventos(sessao)
    assert ev.hubspot_contact_id == "101"
    assert ev.status == "pendente"
    assert ev.tentativas == 0
    assert ev.recebido_em is not None
    assert ev.processado_em is None and ev.erro is None


def test_lote_com_varios_eventos(client, sessao):
    resp = _postar(client, [evento(101), evento(102, event_id=2)])
    assert resp.json()["gravados"] == 2
    assert {e.hubspot_contact_id for e in _eventos(sessao)} == {"101", "102"}


def test_reenvio_do_mesmo_contato_nao_duplica(client, sessao):
    _postar(client, [evento(101)])
    resp = _postar(client, [evento(101, event_id=99)])

    assert resp.status_code == 200
    assert resp.json()["duplicados"] == 1
    assert len(_eventos(sessao)) == 1


def test_eventos_que_nao_sao_criacao_de_contato_sao_ignorados(client, sessao):
    resp = _postar(client, [evento(101, tipo="contact.propertyChange")])
    assert resp.status_code == 200
    assert resp.json()["ignorados"] == 1
    assert _eventos(sessao) == []


def test_assinatura_invalida_retorna_401(client, sessao):
    corpo = corpo_json([evento()])
    resp = _postar(client, corpo, headers_assinados(corpo, segredo="segredo-errado"))
    assert resp.status_code == 401
    assert _eventos(sessao) == []


def test_sem_headers_de_assinatura_retorna_401(client, sessao):
    resp = _postar(client, [evento()], {"Content-Type": "application/json"})
    assert resp.status_code == 401
    assert _eventos(sessao) == []


def test_timestamp_antigo_retorna_401(client, sessao):
    corpo = corpo_json([evento()])
    ts = str(int(time.time() * 1000) - 10 * 60 * 1000)
    headers = {
        "X-HubSpot-Request-Timestamp": ts,
        "X-HubSpot-Signature-v3": calcular_assinatura(
            "segredo-de-teste", "POST", URL_WEBHOOK, corpo, ts
        ),
    }
    assert _postar(client, corpo, headers).status_code == 401


def test_assinatura_e_verificada_antes_do_payload(client):
    # Payload malformado com assinatura inválida: 401, não 422.
    resp = _postar(client, b"{nao e json", {"X-HubSpot-Signature-v3": "x"})
    assert resp.status_code == 401


def test_json_malformado_retorna_422(client, sessao):
    resp = _postar(client, b"{nao e json")
    assert resp.status_code == 422
    assert _eventos(sessao) == []


def test_payload_sem_campos_obrigatorios_retorna_422(client, sessao):
    resp = _postar(client, [{"eventId": 1, "subscriptionType": "contact.creation"}])
    assert resp.status_code == 422
    assert _eventos(sessao) == []


def test_lista_vazia_retorna_422(client):
    assert _postar(client, []).status_code == 422


def test_url_publica_configurada_e_usada_na_assinatura(client, sessao, monkeypatch):
    from app.config import get_settings

    url_publica = "https://leads.exemplo.com/webhooks/hubspot"
    monkeypatch.setattr(get_settings(), "webhook_url_publica", url_publica)
    corpo = corpo_json([evento(555)])

    assert _postar(client, corpo, headers_assinados(corpo)).status_code == 401
    assert _postar(client, corpo, headers_assinados(corpo, uri=url_publica)).status_code == 200


def test_assinatura_v1_de_webhook_de_app_salva_evento(client, sessao):
    import hashlib

    corpo = corpo_json([evento(888)])
    headers = {
        "Content-Type": "application/json",
        "X-HubSpot-Signature": hashlib.sha256(b"segredo-de-teste" + corpo).hexdigest(),
        "X-HubSpot-Signature-Version": "v1",
    }
    assert _postar(client, corpo, headers).status_code == 200
    assert [e.hubspot_contact_id for e in _eventos(sessao)] == ["888"]


def test_assinatura_v1_desligada_retorna_401(client, sessao, monkeypatch):
    import hashlib

    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "assinaturas_aceitas", "v3")
    corpo = corpo_json([evento(889)])
    headers = {"X-HubSpot-Signature": hashlib.sha256(b"segredo-de-teste" + corpo).hexdigest()}
    assert _postar(client, corpo, headers).status_code == 401
    assert _eventos(sessao) == []
