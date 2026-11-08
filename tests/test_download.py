"""The capture endpoint serves only a prepared per-entry ZIP and requires HA auth."""
import asyncio
import importlib.util
from pathlib import Path
import sys,tempfile,types,unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
class Missing(Exception):pass

class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_entry_scoped_download(self):
        pkg=types.ModuleType('capture_download_test');pkg.__path__=[str(ROOT/'custom_components/aux_heatpump')]
        fake_web=types.SimpleNamespace(HTTPNotFound=Missing,FileResponse=lambda path,headers:types.SimpleNamespace(path=path,headers=headers))
        mods={'capture_download_test':pkg,'aiohttp':types.SimpleNamespace(web=fake_web),
              'homeassistant.components.http':types.SimpleNamespace(HomeAssistantView=object)}
        with patch.dict(sys.modules,mods):
            spec=importlib.util.spec_from_file_location('capture_download_test.download',ROOT/'custom_components/aux_heatpump/download.py')
            m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'frames.zip';path.write_bytes(b'zip fixture')
            c=types.SimpleNamespace(recorder=types.SimpleNamespace(export_file=path))
            hass=types.SimpleNamespace(data={'aux_heatpump':{'entry':c,'_capture_download_view':True}},async_add_executor_job=asyncio.to_thread)
            view=m.CaptureDownloadView(hass)
            self.assertTrue(view.requires_auth)
            response=await view.get(None,'entry')
            self.assertEqual(response.path,path)
            self.assertIn('attachment;',response.headers['Content-Disposition'])
            self.assertEqual(response.headers['Cache-Control'],'no-store')
            for entry in ('unknown','_capture_download_view','../entry','../../configuration.yaml'):
                with self.assertRaises(Missing):await view.get(None,entry)
            c.recorder.export_file=None
            with self.assertRaises(Missing):await view.get(None,'entry')

if __name__=='__main__':unittest.main()
