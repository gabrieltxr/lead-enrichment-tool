"""Validação da assinatura dos webhooks do HubSpot.

Referência: https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/request-validation

- v3 (`X-HubSpot-Signature-v3` + `X-HubSpot-Request-Timestamp`): HMAC-SHA256
  em base64 de método + URI + corpo + timestamp, com o client secret como chave.
- v1 (`X-HubSpot-Signature`, versão `v1`): SHA-256 em hex de secret + corpo.
  Usada por assinaturas de webhook de apps. Não tem timestamp, então não
  protege contra replay; a idempotência por contato limita o efeito.
- v2 (`X-HubSpot-Signature`, versão `v2`): SHA-256 em hex de secret + método +
  URI + corpo. Usada pela ação de webhook de workflows (planos Pro/Enterprise).

As versões aceitas são configuráveis (ASSINATURAS_ACEITAS).
"""

import base64
import hashlib
import hmac
import time
from collections.abc import Mapping

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


def calcular_assinatura_legada(
    segredo: str, versao: str, metodo: str, uri: str, corpo: bytes
) -> str:
    """Assinaturas v1 e v2: SHA-256 em hex, sem HMAC."""
    if versao == "v1":
        mensagem = segredo.encode() + corpo
    else:
        mensagem = segredo.encode() + metodo.upper().encode() + uri.encode() + corpo
    return hashlib.sha256(mensagem).hexdigest()


def requisicao_autentica(
    *,
    segredo: str,
    metodo: str,
    uri: str,
    corpo: bytes,
    headers: Mapping[str, str],
    janela_segundos: int,
    versoes_aceitas: frozenset[str],
    agora_ms: int | None = None,
) -> bool:
    """Valida a requisição pela versão de assinatura que ela traz.

    Se a requisição traz v3, só a v3 vale (não cai para v1/v2).
    """
    assinatura_v3 = headers.get("X-HubSpot-Signature-v3")
    if assinatura_v3 is not None:
        return "v3" in versoes_aceitas and assinatura_valida(
            segredo=segredo, metodo=metodo, uri=uri, corpo=corpo,
            timestamp=headers.get("X-HubSpot-Request-Timestamp"),
            assinatura=assinatura_v3, janela_segundos=janela_segundos, agora_ms=agora_ms,
        )
    assinatura = headers.get("X-HubSpot-Signature")
    versao = (headers.get("X-HubSpot-Signature-Version") or "v1").lower()
    if not assinatura or versao not in {"v1", "v2"} or versao not in versoes_aceitas:
        return False
    esperada = calcular_assinatura_legada(segredo, versao, metodo, uri, corpo)
    return hmac.compare_digest(esperada, assinatura.lower())
