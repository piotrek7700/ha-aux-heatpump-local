"""Confirmed zone heat-emitter choices (B6 selectors 0030 and 0031)."""
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .names import AuxNames, NAME_LANGUAGES
from .const import DOMAIN, NAME
from .parameters import SELECTS


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AuxNameLanguageSelect(coordinator, entry),
                        *(AuxEmitterSelect(coordinator, entry, p) for p in SELECTS)])


class AuxEmitterSelect(AuxNames, CoordinatorEntity, SelectEntity):
    _aux_name_domain = "select"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:radiator"

    def __init__(self, coordinator, entry, parameter):
        super().__init__(coordinator)
        self.key = parameter.key
        self._attr_translation_key = parameter.key
        self._attr_unique_id = f"{entry.entry_id}_b6_{parameter.key}"
        self._attr_options = [label for _, label in parameter.options]
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return self.coordinator.has_fresh_value(self.key)

    @property
    def current_option(self):
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        return self.coordinator.control_status(self.key)

    async def async_select_option(self, option):
        await self.coordinator.async_control(self.key, option)


class AuxNameLanguageSelect(AuxNames, CoordinatorEntity, SelectEntity):
    """Local presentation setting; never sends any pump traffic."""
    _aux_name_domain = "select"
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True
    _attr_entity_category = None  # Show beside the other device controls.
    _attr_translation_key = "integration_language"
    _attr_icon = "mdi:translate"
    _attr_options = list(NAME_LANGUAGES)

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_integration_language"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return True

    @property
    def current_option(self):
        return self.coordinator.name_language

    @property
    def extra_state_attributes(self):
        return {"effective_language": self.coordinator.effective_name_language,
                "scope": "entity_names_only", "shared_across_users": True}

    async def async_select_option(self, option):
        await self.coordinator.async_set_name_language(option)
