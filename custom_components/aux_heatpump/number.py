"""Writable CO and CWU setpoints, confirmed by pump telemetry."""
from homeassistant.components.number import NumberEntity, NumberDeviceClass, NumberMode
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .names import AuxNames
from .const import DOMAIN, NAME
from .control import LIMITS
from .parameters import NUMBERS
from homeassistant.const import EntityCategory


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AuxSetpoint(coordinator, entry, "heating_setpoint", "Temperatura zadana CO"),
                        AuxSetpoint(coordinator, entry, "dhw_setpoint", "Temperatura zadana CWU")]
                       + [AuxParameterNumber(coordinator, entry, p) for p in NUMBERS])


class AuxSetpoint(AuxNames, CoordinatorEntity, NumberEntity):
    _aux_name_domain = "number"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_unit_of_measurement = "°C"
    _attr_native_step = 1
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry, key, name):
        super().__init__(coordinator)
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}_control"
        self._attr_native_min_value, self._attr_native_max_value = LIMITS[key]
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        # Show the current setpoint even while a template/other status is pending.
        # A write still goes through the coordinator's full readiness checks.
        return self.coordinator.has_fresh_value(self.key)

    @property
    def extra_state_attributes(self):
        return self.coordinator.control_status(self.key)

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key)

    async def async_set_native_value(self, value):
        await self.coordinator.async_control(self.key, value)


class AuxParameterNumber(AuxSetpoint):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, parameter):
        # Keep existing setpoint IDs untouched, with a separate namespace for B6.
        CoordinatorEntity.__init__(self, coordinator)
        self.key = parameter.key
        self._attr_translation_key = parameter.key
        self._attr_unique_id = f"{entry.entry_id}_b6_{parameter.key}"
        self._attr_native_min_value = parameter.low
        self._attr_native_max_value = parameter.high
        self._attr_native_step = parameter.step
        self._attr_native_unit_of_measurement = parameter.unit
        self._attr_device_class = NumberDeviceClass.TEMPERATURE if parameter.unit == "°C" else None
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")
