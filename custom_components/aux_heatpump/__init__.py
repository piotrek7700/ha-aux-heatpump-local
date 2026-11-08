"""Direct TCP integration for the AUX ACHP-H08 heat pump."""
import asyncio
from datetime import datetime, timedelta, timezone
from time import monotonic
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.storage import Store
from .errors import localized_error
from .names import NAME_LANGUAGES, load_name_catalogs
from .connection import Receiver
from .recorder import FrameRecorder
from .entity_upgrade import enable_default_entities
from .const import DOMAIN, NAME, STALE_SECONDS, VERSION
from .control import CONTROL_KEYS, is_settings_frame, make_command
from .parameters import BY_KEY, PARAMETER_KEYS, SELECTORS, make_parameter_command, reply_payload, values_for_raw
from .parameters import READ_BLOCKS, make_parameter_read, block_reply_payload
import logging

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SWITCH, Platform.SELECT, Platform.BUTTON, Platform.CLIMATE]
COMMAND_TIMEOUT = 20
PARAMETER_REFRESH_SECONDS = 300
CONTROL_PREFLIGHT_TIMEOUT = 15

class AuxCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, logging.getLogger(__name__), name=NAME)
        self._aux_hass = hass
        self.entry_id = entry.entry_id
        self.name_language = "system"
        self.entity_name_catalogs = {}
        self.name_language_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.preferences")
        self.name_language_lock = asyncio.Lock()
        self.recording_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.recording")
        self.recording_lock = asyncio.Lock()
        capture_dir = hass.config.path("aux_heatpump_captures", entry.entry_id) if hass is not None else None
        executor = hass.async_add_executor_job if hass is not None else None
        task_factory = (lambda coro: hass.async_create_background_task(coro, "AUX frame recorder")) if hass is not None else None
        self.recorder = FrameRecorder(capture_dir, executor=executor,
                                      notify=self.recording_changed, task_factory=task_factory)
        self.data = {}
        self.seen = {}
        self.connected = False
        self.settings_template = None
        self.settings_received_at = None
        self.settings_last_received = None
        self.settings_source = None
        self.settings_restore_status = "not_loaded"
        self.settings_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.settings")
        self.saved_settings = None
        self.requires_live_settings = False
        self.resync_after_sequence = None
        self.receive_sequence = 0
        self.seen_sequence = {}
        self.last_command = None
        self.control_lock = asyncio.Lock()
        self.pending = None
        self.parameter_pending = None
        self.parameter_raw = {}
        self.parameter_live = set()
        self.parameter_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.parameters")
        self.block_pending = None
        self.parameter_refresh_task = None
        self.parameter_refresh_event = asyncio.Event()
        self.last_parameter_refresh = None
        self.last_parameter_refresh_error = None
        self.control_updates = asyncio.Event()
        self.last_preflight = None
        self.receiver = Receiver(entry.data[CONF_HOST], entry.data[CONF_PORT],
                                 self.receive, self.disconnect, self.receive_frame)
        self.task = None

    @callback
    def recording_changed(self):
        self.async_set_updated_data(dict(self.data))

    async def restore_recording(self):
        try:
            stored = await self.recording_store.async_load()
            if not isinstance(stored, dict) or not stored.get("enabled"):
                return
            expires = datetime.fromisoformat(stored["expires_at"])
            started = datetime.fromisoformat(stored["started_at"])
            if expires.tzinfo is None or started.tzinfo is None:
                return
            # Never extend a saved session or accept a deadline beyond seven days.
            now = datetime.now(timezone.utc)
            if now < expires <= now + timedelta(days=7):
                await self.recorder.start(expires_at=expires, started_at=started)
        except (OSError, ValueError, KeyError, TypeError, HomeAssistantError) as err:
            self.recorder.last_error = f"Cannot restore recording: {err}"
            _LOGGER.warning("AUX frame recording restore failed: %s", err)

    async def async_start_recording(self):
        async with self.recording_lock:
            try:
                await self.recorder.start()
                await self.recording_store.async_save({"enabled": True,
                    "started_at": self.recorder.started_at.isoformat(),
                    "expires_at": self.recorder.expires_at.isoformat()})
            except (OSError, ValueError, HomeAssistantError) as err:
                await self.recorder.stop(reason="start_error")
                self.recorder.last_error = str(err)
                self.recording_changed()
                raise localized_error("recording_failed", detail=str(err)) from err

    async def async_stop_recording(self):
        async with self.recording_lock:
            await self.recorder.stop()
            try:
                await self.recording_store.async_save({"enabled": False})
            except (OSError, HomeAssistantError) as err:
                raise localized_error("recording_failed", detail=str(err)) from err

    async def async_download_recording(self):
        try:
            await self.recorder.export()
            from homeassistant.components import persistent_notification
            from homeassistant.components.http.auth import async_sign_path
            path = f"/api/aux_heatpump/captures/{self.entry_id}"
            url = async_sign_path(self._aux_hass, path, timedelta(minutes=10))
            polish = self.effective_name_language == "pl"
            message = (f"[Pobierz ZIP z nagraniem]({url}) — link ważny 10 minut." if polish else
                       f"[Download recording ZIP]({url}) — link valid for 10 minutes.")
            persistent_notification.async_create(self._aux_hass, message,
                title="Nagranie AUX" if polish else "AUX recording",
                notification_id=f"aux_heatpump_recording_{self.entry_id}")
            self.recording_changed()
        except (OSError, ValueError, HomeAssistantError) as err:
            raise localized_error("recording_failed", detail=str(err)) from err

    @property
    def effective_name_language(self):
        if self.name_language != "system":
            return self.name_language
        system_language = getattr(getattr(self._aux_hass, "config", None), "language", "en") or "en"
        language = system_language.lower().replace("_", "-").split("-")[0]
        return language if language in ("pl", "en") else "en"

    def entity_name(self, domain, key):
        return self.entity_name_catalogs.get(self.effective_name_language, {}).get(domain, {}).get(key)

    async def restore_name_language(self):
        # File I/O runs outside the HA event loop. Properties only access memory.
        if self._aux_hass is not None:
            self.entity_name_catalogs = await self._aux_hass.async_add_executor_job(load_name_catalogs)
        else:  # Standalone tests without a running Home Assistant instance.
            self.entity_name_catalogs = load_name_catalogs()
        try:
            stored = await self.name_language_store.async_load()
            if isinstance(stored, dict) and stored.get("name_language") in NAME_LANGUAGES:
                self.name_language = stored["name_language"]
        except (OSError, ValueError, HomeAssistantError) as err:
            _LOGGER.warning("Cannot restore AUX name language; using system language: %s", err)

    async def async_set_name_language(self, language):
        if language not in NAME_LANGUAGES:
            raise localized_error("invalid_name_language")
        async with self.name_language_lock:
            try:
                await self.name_language_store.async_save({"name_language": language})
            except (OSError, HomeAssistantError) as err:
                raise localized_error("language_save_failed", detail=str(err)) from err
            self.name_language = language
            # Notify all CoordinatorEntities, including when TCP is disconnected.
            # HA updates original_name while retaining registry name overrides.
            self.async_set_updated_data(dict(self.data))

    @callback
    def receive(self, values):
        now = monotonic()
        if not self.connected:
            self.parameter_refresh_event.set()
        self.connected = True
        self.receive_sequence += 1
        self.seen_sequence.update({key: self.receive_sequence for key in values})
        self.seen.update({key: now for key in values})
        data = {**self.data, **values}
        if (self.resync_after_sequence is not None
                and all(data.get(k) is not None and self.seen_sequence.get(k, -1) > self.resync_after_sequence
                        for k in CONTROL_KEYS)):
            self.resync_after_sequence = None
            if self.saved_settings is not None:
                self.saved_settings = {**self.saved_settings, "requires_live_settings": False,
                                       "needs_telemetry_resync": False}
                self.settings_store.async_delay_save(lambda: self.saved_settings, 1)
            _LOGGER.info("AUX control telemetry resynchronized; cached E2 remains usable")
        self.async_set_updated_data(data)
        self.control_updates.set()
        if self.pending is not None:
            key, target, future, sent_at = self.pending
            if key in values and now >= sent_at and values[key] == target and not future.done():
                future.set_result(None)

    @callback
    def receive_frame(self, frame):
        self.recorder.add(frame)
        block = block_reply_payload(frame)
        if block is not None:
            start, raw_bytes = block
            values = {}
            for index, raw in enumerate(raw_bytes):
                selector = start + index
                if selector in SELECTORS:
                    self.parameter_raw[selector] = raw
                    self.parameter_live.add(selector)
                    values.update(values_for_raw(selector, raw))
            self.parameter_store.async_delay_save(self.parameter_storage_data, 1)
            self.receive(values)
            if self.block_pending is not None:
                expected_start, future = self.block_pending
                if start == expected_start and not future.done():
                    future.set_result(None)
        payload = reply_payload(frame)
        if payload is not None:
            selector, raw = payload
            self.parameter_raw[selector] = raw
            self.parameter_live.add(selector)
            self.parameter_store.async_delay_save(self.parameter_storage_data, 1)
            self.receive(values_for_raw(selector, raw))
            if self.parameter_pending is not None:
                expected_selector, expected_raw, future = self.parameter_pending
                if (selector, raw) == (expected_selector, expected_raw) and not future.done():
                    future.set_result(None)
        if is_settings_frame(frame):
            self.settings_template = frame
            self.settings_received_at = monotonic()
            self.settings_last_received = datetime.now(timezone.utc)
            self.settings_source = "live"
            self.requires_live_settings = False
            self.resync_after_sequence = None
            stored = {"frame_hex": frame.hex(), "requires_live_settings": False,
                      "last_received_utc": self.settings_last_received.isoformat()}
            if stored != self.saved_settings:
                self.saved_settings = stored
                self.settings_store.async_delay_save(lambda: self.saved_settings, 1)
            _LOGGER.debug("AUX live settings template: %s", frame.hex(" "))
            # A command from the panel is a template, not an acknowledged state.
            self.async_set_updated_data(dict(self.data))

    async def restore_settings(self):
        """Restore only a CRC-valid observed E2, never a synthesized command."""
        try:
            stored = await self.settings_store.async_load()
            if not isinstance(stored, dict) or not isinstance(stored.get("frame_hex"), str):
                self.settings_restore_status = "no_saved_frame"
                return
            if len(stored["frame_hex"]) != 54:
                self.settings_restore_status = "invalid_saved_frame"
                return
            frame = bytes.fromhex(stored["frame_hex"])
            if not is_settings_frame(frame):
                self.settings_restore_status = "invalid_saved_frame"
                _LOGGER.warning("Ignoring saved AUX E2: invalid frame or CRC")
                return
            self.settings_template = frame
            self.settings_source = "storage"
            self.settings_restore_status = "restored"
            self.saved_settings = stored
            # Older caches have no receive timestamp; do not invent one.
            try:
                received = datetime.fromisoformat(stored.get("last_received_utc", ""))
                if received.tzinfo is not None and received.utcoffset() is not None:
                    self.settings_last_received = received.astimezone(timezone.utc)
            except (TypeError, ValueError):
                pass
            # Migrate the persistent lock used by 0.4.0/0.4.1. A timeout does
            # not corrupt the observed template. Fresh full telemetry replaces
            # known fields before a later explicit command, with no auto retry.
            self.requires_live_settings = False
            if stored.get("requires_live_settings") or stored.get("needs_telemetry_resync"):
                self.resync_after_sequence = self.receive_sequence
            _LOGGER.info("Restored saved AUX E2; waiting for fresh telemetry before writes")
        except (OSError, ValueError, HomeAssistantError) as err:
            self.settings_restore_status = "load_error"
            _LOGGER.warning("Cannot restore saved AUX E2: %s", err)

    def require_fresh_control_values(self):
        self.requires_live_settings = False
        self.resync_after_sequence = self.receive_sequence
        if self.saved_settings is not None:
            self.saved_settings = {**self.saved_settings, "requires_live_settings": False,
                                   "needs_telemetry_resync": True}
            self.settings_store.async_delay_save(lambda: self.saved_settings, 1)

    def has_fresh_value(self, key):
        if key in PARAMETER_KEYS:
            # B6 replies are event-driven, not cyclic telemetry. Keep the last
            # confirmed snapshot, with its source exposed to the user.
            return self.connected and self.data.get(key) is not None
        return (self.connected and self.data.get(key) is not None
                and key in self.seen and monotonic() - self.seen[key] <= STALE_SECONDS)

    def control_status(self, key):
        """Explain readiness without confusing a stored template with telemetry."""
        if key in PARAMETER_KEYS:
            p = BY_KEY[key]
            reason = ("disconnected" if not self.connected else
                      "missing_live_parameter_byte" if p.mask and p.selector not in self.parameter_live else
                      "ready")
            return {"write_ready": reason == "ready", "write_block_reason": reason,
                    "parameter_selector_hex": f"{p.selector:04X}", "parameter_offset": 8,
                    "parameter_mask_hex": f"{p.mask:02X}" if p.mask else None,
                    "parameter_raw": self.parameter_raw.get(p.selector),
                    "value_source": "live_reply" if p.selector in self.parameter_live else "stored_snapshot",
                    "state_is_snapshot": True, "range_source": p.range_source,
                    "tcp_connected": self.connected, "confirmation": "03 reply, matching selector and full byte",
                    "last_command": dict(self.last_command) if self.last_command is not None else None,
                    "last_parameter_refresh": self.last_parameter_refresh,
                    "last_parameter_refresh_error": self.last_parameter_refresh_error,
                    "integration_version": VERSION}
        missing = [k for k in CONTROL_KEYS if self.data.get(k) is None or k not in self.seen]
        stale = [k for k in CONTROL_KEYS if k not in missing and not self.has_fresh_value(k)]
        if key not in CONTROL_KEYS:
            reason = "unknown_control"
        elif self.settings_template is None:
            reason = "missing_e2"
        elif not self.connected:
            reason = "disconnected"
        elif missing:
            reason = "missing_telemetry"
        elif stale:
            reason = "stale_telemetry"
        elif self.resync_after_sequence is not None:
            reason = "waiting_for_control_resync"
        else:
            reason = "ready"
        return {"write_ready": reason == "ready", "write_block_reason": reason,
                "e2_available": self.settings_template is not None,
                "e2_saved": self.saved_settings is not None,
                "e2_source": self.settings_source,
                "e2_restore_status": self.settings_restore_status,
                "value_source": "B7 & 0x01" if key == "dhw_enabled" else "telemetry",
                "state_is_snapshot": False,
                "requires_live_settings": self.requires_live_settings,
                "tcp_connected": self.connected,
                "missing_control_fields": missing, "stale_control_fields": stale,
                "last_command": dict(self.last_command) if self.last_command is not None else None,
                "last_preflight": dict(self.last_preflight) if self.last_preflight else None,
                "integration_version": VERSION}

    def can_control(self, key):
        return self.control_status(key)["write_ready"]

    async def async_control(self, key, value):
        if key in PARAMETER_KEYS:
            return await self.async_parameter_control(key, value)
        requested_at = datetime.now(timezone.utc).isoformat()
        async with self.control_lock:
            _LOGGER.info("AUX write requested: %s=%s, current=%s", key, value, self.data.get(key))
            if key not in CONTROL_KEYS:
                raise localized_error("unknown_control")
            if self.settings_template is None:
                raise localized_error("missing_e2")
            try:
                # Validate the requested value before any outgoing traffic.
                make_command(self.settings_template, key, value)
                await self._async_prepare_e2_control()
            except (ValueError, OSError, TimeoutError, ConnectionError) as err:
                raise localized_error("e2_not_sent", detail=str(err)) from err
            if (key == "fast_dhw_enabled" and value is True
                    and (not self.has_fresh_value("dhw_enabled") or not self.data["dhw_enabled"])):
                raise localized_error("fast_requires_dhw")
            # OFF may follow a just-sent ON whose telemetry is still delayed.
            # Never drop an explicit Fast DHW OFF because an old sample is OFF.
            force_fast_off = key == "fast_dhw_enabled" and value is False
            if not force_fast_off and self.has_fresh_value(key) and self.data[key] == value:
                _LOGGER.info("AUX write skipped: %s already equals %s", key, value)
                return
            if not self.can_control(key):
                status = self.control_status(key)
                _LOGGER.warning("AUX write blocked: %s=%s; status=%s", key, value, status)
                error_keys = {"missing_e2": "missing_e2", "disconnected": "disconnected",
                              "waiting_for_control_resync": "resync", "missing_telemetry": "missing_telemetry",
                              "stale_telemetry": "stale_telemetry"}
                raise localized_error(error_keys.get(status["write_block_reason"], "not_ready"),
                                      fields=", ".join(status["missing_control_fields"] + status["stale_control_fields"]))
            template = self.settings_template
            future = asyncio.get_running_loop().create_future()
            # No incoming sample may confirm a command that has not been sent.
            self.pending = (key, value, future, float("inf"))
            self.last_command = {"field": key, "requested_value": value,
                                 "status": "waiting_for_55", "tx_frame_hex": None,
                                 "requested_at_utc": requested_at,
                                 "tx_trigger": None, "e2_updated_during_wait": False}
            def build_at_poll():
                if not self.can_control(key):
                    raise localized_error("readiness_lost")
                if key == "fast_dhw_enabled" and value is True and not self.data["dhw_enabled"]:
                    raise localized_error("fast_dhw_changed")
                frame = make_command(self.settings_template, key, value, self.data)
                self.last_command["e2_updated_during_wait"] = self.settings_template != template
                self.pending = (key, value, future, monotonic())
                return frame
            try:
                tx = await self.receiver.send_after_poll(build_at_poll)
                self.last_command.update(tx)
                self.last_command["status"] = "sent_waiting_for_telemetry"
                _LOGGER.info("AUX settings sent over TCP; awaiting telemetry: %s=%s", key, value)
                await asyncio.wait_for(future, timeout=COMMAND_TIMEOUT)
                self.last_command["status"] = "confirmed"
                self.last_command["observed_value"] = self.data.get(key)
                _LOGGER.info("AUX write confirmed by telemetry: %s=%s", key, value)
            except (OSError, TimeoutError, ConnectionError, ValueError, HomeAssistantError) as err:
                # Retain valid E2; require new telemetry, not another HMI action.
                self.require_fresh_control_values()
                self.last_command.update(status="not_confirmed" if self.last_command.get("tx_at_utc") else "not_sent_or_transport_error", observed_value=self.data.get(key),
                                         error=f"{type(err).__name__}: {err}")
                _LOGGER.warning("AUX write failed or not confirmed: %s=%s, last_telemetry=%s, error=%s: %s",
                                key, value, self.data.get(key), type(err).__name__, err)
                if not self.last_command.get("tx_at_utc"):
                    raise localized_error("e2_transport_error", detail=str(err)) from err
                raise localized_error("no_confirmation") from err
            except asyncio.CancelledError:
                self.require_fresh_control_values()
                self.last_command["status"] = "cancelled"
                future.cancel()
                raise
            finally:
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()
                self.pending = None
                self.async_set_updated_data(dict(self.data))

    def parameter_storage_data(self):
        return {"raw_by_selector": {f"{s:04X}": raw for s, raw in self.parameter_raw.items()}}

    async def _async_prepare_e2_control(self):
        """B3 configuration reads are not E2 control telemetry or an E2 template."""
        after_sequence = self.receive_sequence
        self.last_preflight = {"status": "reading_b3_then_waiting_for_telemetry"}
        try:
            for start in READ_BLOCKS:
                try:
                    await self._async_read_parameter_block(start, timeout=min(COMMAND_TIMEOUT, 5))
                except TimeoutError as err:
                    # E2 contains none of the B6 configuration fields read by
                    # B3. A B3 timeout cannot invalidate fresh E2 telemetry.
                    self.last_preflight["b3_warning"] = str(err)
                    _LOGGER.warning("AUX B3 before E2 failed; still requiring fresh control telemetry: %s", err)
                    break
            await self._async_wait_for_control_telemetry(after_sequence)
        except (OSError, TimeoutError, ConnectionError) as err:
            self.last_preflight.update(status="failed", error=str(err))
            raise
        self.last_preflight.update(status="ready", waiting_for=[], fresh_control_fields=list(CONTROL_KEYS))

    async def _async_wait_for_control_telemetry(self, after_sequence):
        deadline = monotonic() + CONTROL_PREFLIGHT_TIMEOUT
        while True:
            if not self.connected:
                raise ConnectionError("Connection lost while refreshing telemetry")
            missing = [key for key in CONTROL_KEYS
                       if not self.has_fresh_value(key) or self.seen_sequence.get(key, -1) <= after_sequence]
            if not missing:
                return
            self.last_preflight.update(waiting_for=missing)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("Missing new control readings: " + ", ".join(missing))
            self.control_updates.clear()
            try:
                await asyncio.wait_for(self.control_updates.wait(), timeout=remaining)
            except TimeoutError as err:
                raise TimeoutError("Missing new control readings: " + ", ".join(missing)) from err

    async def restore_parameters(self):
        """Restore snapshots for display only; packed writes need a live full byte."""
        try:
            stored = await self.parameter_store.async_load()
            if not isinstance(stored, dict) or not isinstance(stored.get("raw_by_selector"), dict):
                return
            values = {}
            for selector_hex, raw in stored["raw_by_selector"].items():
                try:
                    selector = int(selector_hex, 16)
                except (TypeError, ValueError):
                    continue
                if selector not in SELECTORS or type(raw) is not int or not 0 <= raw <= 255:
                    continue
                self.parameter_raw[selector] = raw
                values.update(values_for_raw(selector, raw))
            self.async_set_updated_data({**self.data, **values})
        except (OSError, ValueError, HomeAssistantError) as err:
            _LOGGER.warning("Cannot restore AUX parameter snapshots: %s", err)

    async def async_parameter_control(self, key, value):
        async with self.control_lock:
            p = BY_KEY[key]
            try:
                # Refresh the entire shared byte before read-modify-write.
                # Single-value validation can run before querying; packed
                # validation uses a dummy byte for type checking only.
                make_parameter_command(key, value, 0 if p.mask else None)
                await self._async_read_parameter_block(0 if p.selector < 60 else 60)
            except (ValueError, OSError, TimeoutError, ConnectionError) as err:
                raise localized_error("b6_preflight", detail=str(err)) from err
            if not self.can_control(key):
                raise localized_error("b6_not_ready")
            try:
                # Validate before waiting, then rebuild from the newest packed
                # byte after the receive gap, preserving every unrelated bit.
                make_parameter_command(key, value, self.parameter_raw.get(p.selector))
                await self.receiver.wait_for_idle()
                if not self.can_control(key):
                    raise ConnectionError("Parameter write readiness was lost")
                frame = make_parameter_command(key, value, self.parameter_raw.get(p.selector))
            except (ValueError, OSError, TimeoutError, ConnectionError) as err:
                raise localized_error("parameter_not_sent", detail=str(err)) from err
            future = asyncio.get_running_loop().create_future()
            self.parameter_pending = (p.selector, frame[8], future)
            self.last_command = {"field": key, "requested_value": value, "status": "sending",
                                 "tx_frame_hex": frame.hex(" "), "protocol": "B6",
                                 "selector_hex": f"{p.selector:04X}"}
            try:
                _LOGGER.info("AUX TX B6 parameter %s=%s: %s", key, value, frame.hex(" "))
                await self.receiver.send(frame)
                self.last_command["status"] = "sent_waiting_for_reply"
                await asyncio.wait_for(future, timeout=COMMAND_TIMEOUT)
                self.last_command.update(status="confirmed_by_reply", observed_value=self.data.get(key))
            except (OSError, TimeoutError, ConnectionError) as err:
                # Do not assume success or resend. For a packed byte a fresh
                # response is required before the next read-modify-write.
                self.parameter_live.discard(p.selector)
                self.last_command.update(status="not_confirmed", error=f"{type(err).__name__}: {err}")
                raise localized_error("b6_no_reply") from err
            except asyncio.CancelledError:
                self.parameter_live.discard(p.selector)
                self.last_command["status"] = "cancelled"
                raise
            finally:
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()
                self.parameter_pending = None
                self.async_set_updated_data(dict(self.data))
            # Same control lock: no other read/write can interleave with the
            # acknowledged B6 and its explicit configuration readback.
            try:
                await self._async_read_parameter_block(0 if p.selector < 60 else 60)
            except (OSError, TimeoutError, ConnectionError) as err:
                self.last_command.update(status="confirmed_by_reply_readback_failed", readback_error=str(err))
                self.async_set_updated_data(dict(self.data))
                raise localized_error("b6_readback_failed") from err
            if self.data.get(key) != value:
                self.last_command.update(status="readback_mismatch", observed_value=self.data.get(key))
                self.async_set_updated_data(dict(self.data))
                raise localized_error("b6_readback_mismatch")
            self.last_command.update(status="confirmed_by_readback", observed_value=self.data.get(key))
            self.async_set_updated_data(dict(self.data))

    async def _async_read_parameter_block(self, start, timeout=None):
        """Caller holds control_lock. Send one read, with no automatic retries."""
        if not self.connected:
            raise ConnectionError("No connection to the pump")
        await self.receiver.wait_for_idle()
        if not self.connected:
            raise ConnectionError("Connection lost before read")
        frame = make_parameter_read(start)
        read_timeout = COMMAND_TIMEOUT if timeout is None else timeout
        future = asyncio.get_running_loop().create_future()
        self.block_pending = (start, future)
        try:
            _LOGGER.info("AUX TX B3 read %04X: %s", start, frame.hex(" "))
            await self.receiver.send(frame)
            try:
                await asyncio.wait_for(future, timeout=read_timeout)
            except TimeoutError as err:
                raise TimeoutError(f"B3 block {start:04X}: no valid 60-byte 03 reply within {read_timeout} s") from err
        finally:
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()
            self.block_pending = None

    async def async_refresh_parameters(self):
        async with self.control_lock:
            try:
                for start in READ_BLOCKS:
                    await self._async_read_parameter_block(start)
            except (OSError, TimeoutError, ConnectionError) as err:
                self.last_parameter_refresh_error = f"{type(err).__name__}: {err}"
                self.async_set_updated_data(dict(self.data))
                raise localized_error("b3_refresh_failed", detail=f"{type(err).__name__}: {err}") from err
            from datetime import datetime, timezone
            self.last_parameter_refresh = datetime.now(timezone.utc).isoformat()
            self.last_parameter_refresh_error = None
            self.async_set_updated_data(dict(self.data))

    async def parameter_refresh_loop(self):
        """Read on first valid traffic/reconnect and every five minutes."""
        while True:
            await self.parameter_refresh_event.wait()
            self.parameter_refresh_event.clear()
            if self.connected:
                try:
                    await self.async_refresh_parameters()
                except HomeAssistantError as err:
                    _LOGGER.warning("AUX B3 refresh failed: %s", err)
            try:
                await asyncio.wait_for(self.parameter_refresh_event.wait(), timeout=PARAMETER_REFRESH_SECONDS)
            except TimeoutError:
                self.parameter_refresh_event.set()

    @callback
    def disconnect(self):
        self.connected = False
        self.control_updates.set()
        self.parameter_refresh_event.set()
        self.parameter_live.clear()
        if self.block_pending is not None and not self.block_pending[1].done():
            self.block_pending[1].set_exception(ConnectionError("Pompa rozłączyła się podczas odczytu B3"))
        if self.parameter_pending is not None and not self.parameter_pending[2].done():
            self.parameter_pending[2].set_exception(ConnectionError("Pompa rozłączyła się"))
        # Keep the last observed E2 across reconnects; all telemetry is cleared.
        if self.pending is not None and not self.pending[2].done():
            self.pending[2].set_exception(ConnectionError("Pompa rozłączyła się"))
        self.seen.clear()
        self.seen_sequence.clear()
        self.async_set_updated_data({k: v for k, v in self.data.items() if k in PARAMETER_KEYS})

    @callback
    def expire(self, _now):
        stale = [key for key, time in self.seen.items() if key not in PARAMETER_KEYS and monotonic() - time > STALE_SECONDS]
        if stale:
            data = dict(self.data)
            for key in stale:
                self.seen.pop(key, None)
                data.pop(key, None)
            self.async_set_updated_data(data)

    async def stop(self):
        if self.parameter_refresh_task is not None:
            self.parameter_refresh_task.cancel()
            try:
                await self.parameter_refresh_task
            except asyncio.CancelledError:
                pass
            self.parameter_refresh_task = None
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        await self.recorder.stop(reason="unload")
        if self.parameter_raw:
            try:
                await self.parameter_store.async_save(self.parameter_storage_data())
            except (OSError, HomeAssistantError) as err:
                _LOGGER.warning("Cannot save AUX parameters: %s", err)
        if self.saved_settings is not None:
            try:
                await self.settings_store.async_save(self.saved_settings)
            except (OSError, HomeAssistantError) as err:
                _LOGGER.warning("Cannot save AUX E2 on unload: %s", err)

async def async_setup_entry(hass, entry):
    _LOGGER.info("Starting AUX ACHP-H08 integration %s with B3 configuration reads", VERSION)
    enable_default_entities(hass, entry)
    coordinator = AuxCoordinator(hass, entry)
    await coordinator.restore_name_language()
    await coordinator.restore_settings()
    await coordinator.restore_parameters()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    if not hass.data[DOMAIN].get("_capture_download_view"):
        from .download import CaptureDownloadView
        hass.http.register_view(CaptureDownloadView(hass))
        hass.data[DOMAIN]["_capture_download_view"] = True
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.restore_recording()
    entry.async_on_unload(async_track_time_interval(hass, coordinator.expire, timedelta(seconds=15)))
    @callback
    def on_stop(_event):
        hass.async_create_task(coordinator.stop())
    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_stop))
    coordinator.task = hass.async_create_background_task(coordinator.receiver.run(), "AUX ACHP-H08 TCP receiver")
    coordinator.parameter_refresh_task = hass.async_create_background_task(coordinator.parameter_refresh_loop(), "AUX ACHP-H08 B3 refresh")
    return True

async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.stop()
    return True
