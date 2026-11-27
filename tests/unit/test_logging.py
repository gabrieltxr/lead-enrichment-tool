import json
import logging

from app.utils.logging import FormatoJson, configurar_logging, mascarar, sanitizar


def _formatar(**extra):
    registro = logging.makeLogRecord({"msg": "evento", "levelname": "INFO", **extra})
    return json.loads(FormatoJson().format(registro))


def test_log_remove_campos_pessoais():
    saida = _formatar(email="a@b.com", hubspot_contact_id="7")
    assert saida["email"] == "[removido]"
    assert saida["hubspot_contact_id"] == "7"
    assert "a@b.com" not in json.dumps(saida)


def test_log_remove_segredos():
    saida = _formatar(token="abc", Authorization="Bearer x", api_key="k")
    assert saida["token"] == saida["Authorization"] == saida["api_key"] == "[removido]"


def test_ids_longos_nao_sao_mascarados():
    saida = _formatar(hubspot_contact_id="123456789012", evento_id=98765432101)
    assert saida["hubspot_contact_id"] == "123456789012"
    assert saida["evento_id"] == 98765432101


def test_mascara_dados_pessoais_em_texto_livre():
    texto = "falha para ana.silva@empresa.com.br tel (11) 98765-4321 cpf 123.456.789-09"
    limpo = mascarar(texto)
    assert "ana.silva" not in limpo and "98765" not in limpo and "789-09" not in limpo
    assert mascarar("Authorization: Bearer eyJabc.def") == "Authorization: [removido]"
    assert mascarar("HTTP 503 em /api/cnpj/v1") == "HTTP 503 em /api/cnpj/v1"


def test_mascara_mensagem_valores_aninhados_e_traceback():
    try:
        raise ValueError("contato x@y.com invalido")
    except ValueError:
        import sys

        registro = logging.makeLogRecord({
            "msg": "erro com %s", "args": ("z@w.com",), "levelname": "ERROR",
            "exc_info": sys.exc_info(), "detalhes": {"nome": "Ana", "fontes": ["a@b.com", 3]},
        })
    saida = FormatoJson().format(registro)
    assert "@" not in saida
    dados = json.loads(saida)
    assert dados["detalhes"] == {"nome": "[removido]", "fontes": ["[removido]", 3]}


def test_sanitizar_tipos():
    assert sanitizar(None) is None and sanitizar(1.5) == 1.5 and sanitizar(True) is True
    assert sanitizar(("a@b.com",)) == ["[removido]"]
    assert sanitizar(object()).startswith("<object")


def test_configurar_logging_unifica_loggers():
    configurar_logging("debug")
    raiz = logging.getLogger()
    assert raiz.level == logging.DEBUG
    assert isinstance(raiz.handlers[0].formatter, FormatoJson)
    assert logging.getLogger("uvicorn.access").propagate is True
    assert logging.getLogger("httpx").level == logging.WARNING
