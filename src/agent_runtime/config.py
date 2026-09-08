"""Environment-backed runtime configuration with safe defaults."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    """Runtime settings loaded from environment variables or an optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://agent_runtime:agent_runtime@localhost:5432/agent_runtime"
    )
    model_provider: str = "openai_compatible"
    model_name: str = "replace-me"
    model_base_url: str | None = None
    model_api_key: SecretStr | None = None
    fake_model: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "agent-runtime-kit"
    otel_capture_content: bool = False
    max_tool_iterations: int = Field(default=3, ge=1, le=20)
    tool_timeout_seconds: float = Field(default=10, gt=0, le=300)
    invocation_timeout_seconds: float = Field(default=60, gt=0, le=600)
    shutdown_grace_seconds: float = Field(default=20, gt=0, le=120)
    max_request_body_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_pool_timeout_seconds: float = Field(default=5, gt=0, le=60)
