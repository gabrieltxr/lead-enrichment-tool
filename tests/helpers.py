import json
import time

from app.hubspot.assinatura import calcular_assinatura

SEGREDO = "segredo-de-teste"
URL_WEBHOOK = "https://teste.local/webhooks/hubspot"


def evento(contact_id: int = 101, tipo: str = "contact.creation", event_id: int = 1) -> dict:
    return {
        "eventId": event_id,
        "subscriptionId": 9,
        "portalId": 123,
        "appId": 456,
        "occurredAt": 1764178977000,
        "subscriptionType": tipo,
        "attemptNumber": 0,
        "objectId": contact_id,
        "changeFlag": "CREATED",
        "changeSource": "CRM_UI",
    }


def headers_assinados(corpo: bytes, segredo: str = SEGREDO, uri: str = URL_WEBHOOK) -> dict:
    ts = str(int(time.time() * 1000))
    return {
        "Content-Type": "application/json",
        "X-HubSpot-Request-Timestamp": ts,
        "X-HubSpot-Signature-v3": calcular_assinatura(segredo, "POST", uri, corpo, ts),
    }


def corpo_json(dados: object) -> bytes:
    return json.dumps(dados).encode()
