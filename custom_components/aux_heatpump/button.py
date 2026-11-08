"""Explicit read-only configuration refresh."""
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .names import AuxNames
from .const import DOMAIN, NAME
from homeassistant.const import EntityCategory


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AuxRefreshButton(coordinator, entry),
                        AuxRecordingButton(coordinator, entry, True),
                        AuxRecordingButton(coordinator, entry, False),
                        AuxRecordingButton(coordinator, entry, None)])


class AuxRefreshButton(AuxNames, CoordinatorEntity, ButtonEntity):
    _aux_name_domain = "button"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "refresh_parameters"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh_parameters"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return self.coordinator.connected

    async def async_press(self):
        await self.coordinator.async_refresh_parameters()


class AuxRecordingButton(AuxNames, CoordinatorEntity, ButtonEntity):
    _aux_name_domain = "button"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator, entry, start):
        super().__init__(coordinator)
        self.start = start
        self._attr_translation_key = ("start_frame_recording" if start is True else
                                      "stop_frame_recording" if start is False else "download_frame_recording")
        self._attr_unique_id = f"{entry.entry_id}_{self._attr_translation_key}"
        self._attr_icon = ("mdi:record-rec" if start is True else
                           "mdi:stop-circle-outline" if start is False else "mdi:download")
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return True  # May start while disconnected; stop must always remain usable.

    async def async_press(self):
        if self.start is True:
            await self.coordinator.async_start_recording()
        elif self.start is False:
            await self.coordinator.async_stop_recording()
        else:
            await self.coordinator.async_download_recording()
