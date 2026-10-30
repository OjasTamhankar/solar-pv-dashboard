from typing import Literal
from pydantic import BaseModel, Field, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Subsystem(BaseModel):
    id: str
    name: str
    active: bool = True
    ac_capacity_kw: float = Field(gt=0)
    inverter_count: int = Field(ge=1)
    inverter_rating_kw: float = Field(gt=0)
    module_wattage: float = Field(gt=0)
    module_count: int = Field(ge=1)
    temp_coefficient: float = Field(lt=0, gt=-0.02)
    noct_c: float = Field(gt=0, lt=100)
    inverter_efficiency: float = Field(gt=0, le=1)
    performance_ratio: float = Field(gt=0, le=1)
    tilt_deg: float = Field(ge=0, le=90)
    azimuth_deg: float = Field(ge=0, lt=360)
    modules_per_string: int = Field(ge=1)
    strings_per_inverter: int = Field(ge=1)
    vmp_v: float | None = Field(default=None, gt=0)
    imp_a: float | None = Field(default=None, gt=0)
    voc_v: float | None = Field(default=None, gt=0)
    isc_a: float | None = Field(default=None, gt=0)
    max_dc_voltage_v: float | None = Field(default=None, gt=0)
    mppt_min_v: float | None = Field(default=None, gt=0)
    mppt_max_v: float | None = Field(default=None, gt=0)
    max_dc_current_a: float | None = Field(default=None, gt=0)
    mppt_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def inverter_sizing(self):
        total = self.inverter_count * self.inverter_rating_kw
        if total + 1e-6 < self.ac_capacity_kw:
            raise ValueError("Combined inverter rating must cover subsystem AC capacity")
        if self.mppt_min_v and self.mppt_max_v and self.mppt_min_v >= self.mppt_max_v:
            raise ValueError("MPPT minimum voltage must be below maximum")
        if self.vmp_v and self.mppt_min_v and self.mppt_max_v:
            string_v = self.vmp_v * self.modules_per_string
            if not self.mppt_min_v <= string_v <= self.mppt_max_v:
                raise ValueError("String Vmp is outside the configured MPPT range")
        if self.voc_v and self.max_dc_voltage_v:
            if self.voc_v * self.modules_per_string > self.max_dc_voltage_v:
                raise ValueError("String Voc exceeds maximum DC voltage")
        if self.imp_a and self.max_dc_current_a:
            if self.imp_a * self.strings_per_inverter > self.max_dc_current_a:
                raise ValueError("Parallel string current exceeds inverter DC input limit")
        return self


class PlantConfig(BaseModel):
    plant_name: str = "Solar PV Plant"
    timezone: str = "Asia/Kolkata"
    identical_subsystems: bool = True
    calculation_model: Literal["pr", "temperature"] = "pr"
    reference_date: str = "2007-01-03"
    shared: Subsystem
    subsystems: list[Subsystem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_subsystems(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Plant timezone must be a valid IANA timezone")
        active = [x for x in self.subsystems if x.active]
        if not active:
            raise ValueError("At least one subsystem must be active")
        if len({x.id for x in self.subsystems}) != len(self.subsystems):
            raise ValueError("Subsystem IDs must be unique")
        if self.identical_subsystems:
            # The shared values are authoritative in identical mode.
            shared_fields = type(self.shared).model_fields
            self.subsystems = [x.model_copy(update={k: getattr(self.shared, k) for k in shared_fields if k not in ("id", "name", "active")}) for x in self.subsystems]
        return self

    @property
    def capacity_kw(self):
        return sum(s.ac_capacity_kw for s in self.subsystems if s.active)


def default_config():
    common = dict(ac_capacity_kw=50, inverter_count=1, inverter_rating_kw=50,
        module_wattage=550, module_count=16*5, temp_coefficient=-0.0036,
        noct_c=44, inverter_efficiency=0.987, performance_ratio=0.80,
        tilt_deg=15, azimuth_deg=180, modules_per_string=16, strings_per_inverter=5,
        vmp_v=42.10, imp_a=13.07, voc_v=50.10, isc_a=13.95,
        max_dc_voltage_v=1000, mppt_min_v=150, mppt_max_v=1000,
        max_dc_current_a=110, mppt_count=4)
    a = Subsystem(id="subsystem-a", name="Subsystem A", **common)
    b = Subsystem(id="subsystem-b", name="Subsystem B", **common)
    return PlantConfig(shared=a, subsystems=[a,b])
