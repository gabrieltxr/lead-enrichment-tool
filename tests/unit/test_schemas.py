import pytest
from pydantic import ValidationError

from app.hubspot.schemas import LoteWebhook
from tests.helpers import evento


def test_lote_valido_ignora_campos_extras():
    lote = LoteWebhook.model_validate([evento(contact_id=42)])
    assert lote.root[0].object_id == 42
    assert lote.root[0].subscription_type == "contact.creation"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"objectId": 1},
        [{"eventId": 1, "subscriptionType": "contact.creation"}],
        [{**evento(), "objectId": "abc"}],
        [{**evento(), "objectId": 0}],
        [{**evento(), "subscriptionType": ""}],
    ],
)
def test_payloads_invalidos_sao_rejeitados(payload):
    with pytest.raises(ValidationError):
        LoteWebhook.model_validate(payload)
