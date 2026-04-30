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
import asyncio
import hashlib
import time
from typing import Any

import httpx
import jwt
import structlog
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from dds_proxy.config import settings

log = structlog.get_logger(__name__)

_BEARER_PREFIX = "Bearer "
_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


class OIDCProvider:
    """Manages JWKS key retrieval and userinfo lookups for OIDC token validation."""

    def __init__(
        self,
        jwks_uri: str,
        userinfo_uri: str,
        http_client: httpx.AsyncClient,
        *,
        cache_lifespan: int = 300,
        userinfo_cache_ttl: int = 60,
    ) -> None:
        """Initialize with JWKS and userinfo endpoints."""
        self._jwk_client = PyJWKClient(
            jwks_uri,
            cache_jwk_set=True,
            lifespan=cache_lifespan,
            cache_keys=True,
            max_cached_keys=16,
        )
        self._userinfo_uri = userinfo_uri
        self._http_client = http_client
        self._userinfo_cache_ttl = userinfo_cache_ttl
        self._userinfo_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def get_signing_key(self, token: str) -> jwt.PyJWK:
        """Retrieve the signing key for a JWT, using cached JWKS when possible."""
        signing_key = await asyncio.to_thread(self._jwk_client.get_signing_key_from_jwt, token)
        log.debug("Resolved signing key", kid=signing_key.key_id)
        return signing_key

    async def fetch_userinfo(self, access_token: str) -> dict[str, Any]:
        """Fetch user claims from the OIDC userinfo endpoint, with TTL caching."""
        cache_key = hashlib.sha256(access_token.encode()).hexdigest()
        cached = self._userinfo_cache.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < self._userinfo_cache_ttl:
            log.debug("Userinfo cache hit")
            return cached[1]

        log.debug("Fetching userinfo", userinfo_uri=self._userinfo_uri)
        response = await self._http_client.get(
            self._userinfo_uri,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        userinfo: dict[str, Any] = response.json()

        self._userinfo_cache[cache_key] = (time.monotonic(), userinfo)
        self._evict_expired_cache_entries()
        log.debug("Userinfo fetched and cached", sub=userinfo.get("sub", "unknown"))
        return userinfo

    def _evict_expired_cache_entries(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._userinfo_cache.items() if (now - ts) >= self._userinfo_cache_ttl]
        for k in expired:
            del self._userinfo_cache[k]


async def validate_token(token: str, oidc_provider: OIDCProvider) -> dict[str, Any]:
    """Validate JWT signature and standard claims, returning the decoded payload."""
    signing_key = await oidc_provider.get_signing_key(token)
    payload: dict[str, Any] = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=settings.oidc_audience,
        issuer=settings.oidc_issuer,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )
    return payload


def check_groups(
    userinfo: dict[str, Any], required_groups: list[str], group_claim: str
) -> list[str]:
    """Verify that the user belongs to at least one of the required groups."""
    user_groups = userinfo.get(group_claim, [])
    if isinstance(user_groups, str):
        user_groups = [user_groups]
    sub = userinfo.get("sub", "unknown")
    log.debug(
        "Checking group membership",
        sub=sub,
        user_groups=user_groups,
        required_groups=required_groups,
        claim=group_claim,
    )
    matched = sorted(set(required_groups).intersection(user_groups))
    if not matched:
        log.warning("Insufficient group membership", sub=sub, user_groups=user_groups, required_groups=required_groups)
        raise HTTPException(status_code=403, detail="Insufficient group membership")
    return matched


async def get_authenticated_user(request: Request) -> dict[str, Any] | None:
    """FastAPI dependency that validates the JWT and optionally checks group membership.

    When ``auth_enabled`` is ``False``, all requests pass through.
    When ``True``, the request must be authenticated via OIDC (JWT) or mTLS
    (header set by the auth service). If neither succeeds, the request is rejected.
    """
    if not settings.auth_enabled:
        return None

    path = request.url.path

    # --- OIDC path ---
    if settings.oidc_issuer:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix(_BEARER_PREFIX).strip() if auth_header.startswith(_BEARER_PREFIX) else ""

        if token:
            log.info("Using token from Authorization header", path=path)
        else:
            token = request.headers.get("X-Auth-Request-Access-Token", "").strip()
            if token:
                log.info("Using access token from X-Auth-Request-Access-Token header", path=path)

        if token:
            oidc_provider: OIDCProvider = request.app.state.oidc_provider

            try:
                payload = await validate_token(token, oidc_provider)
            except jwt.ExpiredSignatureError as exc:
                log.warning("Token expired", path=path, error=str(exc))
                raise HTTPException(status_code=401, detail="Token expired", headers=_WWW_AUTHENTICATE) from exc
            except jwt.InvalidAudienceError as exc:
                log.warning("Invalid audience in token", path=path, error=str(exc))
                raise HTTPException(status_code=401, detail="Invalid audience", headers=_WWW_AUTHENTICATE) from exc
            except jwt.InvalidIssuerError as exc:
                log.warning("Invalid issuer in token", path=path, error=str(exc))
                raise HTTPException(status_code=401, detail="Invalid issuer", headers=_WWW_AUTHENTICATE) from exc
            except jwt.PyJWTError as exc:
                log.warning("Invalid token", path=path, error=str(exc))
                raise HTTPException(
                    status_code=401, detail=f"Invalid token: {exc}", headers=_WWW_AUTHENTICATE
                ) from exc

            sub = payload.get("sub", "unknown")
            iss = payload.get("iss", "unknown")
            log.info("Token validated", sub=sub, iss=iss, path=path)
            log.debug("Token claims", payload=payload)

            request.state.user = payload

            if settings.oidc_required_groups:
                access_token = request.headers.get("X-Auth-Request-Access-Token", "")
                if not access_token:
                    log.warning("Missing access token for group lookup", sub=sub, path=path)
                    raise HTTPException(
                        status_code=401, detail="Missing access token for group lookup", headers=_WWW_AUTHENTICATE
                    )

                try:
                    userinfo = await oidc_provider.fetch_userinfo(access_token)
                except httpx.HTTPError as exc:
                    log.error("Userinfo fetch failed", sub=sub, error=str(exc))
                    raise HTTPException(status_code=502, detail=f"Userinfo fetch failed: {exc}") from exc

                log.debug("Userinfo received", sub=sub, userinfo=userinfo)
                matched = check_groups(userinfo, settings.oidc_required_groups, settings.oidc_group_claim)
                email = userinfo.get("email", "unknown")
                log.info("Group authorization granted", sub=sub, email=email, matched_groups=matched, path=path)

            return payload

    # --- mTLS path ---
    if settings.mtls_header:
        mtls_value = request.headers.get(settings.mtls_header, "").strip()
        if mtls_value:
            client_dn = request.headers.get("X-Client-DN", "unknown")
            log.info("mTLS authentication verified", client_dn=client_dn, path=path)
            return None

    # --- Neither succeeded ---
    log.warning("No valid authentication credentials found", path=path)
    raise HTTPException(status_code=401, detail="Authentication required", headers=_WWW_AUTHENTICATE)
