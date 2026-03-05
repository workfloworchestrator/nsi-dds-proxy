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
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """DDS Proxy Settings."""

    dds_base_url: str = "https://dds.nsi.anaeng.global/dds"
    cache_ttl_seconds: int = 60
    http_timeout_seconds: float = 30.0
    dds_client_cert: str = "client-certificate.pem"
    dds_client_key: str = "client-private-key.pem"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file="dds_proxy.env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings."""
    return Settings()
