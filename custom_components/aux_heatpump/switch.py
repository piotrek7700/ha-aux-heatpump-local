"""CO/CWU permissions and Fast DHW, with telemetry-confirmed state."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .names import AuxNames
from .const import DOMAIN, NAME
from .parameters import SWITCHES
from homeassistant.const import EntityCategory


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        AuxSwitch(coordinator, entry, "heating_enabled", "Zezwolenie CO",
                  "heating_enable_control", "mdi:radiator"),
        # Preserve the existing CWU unique ID and automations.
        AuxSwitch(coordinator, entry, "dhw_enabled", "Zezwolenie CWU",
                  "dhw_enable_control", "mdi:water-boiler"),
        AuxSwitch(coordinator, entry, "fast_dhw_enabled", "Fast hot water",
                  "fast_dhw_enable_control", "mdi:water-boiler"),
    ] + [AuxParameterSwitch(coordinator, entry, p) for p in SWITCHES])


class AuxSwitch(AuxNames, CoordinatorEntity, SwitchEntity):
    _aux_name_domain = "switch"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, entry, key, name, unique_suffix, icon):
        super().__init__(coordinator)
        self.key = key
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
                                           name=NAME, manufacturer="AUX", model="ACHP-H08")

    @property
    def available(self):
        return self.coordinator.has_fresh_value(self.key)

    @property
    def is_on(self):
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        return self.coordinator.control_status(self.key)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_control(self.key, True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_control(self.key, False)


class AuxParameterSwitch(AuxSwitch):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, parameter):
        super().__init__(coordinator, entry, parameter.key, parameter.name,
                         f"b6_{parameter.key}", "mdi:cog")
