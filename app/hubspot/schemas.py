"""Payloads recebidos do HubSpot.

O webhook do HubSpot envia uma lista de eventos por requisição. Só os campos
usados pelo serviço são validados; os demais são ignorados.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, RootModel

TIPO_CONTATO_CRIADO = "contact.creation"


class EventoWebhook(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    event_id: int = Field(alias="eventId")
    subscription_type: str = Field(alias="subscriptionType", min_length=1)
    object_id: int = Field(alias="objectId", gt=0)
    portal_id: int | None = Field(default=None, alias="portalId")
    occurred_at: int | None = Field(default=None, alias="occurredAt")


class LoteWebhook(RootModel[Annotated[list[EventoWebhook], Field(min_length=1, max_length=1000)]]):
    pass


def definicoes_propriedades_controle(nome_origem: str, nome_data: str) -> list[dict]:
    """Propriedades customizadas criadas no HubSpot se ainda não existirem."""
    return [
        {
            "name": nome_origem,
            "label": "Origem do enriquecimento",
            "description": "Fontes que preencheram campos automaticamente.",
            "groupName": "contactinformation",
            "type": "string",
            "fieldType": "text",
        },
        {
            "name": nome_data,
            "label": "Data do enriquecimento",
            "description": "Data da última gravação automática de campos.",
            "groupName": "contactinformation",
            "type": "date",
            "fieldType": "date",
        },
    ]
