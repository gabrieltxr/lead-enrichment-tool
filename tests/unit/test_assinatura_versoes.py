import hashlib

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.hubspot.assinatura import calcular_assinatura, requisicao_autentica

SEGREDO = "segredo-de-teste"
URI = "https://exemplo.com/webhooks/hubspot"
CORPO = b'[{"objectId": 1}]'
AGORA = 1_764_178_977_000
TODAS = frozenset({"v1", "v2", "v3"})


def _autentica(headers, versoes=TODAS, **kwargs):
    padrao = dict(
        segredo=SEGREDO, metodo="POST", uri=URI, corpo=CORPO, headers=headers,
        janela_segundos=300, versoes_aceitas=versoes, agora_ms=AGORA,
    )
    padrao.update(kwargs)
    return requisicao_autentica(**padrao)


def _v1(segredo=SEGREDO, corpo=CORPO):
    return hashlib.sha256(segredo.encode() + corpo).hexdigest()


def _v2(segredo=SEGREDO):
    return hashlib.sha256((segredo + "POST" + URI).encode() + CORPO).hexdigest()


def _v3(segredo=SEGREDO):
    return {
        "X-HubSpot-Signature-v3": calcular_assinatura(segredo, "POST", URI, CORPO, str(AGORA)),
        "X-HubSpot-Request-Timestamp": str(AGORA),
    }


def test_v3_valida():
    assert _autentica(_v3())


def test_v1_valida_com_e_sem_header_de_versao():
    assert _autentica({"X-HubSpot-Signature": _v1(), "X-HubSpot-Signature-Version": "v1"})
    assert _autentica({"X-HubSpot-Signature": _v1()})


def test_v1_em_maiusculas_e_aceita():
    assert _autentica({"X-HubSpot-Signature": _v1().upper()})


def test_v1_com_corpo_alterado_e_rejeitada():
    assert not _autentica({"X-HubSpot-Signature": _v1(corpo=b"[]")})


def test_v1_com_segredo_errado_e_rejeitada():
    assert not _autentica({"X-HubSpot-Signature": _v1(segredo="outro")})


def test_v2_valida():
    assert _autentica({"X-HubSpot-Signature": _v2(), "X-HubSpot-Signature-Version": "v2"})


def test_v2_nao_confunde_com_v1():
    assert not _autentica({"X-HubSpot-Signature": _v1(), "X-HubSpot-Signature-Version": "v2"})


def test_versao_desconhecida_e_rejeitada():
    assert not _autentica({"X-HubSpot-Signature": _v1(), "X-HubSpot-Signature-Version": "v9"})


def test_versao_nao_aceita_e_rejeitada():
    assert not _autentica({"X-HubSpot-Signature": _v1()}, versoes=frozenset({"v3"}))
    assert not _autentica(_v3(), versoes=frozenset({"v1"}))


def test_v3_invalida_nao_cai_para_v1():
    headers = {**_v3(segredo="outro"), "X-HubSpot-Signature": _v1()}
    assert not _autentica(headers)


def test_sem_nenhuma_assinatura_e_rejeitada():
    assert not _autentica({})


def _settings(**kwargs):
    return Settings(hubspot_token="t", hubspot_webhook_secret="s", database_url="x", **kwargs)


def test_config_normaliza_versoes():
    assert _settings(assinaturas_aceitas=" V3, v1 ").versoes_assinatura == {"v1", "v3"}


def test_config_padrao_aceita_v3_e_v1():
    assert _settings().versoes_assinatura == {"v1", "v3"}


@pytest.mark.parametrize("valor", ["", "v4", "v3,abc"])
def test_config_rejeita_versoes_invalidas(valor):
    with pytest.raises(ValidationError):
        _settings(assinaturas_aceitas=valor)
