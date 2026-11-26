"""Logs estruturados em JSON, sem dados pessoais.

Regra do projeto: nunca registrar nome, e-mail ou telefone. Use apenas IDs
(contato, evento) nos campos extras. A Fase 3 amplia este módulo.
"""

import json
import logging
from datetime import UTC, datetime

_CAMPOS_PROIBIDOS = {"email", "nome", "firstname", "lastname", "phone", "telefone", "mobilephone"}
_ATRIBUTOS_PADRAO = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class FormatoJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dados = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for chave, valor in vars(record).items():
            if chave in _ATRIBUTOS_PADRAO:
                continue
            dados[chave] = "[removido]" if chave.lower() in _CAMPOS_PROIBIDOS else valor
        if record.exc_info:
            dados["exc"] = self.formatException(record.exc_info)
        return json.dumps(dados, ensure_ascii=False, default=str)


def configurar_logging(nivel: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(FormatoJson())
    raiz = logging.getLogger()
    raiz.handlers[:] = [handler]
    raiz.setLevel(nivel.upper())
