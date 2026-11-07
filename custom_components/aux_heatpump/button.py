"""Explicit read-only configuration refresh."""
from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .names import AuxNames
from .const import DOMAIN, NAME


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AuxRefreshButton(hass.data[DOMAIN][entry.entry_id], entry)])


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
