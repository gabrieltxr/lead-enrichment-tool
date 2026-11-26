"""Configuração da aplicação, lida de variáveis de ambiente (ou do arquivo .env)."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    hubspot_token: SecretStr
    hubspot_webhook_secret: SecretStr
    webhook_url_publica: str | None = None
    janela_assinatura_segundos: int = Field(default=300, gt=0)

    database_url: str

    fonte_dominio_ativa: bool = False
    provedor_dominio_api_key: SecretStr | None = None
    limite_consumo_mensal_provedor: int = Field(default=1000, ge=0)

    max_tentativas: int = Field(default=5, ge=1)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
