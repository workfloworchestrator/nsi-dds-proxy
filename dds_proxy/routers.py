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
"""Data endpoints. Each returns a full collection from DDS, 502 on upstream failure."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Request

from dds_proxy.config import settings
from dds_proxy.dds_client import fetch_sdps, fetch_stps, fetch_switching_services, fetch_topologies
from dds_proxy.models import ServiceDemarcationPoint, ServiceTerminationPoint, SwitchingService, Topology

T = TypeVar("T")

router = APIRouter()


async def _fetch_or_502(fetch: Callable[..., Awaitable[list[T]]], request: Request) -> list[T]:
    """Run a DDS fetch, mapping any upstream failure to a 502."""
    try:
        return await fetch(client=request.app.state.http_client, dds_base_url=settings.dds_base_url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"DDS fetch failed: {exc}") from exc


@router.get("/topologies", response_model=list[Topology], tags=["topologies"])
async def get_topologies(request: Request) -> list[Topology]:
    """Return all topologies found in the DDS."""
    return await _fetch_or_502(fetch_topologies, request)


@router.get("/switching-services", response_model=list[SwitchingService], tags=["switching-services"])
async def get_switching_services(request: Request) -> list[SwitchingService]:
    """Return all switching services found across all DDS topologies."""
    return await _fetch_or_502(fetch_switching_services, request)


@router.get(
    "/service-termination-points",
    response_model=list[ServiceTerminationPoint],
    tags=["service-termination-points"],
)
async def get_service_termination_points(request: Request) -> list[ServiceTerminationPoint]:
    """Return all STPs attached to switching services across all DDS topologies."""
    return await _fetch_or_502(fetch_stps, request)


@router.get(
    "/service-demarcation-points",
    response_model=list[ServiceDemarcationPoint],
    tags=["service-demarcation-points"],
)
async def get_service_demarcation_points(request: Request) -> list[ServiceDemarcationPoint]:
    """Return all SDPs (matched STP pairs) across all DDS topologies."""
    return await _fetch_or_502(fetch_sdps, request)
