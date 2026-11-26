"""Consulta de CNPJ na BrasilAPI (https://brasilapi.com.br/docs#tag/CNPJ).

A busca é por CNPJ exato, então todos os campos têm confiança 1.00. Dados de
sócios (QSA) e telefones da resposta são ignorados: são dados pessoais e não
fazem parte do enriquecimento.
"""

from decimal import Decimal

import httpx

from app.fontes.base import CampoEncontrado, DadosLead, Fonte
from app.utils.retry import TIMEOUT_PADRAO, LimitadorTaxa, politica_http, verificar_resposta

URL_PADRAO = "https://brasilapi.com.br/api/cnpj/v1"
CONFIANCA_CNPJ_EXATO = Decimal("1.00")


def _texto(valor: object) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _endereco(dados: dict) -> str | None:
    partes = [
        _texto(dados.get("descricao_tipo_de_logradouro")),
        _texto(dados.get("logradouro")),
    ]
    rua = " ".join(p for p in partes if p)
    numero = _texto(dados.get("numero"))
    if not rua:
        return None
    return f"{rua}, {numero}" if numero else rua


def extrair_campos(dados: dict) -> list[CampoEncontrado]:
    """Converte a resposta da BrasilAPI em campos internos."""
    cnae = _texto(dados.get("cnae_fiscal_descricao"))
    cep = _texto(dados.get("cep"))
    valores = {
        "razao_social": _texto(dados.get("razao_social")),
        "nome_fantasia": _texto(dados.get("nome_fantasia")),
        "cnae_principal": cnae.capitalize() if cnae else None,
        "situacao_cadastral": _texto(dados.get("descricao_situacao_cadastral")),
        "porte": _texto(dados.get("porte")),
        "municipio": _texto(dados.get("municipio")),
        "uf": _texto(dados.get("uf")),
        "cep": cep.zfill(8) if cep else None,
        "endereco": _endereco(dados),
    }
    return [
        CampoEncontrado(campo=c, valor=v, confianca=CONFIANCA_CNPJ_EXATO)
        for c, v in valores.items()
        if v
    ]


class FonteBrasilAPI(Fonte):
    nome = "brasilapi"

    def __init__(
        self,
        http: httpx.AsyncClient,
        url_base: str = URL_PADRAO,
        tentativas: int = 4,
        limitador: LimitadorTaxa | None = None,
    ) -> None:
        self._http = http
        self._url_base = url_base.rstrip("/")
        self._tentativas = tentativas
        self._limitador = limitador or LimitadorTaxa(limite=3, janela=1.0)

    def aplicavel(self, dados: DadosLead) -> bool:
        return dados.cnpj is not None

    async def consultar(self, dados: DadosLead) -> list[CampoEncontrado]:
        if dados.cnpj is None:
            return []
        async for tentativa in politica_http(self._tentativas):
            with tentativa:
                await self._limitador.aguardar()
                resposta = await self._http.get(
                    f"{self._url_base}/{dados.cnpj}", timeout=TIMEOUT_PADRAO
                )
                if resposta.status_code == 404:
                    return []
                verificar_resposta(resposta)
        return extrair_campos(resposta.json())
