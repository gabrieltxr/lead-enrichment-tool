import json
import logging

from app.utils.logging import FormatoJson


def test_log_remove_campos_pessoais():
    registro = logging.makeLogRecord(
        {"msg": "evento", "levelname": "INFO", "email": "a@b.com", "hubspot_contact_id": "7"}
    )
    saida = json.loads(FormatoJson().format(registro))
    assert saida["email"] == "[removido]"
    assert saida["hubspot_contact_id"] == "7"
    assert "a@b.com" not in json.dumps(saida)
