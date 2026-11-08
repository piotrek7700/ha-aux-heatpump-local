"""Authenticated/signed, short-lived download of the latest local capture ZIP."""
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from .const import DOMAIN


class CaptureDownloadView(HomeAssistantView):
    url = '/api/aux_heatpump/captures/{entry_id}'
    name = 'api:aux_heatpump:captures'
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, entry_id):
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id)
        recorder = getattr(coordinator, "recorder", None)
        path = recorder.export_file if recorder is not None else None
        if path is None or not await self.hass.async_add_executor_job(path.is_file):
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={
            'Content-Disposition': 'attachment; filename="aux-heatpump-frames.zip"',
            'Cache-Control': 'no-store',
        })
