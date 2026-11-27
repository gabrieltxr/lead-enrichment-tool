"""Logs estruturados em JSON, sem dados pessoais.

Regra do projeto: nunca registrar nome, e-mail ou telefone. Use apenas IDs
(contato, evento), nomes de fonte e contagens nos campos extras.

Como segunda linha de defesa, o formatador:
- substitui por `[removido]` campos extras com nomes de dados pessoais ou de
  segredos (email, nome, telefone, token, api_key...), em qualquer nível;
- mascara e-mails, sequências que parecem telefone ou CPF/CNPJ e tokens
  `Bearer` dentro da mensagem, dos valores extras e do traceback.
"""

import json
import logging
import re
from datetime import UTC, datetime

REMOVIDO = "[removido]"

_CAMPOS_PESSOAIS = {
    "email", "e_mail", "nome", "name", "firstname", "lastname", "fullname",
    "phone", "telefone", "mobilephone", "celular", "cpf",
}
_CAMPOS_SEGREDO = {
    "token", "authorization", "api_key", "apikey", "senha", "password", "secret",
    "hubspot_token", "provedor_dominio_api_key", "hubspot_webhook_secret",
}
_CAMPOS_PROIBIDOS = _CAMPOS_PESSOAIS | _CAMPOS_SEGREDO
_ATRIBUTOS_PADRAO = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

_PADROES = (
    re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),  # e-mail
    re.compile(r"(?i)bearer\s+[\w.~+/=-]+"),  # token no header
    # telefone, CPF, CNPJ: 10+ dígitos, com ou sem separadores comuns
    re.compile(r"\+?\(?\d[\d\s().\-/]{8,}\d"),
)


def mascarar(texto: str) -> str:
    """Remove de um texto livre o que parece e-mail, telefone, documento ou token."""
    for padrao in _PADROES:
        texto = padrao.sub(REMOVIDO, texto)
    return texto


def _eh_id(chave: str, valor: object) -> bool:
    chave = chave.lower()
    return (chave == "id" or chave.endswith("_id")) and (
        isinstance(valor, int) or (isinstance(valor, str) and valor.isdigit())
    )


def sanitizar(valor: object, chave: str | None = None) -> object:
    if chave is not None and chave.lower() in _CAMPOS_PROIBIDOS:
        return REMOVIDO
    if chave is not None and _eh_id(chave, valor):
        return valor  # IDs do HubSpot têm 10+ dígitos e não são dado pessoal
    if isinstance(valor, str):
        return mascarar(valor)
    if isinstance(valor, dict):
        return {k: sanitizar(v, str(k)) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set, frozenset)):
        return [sanitizar(v) for v in valor]
    if valor is None or isinstance(valor, (bool, int, float)):
        return valor
    return mascarar(str(valor))


class FormatoJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dados = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "nivel": record.levelname,
            "logger": record.name,
            "msg": mascarar(record.getMessage()),
        }
        for chave, valor in vars(record).items():
            if chave not in _ATRIBUTOS_PADRAO:
                dados[chave] = sanitizar(valor, chave)
        if record.exc_info:
            dados["exc"] = mascarar(self.formatException(record.exc_info))
        return json.dumps(dados, ensure_ascii=False, default=str)


def configurar_logging(nivel: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(FormatoJson())
    raiz = logging.getLogger()
    raiz.handlers[:] = [handler]
    raiz.setLevel(nivel.upper())
    # Logs de acesso e do httpx passam pelo mesmo formatador (URLs podem ter dados).
    for nome in ("uvicorn", "uvicorn.access", "uvicorn.error", "httpx"):
        registro = logging.getLogger(nome)
        registro.handlers[:] = []
        registro.propagate = True
    logging.getLogger("httpx").setLevel("WARNING")
