"""Configuração da aplicação, lida de variáveis de ambiente (ou do arquivo .env)."""

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hubspot_token: SecretStr
    hubspot_webhook_secret: SecretStr
    webhook_url_publica: str | None = None
    janela_assinatura_segundos: int = Field(default=300, gt=0)
    # Versões de assinatura aceitas, separadas por vírgula (v1, v2, v3).
    assinaturas_aceitas: str = "v3,v1"

    database_url: str

    hubspot_url_base: str = "https://api.hubapi.com"
    brasilapi_url_base: str = "https://brasilapi.com.br/api/cnpj/v1"

    # Nomes das propriedades no HubSpot (blueprint: ajuste por conta).
    propriedade_email: str = "email"
    propriedade_cnpj: str = "cnpj"
    propriedade_origem: str = "origem_enriquecimento"
    propriedade_data: str = "data_enriquecimento"
    # Campo interno -> propriedade de contato. Campos fora do mapa são só auditados.
    mapa_propriedades: dict[str, str] = {
        "razao_social": "company",
        "municipio": "city",
        "uf": "state",
        "cep": "zip",
        "endereco": "address",
        "cnae_principal": "industry",
    }
    confianca_minima: Decimal = Field(default=Decimal("0.70"), ge=0, le=1)

    fonte_dominio_ativa: bool = False
    provedor_dominio_api_key: SecretStr | None = None
    limite_consumo_mensal_provedor: int = Field(default=1000, ge=0)

    max_tentativas: int = Field(default=5, ge=1)
    worker_intervalo_segundos: float = Field(default=5.0, gt=0)
    worker_tamanho_lote: int = Field(default=10, ge=1)
    # Tempo que um evento fica reservado para um worker antes de poder ser retomado.
    worker_reserva_segundos: int = Field(default=600, ge=30)
    log_level: str = "INFO"

    @field_validator("assinaturas_aceitas")
    @classmethod
    def _validar_assinaturas(cls, valor: str) -> str:
        versoes = {v.strip().lower() for v in valor.split(",") if v.strip()}
        if not versoes or not versoes <= {"v1", "v2", "v3"}:
            raise ValueError("ASSINATURAS_ACEITAS deve listar v1, v2 e/ou v3")
        return ",".join(sorted(versoes))

    @property
    def versoes_assinatura(self) -> frozenset[str]:
        return frozenset(self.assinaturas_aceitas.split(","))


@lru_cache
def get_settings() -> Settings:
    return Settings()
