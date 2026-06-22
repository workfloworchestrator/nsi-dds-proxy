# Copyright 2026 SURF.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import json
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or ``dds_proxy.env``.

    All fields can be overridden by setting the corresponding environment
    variable (uppercased field name). Certificate fields are validated as
    existing files at startup.
    """

    dds_base_url: str = "https://your-dds-server/dds"
    cache_ttl_seconds: int = 60
    http_timeout_seconds: float = 30.0
    dds_client_cert: Path | None = None
    dds_client_key: Path | None = None
    dds_ca_bundle: Path | None = None
    log_level: str = "INFO"
    dds_proxy_host: str = "localhost"
    dds_proxy_port: int = 8000
    root_path: str = ""

    auth_enabled: bool = False
    mtls_header: str = ""
    # NoDecode suppresses pydantic-settings' pre-validator JSON decode so the
    # raw env string reaches the validator below — otherwise comma-separated and
    # empty values crash before it runs. See parse_comma_separated_groups.
    oidc_required_groups: Annotated[list[str], NoDecode] = []

    @field_validator("oidc_required_groups", mode="before")
    @classmethod
    def parse_comma_separated_groups(cls, v: object) -> object:
        """Accept JSON arrays, comma-separated strings, a single value, or empty (-> [])."""
        if not isinstance(v, str):
            return v
        if not v:
            return []
        if v.startswith("["):
            try:
                return json.loads(v)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in OIDC_REQUIRED_GROUPS: {e}") from e
        return [g.strip() for g in v.split(",") if g.strip()]

    model_config = SettingsConfigDict(env_file="dds_proxy.env", env_file_encoding="utf-8")


settings = Settings()
