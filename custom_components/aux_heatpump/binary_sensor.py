"""Reported AUX component states; read-only, no control commands."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .names import AuxNames
from .const import DOMAIN, NAME
from homeassistant.const import EntityCategory
from .protocol import BINARY_FIELDS

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([*(AuxBinarySensor(coordinator, entry, field) for field in BINARY_FIELDS),
                        AuxRecordingStatus(coordinator, entry)])

class AuxBinarySensor(AuxNames, CoordinatorEntity, BinarySensorEntity):
    _aux_name_domain = "binary_sensor"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, entry, field):
        super().__init__(coordinator)
        self.field = field
        self._attr_translation_key = field.key
        self._attr_unique_id = f"{entry.entry_id}_{field.key}"
        self._attr_entity_registry_enabled_default = field.enabled
        if field.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if field.key == "defrost_candidate":
            self._attr_icon = "mdi:snowflake"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return self.coordinator.connected and self.field.key in self.coordinator.data

    @property
    def extra_state_attributes(self):
        if self.field.key == "defrost_candidate":
            return {"source": "D32 & 0x20", "frame_prefix": "7e f1 f1 12 2d 12 12",
                    "offset_base": 0, "interpretation": "candidate defrost status",
                    "validation": "one cycle confirmed by HMI snowflake; repeat validation needed"}
        if self.field.key == "dhw_enabled":
            return {"source": "B7 & 0x01", "interpretation": "DHW permission; empirical mapping"}
        if self.field.key == "dhw_activity_d31":
            return {"source": "D31 & 0x40", "interpretation": "meaning unconfirmed; separate from permission"}
        return None

    @property
    def is_on(self):
        return self.coordinator.data.get(self.field.key)


class AuxRecordingStatus(AuxNames, CoordinatorEntity, BinarySensorEntity):
    _aux_name_domain = "binary_sensor"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"written_frames", "dropped_frames", "queued_frames",
        "last_frame_at", "last_file", "tcp_connected", "download_archive"})
    _attr_translation_key = "frame_recording"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:record-rec"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_frame_recording"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.coordinator.recorder.active

    @property
    def extra_state_attributes(self):
        return {**self.coordinator.recorder.attributes(),
                "tcp_connected": self.coordinator.connected}
