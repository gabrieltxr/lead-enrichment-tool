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
