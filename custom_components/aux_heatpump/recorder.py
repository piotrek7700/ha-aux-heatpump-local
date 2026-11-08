"""Bounded, local RX-frame recording; all filesystem work runs in an executor."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from zipfile import ZipFile, ZIP_DEFLATED
from pathlib import Path
import re
from uuid import uuid4

_LOGGER = logging.getLogger(__name__)
_MANAGED_FILE = re.compile(r'^frames-\d{8}T\d{6}Z-[0-9a-f]{8}-\d{6}\.jsonl$')


class FrameRecorder:
    def __init__(self, directory, executor=None, notify=None, task_factory=None,
                 duration=timedelta(days=7), max_file_bytes=16*1024*1024,
                 max_files=32, queue_limit=5000):
        self.directory = Path(directory) if directory is not None else None
        self.executor = executor or asyncio.to_thread
        self.notify = notify or (lambda: None)
        self.task_factory = task_factory or asyncio.create_task
        self.duration = duration
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.queue = asyncio.Queue(maxsize=queue_limit)
        self.progress = asyncio.Event()
        self._enqueued_sequence = self._written_sequence = 0
        self.wake = asyncio.Event()
        self.io_lock = asyncio.Lock()
        self.export_lock = asyncio.Lock()
        self.export_file = None
        self.task = None
        self.active = False
        self.started_at = None
        self.expires_at = None
        self.written_frames = 0
        self.dropped_frames = 0
        self.last_error = None
        self.last_frame_at = None
        self.last_file = None
        self.stop_reason = None
        self._file = None
        self._file_size = 0
        self._file_index = 0
        self._session = None
        self._sequence = 0

    def _changed(self):
        self.notify()

    def _prepare(self):
        if self.directory is None:
            raise ValueError('Home Assistant configuration directory is unavailable')
        self.directory.mkdir(parents=True, exist_ok=True)
        # Fail on write permissions before reporting a successful start.
        probe = self.directory / ('.write-check-' + uuid4().hex)
        try:
            with probe.open('x', encoding='utf-8'):
                pass
        finally:
            probe.unlink(missing_ok=True)
        self._prune()

    def _prune(self):
        files = sorted(p for p in self.directory.glob('frames-*.jsonl')
                       if _MANAGED_FILE.fullmatch(p.name) and p.is_file())
        for old in files[:-self.max_files]:
            old.unlink()

    async def start(self, expires_at=None, started_at=None):
        if self.active:
            return  # A repeated press does not extend an existing recording.
        if self.task is not None:
            await self.task
        now = datetime.now(timezone.utc)
        deadline = expires_at or now + self.duration
        if deadline <= now:
            return
        await self.executor(self._prepare)
        self.started_at = started_at or now
        self.expires_at = deadline
        self.last_error = self.last_frame_at = self.last_file = None
        self.stop_reason = None
        self.written_frames = self.dropped_frames = self._sequence = 0
        self._session = uuid4().hex[:8]
        self._file = None
        self._file_size = self._file_index = 0
        self._enqueued_sequence = self._written_sequence = 0
        self.active = True
        self.task = self.task_factory(self._run())
        self._changed()

    def add(self, frame):
        """Called in the receive callback; bounded memory, no disk I/O or TX."""
        if not self.active:
            return
        now = datetime.now(timezone.utc)
        if now >= self.expires_at:
            self.active = False
            self.stop_reason = 'expired'
            self.wake.set()
            self._changed()
            return
        self._sequence += 1
        record = {'timestamp_utc': now.isoformat(), 'direction': 'rx',
                  'session': self._session, 'sequence': self._sequence,
                  'length': len(frame), 'hex': frame.hex(' ')}
        try:
            self.queue.put_nowait(record)
            self._enqueued_sequence = self._sequence
        except asyncio.QueueFull:
            self.dropped_frames += 1

    def _write_batch(self, records):
        handle = None
        try:
            for record in records:
                line = (json.dumps(record, separators=(',', ':')) + '\n').encode('utf-8')
                if len(line) > self.max_file_bytes:
                    raise ValueError('Capture record exceeds the file-size limit')
                if self._file is None or self._file_size + len(line) > self.max_file_bytes:
                    if handle is not None:
                        handle.close(); handle = None
                    self._file_index += 1
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
                    self._file = self.directory / f'frames-{stamp}-{self._session}-{self._file_index:06d}.jsonl'
                    self._file_size = 0
                if handle is None:
                    handle = self._file.open('ab')
                handle.write(line)
                self._file_size += len(line)
            if handle is not None:
                handle.close(); handle = None
            self._prune()
            return str(self._file)
        finally:
            if handle is not None:
                handle.close()

    async def _run(self):
        try:
            while self.active or not self.queue.empty():
                if self.active and datetime.now(timezone.utc) >= self.expires_at:
                    self.active = False
                    self.stop_reason = 'expired'
                batch = []
                while len(batch) < 256 and not self.queue.empty():
                    batch.append(self.queue.get_nowait())
                if batch:
                    async with self.io_lock:
                        self.last_file = await self.executor(self._write_batch, batch)
                    self.written_frames += len(batch)
                    self._written_sequence = batch[-1]["sequence"]
                    self.progress.set()
                    self.last_frame_at = batch[-1]['timestamp_utc']
                    self._changed()
                    continue
                if not self.active:
                    break
                self.wake.clear()
                remaining = max(0.001, (self.expires_at-datetime.now(timezone.utc)).total_seconds())
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=min(1.0, remaining))
                except TimeoutError:
                    pass
        except Exception as err:
            self.last_error = f'{type(err).__name__}: {err}'
            self.stop_reason = 'write_error'
            self.active = False
            while not self.queue.empty():
                self.queue.get_nowait()
                self.dropped_frames += 1
            _LOGGER.warning('AUX frame recording stopped: %s', self.last_error)
        finally:
            self.active = False
            self.progress.set()
            self._changed()

    def _export_snapshot(self):
        files = sorted(p for p in self.directory.glob("frames-*.jsonl")
                       if _MANAGED_FILE.fullmatch(p.name) and p.is_file())
        if not files:
            raise ValueError("No recorded frames are available yet")
        export_dir = self.directory / "exports"
        export_dir.mkdir(exist_ok=True)
        target = export_dir / "frames.zip"
        temporary = export_dir / "frames.zip.tmp"
        try:
            with ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=1) as archive:
                for file in files:
                    archive.write(file, file.name)
            os.replace(temporary, target)  # At most one completed export is retained.
        finally:
            temporary.unlink(missing_ok=True)
        return target

    async def flush(self):
        target = self._enqueued_sequence
        while self._written_sequence < target:
            if self.last_error:
                raise OSError(self.last_error)
            if self.task is None or self.task.done():
                raise ValueError("Recorder stopped before queued frames were written")
            self.progress.clear()
            self.wake.set()
            await asyncio.wait_for(self.progress.wait(), timeout=30)

    async def export(self):
        async with self.export_lock:
            await self.flush()
            # The active writer and export never touch the files concurrently.
            # Reception continues through the bounded queue while ZIP is built.
            self.wake.set()
            async with self.io_lock:
                self.export_file = await self.executor(self._export_snapshot)
            return self.export_file

    async def stop(self, reason='manual'):
        self.active = False
        self.stop_reason = reason
        self.wake.set()
        if self.task is not None:
            await self.task  # Drain queued records and finish executor work.
            self.task = None
        self._changed()

    def attributes(self):
        return {'directory': str(self.directory) if self.directory is not None else None,
                'started_at': self.started_at.isoformat() if self.started_at else None,
                'expires_at': self.expires_at.isoformat() if self.expires_at else None,
                'written_frames': self.written_frames, 'dropped_frames': self.dropped_frames,
                'queued_frames': self.queue.qsize(), 'last_frame_at': self.last_frame_at,
                'last_file': self.last_file, 'last_error': self.last_error,
                'stop_reason': self.stop_reason, 'direction': 'rx',
                'max_raw_storage_bytes': self.max_file_bytes*self.max_files,
                'download_archive': str(self.export_file) if self.export_file else None,
                'retention': 'oldest managed capture files are removed at the limit'}
