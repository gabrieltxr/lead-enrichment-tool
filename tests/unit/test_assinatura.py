from app.hubspot.assinatura import assinatura_valida, calcular_assinatura

SEGREDO = "segredo-de-teste"
URI = "https://exemplo.com/webhooks/hubspot"
CORPO = b'[{"objectId": 1}]'
AGORA = 1_764_178_977_000


def _validar(**kwargs) -> bool:
    padrao = dict(
        segredo=SEGREDO, metodo="POST", uri=URI, corpo=CORPO, timestamp=str(AGORA),
        assinatura=calcular_assinatura(SEGREDO, "POST", URI, CORPO, str(AGORA)),
        janela_segundos=300, agora_ms=AGORA,
    )
    padrao.update(kwargs)
    return assinatura_valida(**padrao)


def test_assinatura_correta_e_aceita():
    assert _validar()


def test_corpo_alterado_e_rejeitado():
    assert not _validar(corpo=b'[{"objectId": 2}]')


def test_segredo_errado_e_rejeitado():
    assert not _validar(segredo="outro")


def test_uri_diferente_e_rejeitada():
    assert not _validar(uri="https://outro.com/webhooks/hubspot")


def test_timestamp_fora_da_janela_e_rejeitado():
    assert not _validar(agora_ms=AGORA + 301_000)


def test_timestamp_no_limite_da_janela_e_aceito():
    assert _validar(agora_ms=AGORA + 300_000)


def test_headers_ausentes_sao_rejeitados():
    assert not _validar(timestamp=None)
    assert not _validar(assinatura=None)
    assert not _validar(assinatura="")


def test_timestamp_nao_numerico_e_rejeitado():
    assert not _validar(timestamp="abc")


def test_valor_conhecido_da_assinatura():
    # Vetor calculado à parte (HMAC-SHA256 base64 de "POSThttps://a.b/c{}1").
    # Detecta mudança acidental no algoritmo.
    esperado = "WP/xKOCR0FJbDVZN5MkBDYRiXc0pUn49Mlrl1W2+zjc="
    assert calcular_assinatura("s", "post", "https://a.b/c", b"{}", "1") == esperado


def test_uri_com_caracteres_codificados_e_normalizada():
    uri_codificada = "https://exemplo.com/webhooks/hubspot?a=1%3A2%2C3"
    uri_decodificada = "https://exemplo.com/webhooks/hubspot?a=1:2,3"
    assert calcular_assinatura(SEGREDO, "POST", uri_codificada, CORPO, "1") == (
        calcular_assinatura(SEGREDO, "POST", uri_decodificada, CORPO, "1")
    )
