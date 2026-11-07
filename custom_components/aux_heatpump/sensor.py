"""Sensors for mapped AUX ACHP-H08 telemetry."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import EntityCategory
from .names import AuxNames
from .const import DOMAIN, NAME
from .protocol import FIELDS, TEXT_FIELDS

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([*(AuxSensor(coordinator, entry, field) for field in (*FIELDS, *TEXT_FIELDS)),
                        AuxLastE2Sensor(coordinator, entry)])

class AuxSensor(AuxNames, CoordinatorEntity, SensorEntity):
    _aux_name_domain = "sensor"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, field):
        super().__init__(coordinator)
        self.field = field
        self._attr_translation_key = field.key
        self._attr_unique_id = f"{entry.entry_id}_{field.key}"
        self._attr_native_unit_of_measurement = field.unit
        self._attr_entity_registry_enabled_default = field.enabled
        if field.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if field.key == "pressure_estimated":
            self._attr_suggested_display_precision = 0
            self._attr_extra_state_attributes = {
                "interpretation": "empirical approximation, not confirmed by a manufacturer specification",
                "source": "A7–A8 Int16BE",
                "validated_raw_range": "-70..630",
                "validated_hmi_range_kpa": "536..3984",
                "low_range_calibration": "raw -70 = 536 kPa; interpolation to raw 70",
                "raw_diagnostic_encoding": "pressure_raw remains UInt16BE",
            }
        elif "frequency" in field.key:
            self._attr_extra_state_attributes = {
                "interpretation": "candidate; frequency field roles require further verification",
                "source": f"D{field.offset}–D{field.offset + 1} UInt16BE / 10",
            }
        if field in TEXT_FIELDS or field.key in ("heating_setpoint", "dhw_setpoint", "target_frequency"):
            self._attr_state_class = None
        if field.kind:
            self._attr_device_class = SensorDeviceClass(field.kind)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                            name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return (self.coordinator.connected
                and self.coordinator.data.get(self.field.key) is not None)

    @property
    def native_value(self):
        return self.coordinator.data.get(self.field.key)


class AuxLastE2Sensor(AuxNames, CoordinatorEntity, SensorEntity):
    """Time of the last valid E2 observed on the bus, retained across restarts."""
    _aux_name_domain = "sensor"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "last_e2_received"
    _attr_device_class = SensorDeviceClass("timestamp")
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:clock-check-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_last_e2_received"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                            name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        # A historical timestamp remains useful even while the bridge is offline.
        return True

    @property
    def native_value(self):
        return self.coordinator.settings_last_received

    @property
    def extra_state_attributes(self):
        return {"template_source": self.coordinator.settings_source,
                "restore_status": self.coordinator.settings_restore_status,
                "tcp_connected": self.coordinator.connected}
