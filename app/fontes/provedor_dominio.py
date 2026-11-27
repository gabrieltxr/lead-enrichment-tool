"""Fonte paga de dados de empresa por domínio, agnóstica de provedor.

Só é usada com FONTE_DOMINIO_ATIVA=true. Nada aqui depende de um provedor
específico: a URL (com o marcador {dominio}), o header de autenticação e o
mapeamento entre a resposta JSON e os campos internos vêm da configuração,
partindo de um preset (`apollo` por padrão, ou `personalizado`).

Correspondência por domínio é exata, mas menos certa que o CNPJ: a confiança
padrão é 0.85. Se o provedor devolver a própria confiança, vale a menor das duas.
O controle de consumo mensal fica no worker (`limite_mensal`), porque precisa
do banco para ser atômico entre vários workers.
"""

from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx

from app.fontes.base import CampoEncontrado, DadosLead, Fonte
from app.utils.retry import TIMEOUT_PADRAO, LimitadorTaxa, politica_http, verificar_resposta

_AUSENTE = object()

# Presets de provedores conhecidos. Cada valor pode ser sobrescrito pela
# variável PROVEDOR_DOMINIO_* correspondente; com o preset "personalizado"
# a URL e o mapa de campos são obrigatórios.
PRESETS: dict[str, dict] = {
    # https://docs.apollo.io/reference/organization-enrichment
    # GET /api/v1/organizations/enrich?domain=..., header x-api-key, 1 crédito
    # por empresa. Testado com chave real em 2026-11-27: domínio desconhecido
    # responde 200 com corpo `{}` (vira "sem dados") e também conta no uso.
    "apollo": {
        "nome": "apollo",
        "url": "https://api.apollo.io/api/v1/organizations/enrich?domain={dominio}",
        "header_auth": "x-api-key",
        "prefixo_auth": "",
        # Limites vistos nos headers x-rate-limit-* da conta de teste: 50 por
        # minuto, 200 por hora e 600 por dia (variam por plano). A janela de
        # 1 hora é a que mais restringe com carga contínua; picos acima de 50 por
        # minuto recebem 429 e são repetidos com backoff.
        "limite_taxa": 200,
        "janela_taxa_segundos": 3600.0,
        "mapa_campos": {
            "nome_empresa": "organization.name",
            "site": "organization.website_url",
            "linkedin_empresa": "organization.linkedin_url",
            "setor": "organization.industry",
            "num_funcionarios": "organization.estimated_num_employees",
            "receita_anual": "organization.annual_revenue",
            "ano_fundacao": "organization.founded_year",
            "descricao": "organization.short_description",
            "endereco": "organization.street_address",
            "municipio": "organization.city",
            "uf": "organization.state",
            "cep": "organization.postal_code",
            "pais": "organization.country",
        },
    },
    "personalizado": {
        "nome": "provedor_dominio",
        "url": None,
        "header_auth": "Authorization",
        "prefixo_auth": "Bearer ",
        "limite_taxa": 2,
        "janela_taxa_segundos": 1.0,
        "mapa_campos": None,
    },
}


def valor_no_caminho(dados: object, caminho: str) -> object:
    """Valor de `a.b.0.c` dentro do JSON, ou None se algum trecho faltar."""
    atual = dados
    for parte in caminho.split("."):
        if isinstance(atual, dict):
            atual = atual.get(parte, _AUSENTE)
        elif isinstance(atual, list) and parte.isdigit() and int(parte) < len(atual):
            atual = atual[int(parte)]
        else:
            return None
        if atual is _AUSENTE:
            return None
    return atual


def _texto(valor: object) -> str | None:
    if valor is None or isinstance(valor, (dict, list)):
        return None
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))  # a Apollo devolve annual_revenue como 150000000.0
    texto = str(valor).strip()
    return texto or None


def _confianca(dados: dict, caminho: str | None, teto: Decimal) -> Decimal:
    if not caminho:
        return teto
    try:
        informada = Decimal(str(valor_no_caminho(dados, caminho)))
    except (InvalidOperation, ValueError):
        return teto
    if not informada.is_finite():
        return teto
    return min(max(informada, Decimal("0")), teto).quantize(Decimal("0.01"))


def extrair_campos(
    dados: dict, mapa: dict[str, str], confianca: Decimal, caminho_confianca: str | None = None
) -> list[CampoEncontrado]:
    """Converte a resposta do provedor em campos internos, segundo o mapa."""
    nivel = _confianca(dados, caminho_confianca, confianca)
    campos = []
    for campo, caminho in mapa.items():
        valor = _texto(valor_no_caminho(dados, caminho))
        if valor:
            campos.append(CampoEncontrado(campo=campo, valor=valor, confianca=nivel))
    return campos


class FonteProvedorDominio(Fonte):
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        nome: str,
        url: str,
        api_key: str,
        mapa_campos: dict[str, str],
        limite_mensal: int,
        confianca: Decimal = Decimal("0.85"),
        caminho_confianca: str | None = None,
        header_auth: str = "Authorization",
        prefixo_auth: str = "Bearer ",
        tentativas: int = 3,
        limitador: LimitadorTaxa | None = None,
    ) -> None:
        self.nome = nome
        self.limite_mensal = limite_mensal
        self._http = http
        self._url = url
        self._headers = {header_auth: f"{prefixo_auth}{api_key}"}
        self._mapa = dict(mapa_campos)
        self._confianca = confianca
        self._caminho_confianca = caminho_confianca
        self._tentativas = tentativas
        self._limitador = limitador or LimitadorTaxa(limite=2, janela=1.0)

    def aplicavel(self, dados: DadosLead) -> bool:
        return dados.dominio is not None

    async def consultar(self, dados: DadosLead) -> list[CampoEncontrado]:
        if dados.dominio is None:
            return []
        url = self._url.replace("{dominio}", quote(dados.dominio, safe=""))
        async for tentativa in politica_http(self._tentativas):
            with tentativa:
                await self._limitador.aguardar()
                resposta = await self._http.get(url, headers=self._headers, timeout=TIMEOUT_PADRAO)
                if resposta.status_code in (204, 404):
                    return []
                verificar_resposta(resposta)
        corpo = resposta.json()
        if not isinstance(corpo, dict):
            return []
        return extrair_campos(corpo, self._mapa, self._confianca, self._caminho_confianca)
