"""Configure direct AUX ACHP-H08 TCP reception."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from .connection import validate_connection
from .const import DOMAIN, NAME, DEFAULT_HOST, DEFAULT_PORT

class AuxConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            if not host:
                errors[CONF_HOST] = "invalid_host"
            elif any(entry.data[CONF_HOST].lower() == host.lower() and
                     entry.data[CONF_PORT] == port for entry in self._async_current_entries()):
                return self.async_abort(reason="already_configured")
            else:
                try:
                    await validate_connection(host, port)
                except (OSError, TimeoutError, ConnectionError):
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(title=NAME, data={CONF_HOST: host, CONF_PORT: port})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        }), errors=errors)
