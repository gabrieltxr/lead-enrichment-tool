"""Leitura e escrita no HubSpot (API CRM v3).

Só escreve o que o projeto permite: propriedades de enriquecimento do contato
(e da empresa, se usado), as propriedades customizadas de controle e notas.
"""

from datetime import UTC, datetime

import httpx

from app.utils.retry import TIMEOUT_PADRAO, LimitadorTaxa, politica_http, verificar_resposta

URL_PADRAO = "https://api.hubapi.com"
# Tipo de associação padrão nota -> contato (HUBSPOT_DEFINED).
ASSOCIACAO_NOTA_CONTATO = 202


class ClienteHubSpot:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token: str,
        url_base: str = URL_PADRAO,
        tentativas: int = 4,
        limitador: LimitadorTaxa | None = None,
    ) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {token}"}
        self._url_base = url_base.rstrip("/")
        self._tentativas = tentativas
        # Starter: 100 requisições a cada 10 s por app. Deixamos folga.
        self._limitador = limitador or LimitadorTaxa(limite=90, janela=10.0)

    async def _requisitar(self, metodo: str, caminho: str, **kwargs) -> httpx.Response:
        async for tentativa in politica_http(self._tentativas):
            with tentativa:
                await self._limitador.aguardar()
                resposta = await self._http.request(
                    metodo, self._url_base + caminho, headers=self._headers,
                    timeout=TIMEOUT_PADRAO, **kwargs,
                )
                verificar_resposta(resposta)
        return resposta

    async def ler_contato(self, contact_id: str, propriedades: list[str]) -> dict[str, str | None]:
        resposta = await self._requisitar(
            "GET", f"/crm/v3/objects/contacts/{contact_id}",
            params={"properties": ",".join(sorted(set(propriedades)))},
        )
        return resposta.json().get("properties", {})

    async def atualizar_contato(self, contact_id: str, propriedades: dict[str, str]) -> None:
        await self._requisitar(
            "PATCH", f"/crm/v3/objects/contacts/{contact_id}", json={"properties": propriedades}
        )

    async def ler_empresa(self, company_id: str, propriedades: list[str]) -> dict[str, str | None]:
        resposta = await self._requisitar(
            "GET", f"/crm/v3/objects/companies/{company_id}",
            params={"properties": ",".join(sorted(set(propriedades)))},
        )
        return resposta.json().get("properties", {})

    async def atualizar_empresa(self, company_id: str, propriedades: dict[str, str]) -> None:
        await self._requisitar(
            "PATCH", f"/crm/v3/objects/companies/{company_id}", json={"properties": propriedades}
        )

    async def criar_nota(self, contact_id: str, corpo: str, quando: datetime | None = None) -> str:
        quando = quando or datetime.now(UTC)
        payload = {
            "properties": {
                "hs_timestamp": quando.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "hs_note_body": corpo,
            },
            "associations": [{
                "to": {"id": contact_id},
                "types": [{
                    "associationCategory": "HUBSPOT_DEFINED",
                    "associationTypeId": ASSOCIACAO_NOTA_CONTATO,
                }],
            }],
        }
        resposta = await self._requisitar("POST", "/crm/v3/objects/notes", json=payload)
        return str(resposta.json()["id"])

    async def listar_propriedades_contato(self) -> set[str]:
        resposta = await self._requisitar("GET", "/crm/v3/properties/contacts")
        return {p["name"] for p in resposta.json().get("results", [])}

    async def criar_propriedade_contato(self, definicao: dict) -> None:
        await self._requisitar("POST", "/crm/v3/properties/contacts", json=definicao)
