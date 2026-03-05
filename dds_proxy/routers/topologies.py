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
from fastapi import APIRouter, Request, HTTPException

from dds_proxy.config import get_settings
from dds_proxy.models import Topology
from dds_proxy.dds_client import fetch_topologies

router = APIRouter(tags=["topologies"])


@router.get("/topologies", response_model=list[Topology])
async def get_topologies(request: Request) -> list[Topology]:
    """Return all topologies found in the DDS."""
    settings = get_settings()
    try:
        return await fetch_topologies(
            client=request.app.state.http_client,
            dds_base_url=settings.dds_base_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DDS fetch failed: {exc}") from exc
