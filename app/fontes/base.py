"""Interface comum das fontes de enriquecimento."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class DadosLead:
    """Dados já normalizados que as fontes podem usar na consulta."""

    hubspot_contact_id: str
    dominio: str | None = None
    cnpj: str | None = None


@dataclass(frozen=True)
class CampoEncontrado:
    campo: str  # nome interno (ex.: "razao_social"), mapeado para o HubSpot depois
    valor: str
    confianca: Decimal


class Fonte(ABC):
    nome: str

    @abstractmethod
    def aplicavel(self, dados: DadosLead) -> bool:
        """True se a fonte tem o que precisa para consultar este lead."""

    @abstractmethod
    async def consultar(self, dados: DadosLead) -> list[CampoEncontrado]:
        """Campos encontrados. Lista vazia se a fonte não conhece o lead.

        Erros transitórios sobem como ErroTransitorio (após o retry da fonte).
        """
