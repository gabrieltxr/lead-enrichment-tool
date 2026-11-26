from decimal import Decimal

import pytest

from app.fontes.base import CampoEncontrado
from app.servicos.regras import Decisao, GravacaoAnterior, decidir

MINIMA = Decimal("0.70")


def _campo(valor="ACME", confianca="1.00"):
    return CampoEncontrado("razao_social", valor, Decimal(confianca))


def _decidir(campo=None, propriedade="company", atual=None, anterior=None):
    return decidir(campo or _campo(), propriedade, atual, anterior, MINIMA)


def test_campo_vazio_e_gravado():
    assert _decidir(atual=None) == Decisao(True, True, "campo_vazio")
    assert _decidir(atual="  ") == Decisao(True, True, "campo_vazio")


def test_campo_manual_nunca_e_sobrescrito():
    assert _decidir(atual="Digitado pelo vendedor") == Decisao(False, False, "campo_manual")


def test_campo_manual_preservado_mesmo_com_confianca_maior_que_gravacao_antiga():
    # Valor atual difere do que o serviço gravou: alguém editou à mão.
    anterior = GravacaoAnterior("ACME VELHA", Decimal("0.80"))
    assert _decidir(atual="Editado", anterior=anterior).motivo == "campo_manual"


def test_confianca_maior_substitui_gravacao_do_servico():
    anterior = GravacaoAnterior("ACME VELHA", Decimal("0.80"))
    assert _decidir(atual="ACME VELHA", anterior=anterior) == Decisao(True, True, "confianca_maior")


@pytest.mark.parametrize("conf_anterior", ["1.00", "0.90"])
def test_confianca_igual_ou_menor_nao_substitui(conf_anterior):
    anterior = GravacaoAnterior("ACME VELHA", Decimal(conf_anterior))
    campo = _campo(confianca="0.90")
    assert _decidir(campo, atual="ACME VELHA", anterior=anterior).motivo == "confianca_nao_maior"


def test_confianca_abaixo_do_minimo_nao_grava_nem_campo_vazio():
    decisao = _decidir(_campo(confianca="0.69"), atual=None)
    assert decisao == Decisao(False, False, "confianca_baixa")


def test_confianca_no_minimo_grava():
    assert _decidir(_campo(confianca="0.70"), atual=None).gravar


def test_sem_mapeamento_so_audita():
    assert _decidir(propriedade=None) == Decisao(False, False, "sem_mapeamento")


def test_mesmo_valor_ja_gravado_pelo_servico_nao_regrava():
    anterior = GravacaoAnterior("ACME", Decimal("1.00"))
    assert _decidir(atual="ACME", anterior=anterior) == Decisao(False, True, "ja_aplicado")


def test_mesmo_valor_digitado_manualmente_nao_conta_como_aplicado():
    assert _decidir(atual="ACME") == Decisao(False, False, "valor_igual_existente")
