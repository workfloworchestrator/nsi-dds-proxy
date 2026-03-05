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
from pydantic import BaseModel


class Lifetime(BaseModel):
    start: str
    end: str


class Topology(BaseModel):
    id: str
    version: str
    name: str
    lifetime: Lifetime

    model_config = {"populate_by_name": True}


class SwitchingService(BaseModel):
    id: str
    encoding: str
    label_swapping: bool
    label_type: str
    topology_id: str

    model_config = {
        "populate_by_name": True,
        "alias_generator": lambda field: {
            "label_swapping": "labelSwapping",
            "label_type": "labelType",
            "topology_id": "topologyId",
        }.get(field, field),
    }


class ServiceTerminationPoint(BaseModel):
    id: str
    name: str
    capacity: int
    label_group: str
    switching_service_id: str

    model_config = {
        "populate_by_name": True,
        "alias_generator": lambda field: {
            "label_group": "LabelGroup",
            "switching_service_id": "SwitchingServiceId",
        }.get(field, field),
    }


class ServiceDemarcationPoint(BaseModel):
    stp_a_id: str
    stp_z_id: str

    model_config = {
        "populate_by_name": True,
        "alias_generator": lambda field: {
            "stp_a_id": "StpAId",
            "stp_z_id": "StpZId",
        }.get(field, field),
    }
