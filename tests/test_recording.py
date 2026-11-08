"""Bounded recorder lifecycle, storage limits and write-failure tests."""
import asyncio
from datetime import datetime,timedelta,timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aux_recording_tests',ROOT/'custom_components/aux_heatpump/recorder.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
FRAME=bytes.fromhex('7e 01 e1 55 0a 00 00 07 52 67')

class RecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.directory=Path(self.tmp.name)/'captures'
        self.rec=m.FrameRecorder(self.directory)
        self.addAsyncCleanup(self.rec.stop)

    async def test_inactive_has_no_io_and_start_stop_flushes(self):
        self.rec.add(FRAME);self.assertFalse(self.directory.exists())
        await self.rec.start();self.rec.add(FRAME);self.rec.add(FRAME)
        await self.rec.stop()
        rows=[json.loads(s) for f in self.directory.glob('frames-*.jsonl') for s in f.read_text().splitlines()]
        self.assertEqual(len(rows),2)
        self.assertEqual(self.rec.written_frames,2)
        self.assertFalse(self.rec.active)
        self.assertEqual([r['sequence'] for r in rows],[1,2])
        for r in rows:
            self.assertEqual(bytes.fromhex(r['hex']),FRAME)
            self.assertEqual(r['direction'],'rx')
            self.assertEqual(r['length'],len(FRAME))
            self.assertIsNotNone(datetime.fromisoformat(r['timestamp_utc']).tzinfo)

    async def test_repeated_start_and_resume_keep_deadline(self):
        await self.rec.start();expires=self.rec.expires_at;started=self.rec.started_at
        await self.rec.start();self.assertEqual(self.rec.expires_at,expires)
        await self.rec.stop(reason='unload')
        await self.rec.start(expires_at=expires,started_at=started)
        self.assertEqual(self.rec.expires_at,expires)
        self.assertEqual(self.rec.started_at,started)

    async def test_automatic_deadline_without_incoming_frames(self):
        self.rec.duration=timedelta(seconds=0.04)
        await self.rec.start()
        await asyncio.wait_for(self.rec.task,2)
        self.assertFalse(self.rec.active)
        self.assertEqual(self.rec.stop_reason,'expired')

    async def test_expired_saved_session_never_starts(self):
        await self.rec.start(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
        self.assertFalse(self.rec.active)
        self.assertIsNone(self.rec.task)
        self.assertFalse(self.directory.exists())

    async def test_queue_is_bounded_and_reports_loss(self):
        self.rec.queue=asyncio.Queue(maxsize=2)
        await self.rec.start()
        for _ in range(100):self.rec.add(FRAME)
        self.assertEqual(self.rec.queue.qsize(),2)
        self.assertEqual(self.rec.dropped_frames,98)
        await self.rec.stop();self.assertEqual(self.rec.written_frames,2)

    async def test_rotation_retains_latest_and_preserves_unmanaged_files(self):
        self.rec.max_file_bytes=500;self.rec.max_files=2
        await self.rec.start()
        keep=self.directory/'notes.jsonl';keep.write_text('keep')
        for _ in range(30):self.rec.add(FRAME)
        await self.rec.stop()
        files=sorted(self.directory.glob('frames-*.jsonl'))
        self.assertEqual(len(files),2)
        self.assertLessEqual(sum(f.stat().st_size for f in files),1000)
        rows=[json.loads(s) for f in files for s in f.read_text().splitlines()]
        self.assertEqual(rows[-1]['sequence'],30)
        self.assertEqual(keep.read_text(),'keep')
        self.assertEqual(self.rec.written_frames,30)

    async def test_write_error_stops_recorder_without_escaping_to_receiver(self):
        await self.rec.start()
        def fail(records):raise OSError('disk full')
        self.rec._write_batch=fail
        self.rec.add(FRAME);self.rec.wake.set()
        await asyncio.wait_for(self.rec.task,2)
        self.assertFalse(self.rec.active)
        self.assertEqual(self.rec.stop_reason,'write_error')
        self.assertIn('disk full',self.rec.last_error)
        self.rec.add(FRAME)  # Remains harmless after failure.

    async def test_export_flushes_queue_and_recording_continues(self):
        from zipfile import ZipFile
        await self.rec.start()
        self.rec.add(FRAME)
        path=await self.rec.export()
        self.assertTrue(self.rec.active)
        with ZipFile(path) as z:
            self.assertIsNone(z.testzip())
            rows=[json.loads(s) for name in z.namelist() for s in z.read(name).decode().splitlines()]
            self.assertEqual(len(rows),1)
        self.rec.add(FRAME)
        await self.rec.export()
        with ZipFile(path) as z:
            self.assertEqual(sum(len(z.read(name).splitlines()) for name in z.namelist()),2)
        self.assertEqual(len(list((self.directory/'exports').glob('*.zip'))),1)
        self.assertFalse(list((self.directory/'exports').glob('*.tmp')))

    async def test_export_without_frames_gives_clear_error(self):
        with self.assertRaisesRegex(ValueError,'No recorded frames'):
            await self.rec.export()

    async def test_directory_error_does_not_report_active(self):
        path=Path(self.tmp.name)/'file';path.write_text('not a directory')
        self.rec.directory=path/'captures'
        with self.assertRaises(OSError):await self.rec.start()
        self.assertFalse(self.rec.active)
        self.assertIsNone(self.rec.task)

if __name__=='__main__':unittest.main()
