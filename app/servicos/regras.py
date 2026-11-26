"""Regras de confiança e proteção de campos preenchidos manualmente.

Um campo só é gravado no HubSpot se:
- a confiança for pelo menos a mínima (padrão 0.70), e
- o campo estiver vazio, ou o valor atual for o que o próprio serviço gravou
  antes (registrado na auditoria) e a nova confiança for maior que a anterior.

Qualquer valor que não veio do serviço é tratado como manual e nunca é
sobrescrito.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.fontes.base import CampoEncontrado


@dataclass(frozen=True)
class GravacaoAnterior:
    """Última gravação aplicada pelo serviço numa propriedade do contato."""

    valor: str
    confianca: Decimal


@dataclass(frozen=True)
class Decisao:
    gravar: bool  # precisa escrever no HubSpot agora
    aplicado: bool  # o valor no HubSpot é (ou passa a ser) o desta fonte
    motivo: str


def _vazio(valor: str | None) -> bool:
    return valor is None or not valor.strip()


def decidir(
    novo: CampoEncontrado,
    propriedade: str | None,
    valor_atual: str | None,
    anterior: GravacaoAnterior | None,
    confianca_minima: Decimal,
) -> Decisao:
    if propriedade is None:
        return Decisao(False, False, "sem_mapeamento")
    if novo.confianca < confianca_minima:
        return Decisao(False, False, "confianca_baixa")
    if _vazio(valor_atual):
        return Decisao(True, True, "campo_vazio")
    atual = valor_atual.strip()
    nosso = anterior is not None and anterior.valor.strip() == atual
    if atual == novo.valor.strip():
        return Decisao(False, nosso, "ja_aplicado" if nosso else "valor_igual_existente")
    if not nosso:
        return Decisao(False, False, "campo_manual")
    if novo.confianca > anterior.confianca:
        return Decisao(True, True, "confianca_maior")
    return Decisao(False, False, "confianca_nao_maior")
