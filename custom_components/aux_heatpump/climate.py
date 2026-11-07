"""CO water-temperature control using the existing confirmed E2 controls."""
import math
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .names import AuxNames
from .const import DOMAIN, NAME
from .control import LIMITS
from .errors import localized_error


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AuxHeatingClimate(hass.data[DOMAIN][entry.entry_id], entry)])


class AuxHeatingClimate(AuxNames, CoordinatorEntity, ClimateEntity):
    _aux_name_domain = "climate"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "heating"
    _attr_icon = "mdi:radiator"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    if not hasattr(ClimateEntity, "native_temperature_unit"):
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_min_temp, _attr_max_temp = LIMITS["heating_setpoint"]
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE
                                | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF)

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_heating_climate"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return (self.coordinator.has_fresh_value("heating_setpoint")
                and self.coordinator.has_fresh_value("heating_enabled"))

    @property
    def native_current_temperature(self):
        if self.coordinator.has_fresh_value("flh_inlet"):
            return self.coordinator.data.get("flh_inlet")
        return None

    @property
    def native_target_temperature(self):
        if self.coordinator.has_fresh_value("heating_setpoint"):
            return self.coordinator.data.get("heating_setpoint")
        return None

    # Older HA releases use the legacy names. New releases handle unit
    # conversion through the native properties; do not override their getters.
    if not hasattr(ClimateEntity, "native_current_temperature"):
        current_temperature = native_current_temperature
    if not hasattr(ClimateEntity, "native_target_temperature"):
        target_temperature = native_target_temperature

    @property
    def hvac_mode(self):
        if not self.coordinator.has_fresh_value("heating_enabled"):
            return None
        return HVACMode.HEAT if self.coordinator.data["heating_enabled"] else HVACMode.OFF

    @property
    def extra_state_attributes(self):
        return {**self.coordinator.control_status("heating_setpoint"),
                "current_temperature_source": "flh_inlet",
                "temperature_description": "FLH water inlet temperature",
                "mode_description": "Heating permission; HEAT does not indicate compressor operation"}

    @staticmethod
    def _validate_mode(mode):
        if mode not in (HVACMode.OFF, HVACMode.HEAT):
            raise localized_error("heating_mode")

    async def async_set_hvac_mode(self, hvac_mode):
        self._validate_mode(hvac_mode)
        await self.coordinator.async_control("heating_enabled", hvac_mode == HVACMode.HEAT)

    async def async_set_temperature(self, **kwargs):
        value = kwargs.get(ATTR_TEMPERATURE)
        mode = kwargs.get("hvac_mode")
        if mode is not None:
            self._validate_mode(mode)
        if (isinstance(value, bool) or not isinstance(value, (float, int))
                or not math.isfinite(value) or value != int(value)
                or not self._attr_min_temp <= value <= self._attr_max_temp):
            raise localized_error("heating_range", minimum=str(self._attr_min_temp), maximum=str(self._attr_max_temp))
        # Changing the target alone must never silently enable CO or change CWU.
        await self.coordinator.async_control("heating_setpoint", value)
        if mode is not None:
            await self.async_set_hvac_mode(mode)

    async def async_turn_on(self):
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self):
        await self.async_set_hvac_mode(HVACMode.OFF)
