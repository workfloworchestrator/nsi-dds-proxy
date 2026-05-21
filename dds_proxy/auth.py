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
from typing import Any

import structlog
from fastapi import HTTPException, Request

from dds_proxy.config import settings

log = structlog.get_logger(__name__)

_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}

_USER_HEADER = "X-Auth-Request-Email"
_GROUPS_HEADER = "X-Auth-Request-Groups"
_CLIENT_DN_HEADER = "X-Client-DN"


def _parse_groups(header_value: str) -> list[str]:
    """Parse oauth2-proxy's X-Auth-Request-Groups value into a list of group strings."""
    return [g.strip() for g in header_value.replace(",", " ").split() if g.strip()]


def check_groups(user_groups: list[str], required_groups: list[str]) -> list[str]:
    """Return the sorted intersection of ``required_groups`` and ``user_groups``."""
    return sorted(set(required_groups).intersection(user_groups))


async def get_authenticated_user(request: Request) -> dict[str, Any] | None:
    """FastAPI dependency that authorises a request from the edge-proxy identity headers.

    When ``auth_enabled`` is ``False``, all requests pass through.
    When ``True``, the request must arrive with one of:

    * oauth2-proxy's identity headers (``X-Auth-Request-Email`` and
      ``X-Auth-Request-Groups``), set by Traefik's ForwardAuth middleware
      on the portal route, or
    * the mTLS header (``settings.mtls_header``) set by the mTLS auth
      subrequest service on the machine-client route.

    If neither is present, the request is rejected with 401.
    """
    if not settings.auth_enabled:
        return None

    path = request.url.path

    user_id = request.headers.get(_USER_HEADER, "").strip()
    if user_id:
        user_groups = _parse_groups(request.headers.get(_GROUPS_HEADER, ""))
        matched = check_groups(user_groups, settings.oidc_required_groups)
        if settings.oidc_required_groups and not matched:
            log.warning(
                "Insufficient group membership",
                user=user_id,
                user_groups=user_groups,
                required_groups=settings.oidc_required_groups,
                path=path,
            )
            raise HTTPException(status_code=403, detail="Insufficient group membership")
        log.info("OIDC user authenticated", user=user_id, matched_groups=matched, path=path)
        return {"sub": user_id, "groups": user_groups}

    if settings.mtls_header:
        mtls_value = request.headers.get(settings.mtls_header, "").strip()
        if mtls_value:
            client_dn = request.headers.get(_CLIENT_DN_HEADER, "unknown")
            log.info("mTLS authentication verified", client_dn=client_dn, path=path)
            return None

    log.warning("No valid authentication credentials found", path=path)
    raise HTTPException(status_code=401, detail="Authentication required", headers=_WWW_AUTHENTICATE)
