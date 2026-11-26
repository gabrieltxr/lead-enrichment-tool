"""Configuração da aplicação, lida de variáveis de ambiente (ou do arquivo .env)."""

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

    fonte_dominio_ativa: bool = False
    provedor_dominio_api_key: SecretStr | None = None
    limite_consumo_mensal_provedor: int = Field(default=1000, ge=0)

    max_tentativas: int = Field(default=5, ge=1)
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
