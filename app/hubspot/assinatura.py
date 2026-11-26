"""Validação da assinatura v3 dos webhooks do HubSpot.

Referência: https://developers.hubspot.com/docs/api/webhooks/validating-requests
A assinatura é HMAC-SHA256 (base64) de: método + URI + corpo + timestamp,
usando o client secret do app como chave.
"""

import base64
import hashlib
import hmac
import time

# O HubSpot decodifica estes caracteres na URI antes de assinar.
_DECODIFICAR_URI = {
    "%3A": ":", "%2F": "/", "%3F": "?", "%40": "@", "%21": "!", "%24": "$",
    "%27": "'", "%28": "(", "%29": ")", "%2A": "*", "%2C": ",", "%3B": ";",
}


def _normalizar_uri(uri: str) -> str:
    for codificado, caractere in _DECODIFICAR_URI.items():
        uri = uri.replace(codificado, caractere).replace(codificado.lower(), caractere)
    return uri


def calcular_assinatura(segredo: str, metodo: str, uri: str, corpo: bytes, timestamp: str) -> str:
    mensagem = metodo.upper().encode() + _normalizar_uri(uri).encode() + corpo + timestamp.encode()
    digest = hmac.new(segredo.encode(), mensagem, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def assinatura_valida(
    *,
    segredo: str,
    metodo: str,
    uri: str,
    corpo: bytes,
    timestamp: str | None,
    assinatura: str | None,
    janela_segundos: int,
    agora_ms: int | None = None,
) -> bool:
    """Retorna True só se a assinatura confere e o timestamp está dentro da janela."""
    if not timestamp or not assinatura:
        return False
    try:
        ts_ms = int(timestamp)
    except ValueError:
        return False
    agora_ms = agora_ms if agora_ms is not None else int(time.time() * 1000)
    if abs(agora_ms - ts_ms) > janela_segundos * 1000:
        return False
    esperada = calcular_assinatura(segredo, metodo, uri, corpo, timestamp)
    return hmac.compare_digest(esperada, assinatura)
