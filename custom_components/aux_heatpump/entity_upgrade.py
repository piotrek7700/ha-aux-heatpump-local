"""Enable telemetry that was disabled by earlier integration defaults."""
from homeassistant.helpers import entity_registry as er


def enable_default_entities(hass, entry):
    registry = er.async_get(hass)
    unique_ids = {f"{entry.entry_id}_{key}" for key in ("fan_speed", "target_frequency")}
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (entity.platform == "aux_heatpump"
                and entity.unique_id in unique_ids
                and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION):
            registry.async_update_entity(entity.entity_id, disabled_by=None)
