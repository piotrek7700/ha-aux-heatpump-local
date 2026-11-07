"""Asynchronous TCP receiver with explicit user-requested settings writes."""
import asyncio
import logging
from datetime import datetime, timezone
from time import monotonic
from .protocol import StreamParser, decode_frame
_LOGGER = logging.getLogger(__name__)
HMI_POLL = bytes.fromhex("7e 01 e1 55 0a 00 00 07 52 67")

class Receiver:
    def __init__(self, host, port, on_update, on_disconnect, on_frame=None):
        self.host, self.port = host, port
        self.on_update, self.on_disconnect = on_update, on_disconnect
        self.writer = None
        self.on_frame = on_frame
        self.last_rx = None
        self._poll_write = None

    async def send_after_poll(self, frame_factory, timeout=10):
        """Send once on the next received native55; never reuse an old poll.

        Match the successful TCP probe: process the complete read first, then
        write without a quiet-gap delay. TCP buffering cannot guarantee a UART slot.
        """
        writer = self.writer
        if writer is None or writer.is_closing():
            raise ConnectionError("No connection to the pump")
        if self._poll_write is not None:
            raise RuntimeError("Another command is waiting for 55")
        future = asyncio.get_running_loop().create_future()
        pending = (writer, frame_factory, future)
        self._poll_write = pending
        try:
            try:
                result = await asyncio.wait_for(future, timeout)
            except TimeoutError as err:
                raise TimeoutError(f"No native 55 received within {timeout} s; E2 was not sent") from err
            await asyncio.wait_for(writer.drain(), timeout=5)
            if writer is not self.writer:
                raise ConnectionError("Connection changed during the write")
            return result
        finally:
            if self._poll_write is pending:
                self._poll_write = None

    def _dispatch_poll_write(self, frames):
        if self._poll_write is None or HMI_POLL not in frames:
            return
        writer, factory, future = self._poll_write
        self._poll_write = None  # Consume before calling the factory: no retries.
        if future.done():
            return
        try:
            if writer is not self.writer or writer.is_closing():
                raise ConnectionError("Connection changed before receiving 55")
            frame = factory()  # Latest E2 and telemetry; no await before write.
            writer.write(frame)
            result = {"tx_at_utc": datetime.now(timezone.utc).isoformat(),
                      "tx_trigger": "native_55", "tx_frame_hex": frame.hex(" "),
                      "rx_to_tx_ms": round((monotonic() - self.last_rx) * 1000, 3),
                      "frames_after_poll_in_tcp_read": len(frames) - frames.index(HMI_POLL) - 1}
            _LOGGER.info("AUX TX after55: %s", result)
            future.set_result(result)
        except Exception as err:
            future.set_exception(err)

    async def wait_for_idle(self, quiet_seconds=0.1, timeout=3):
        """Wait for a receive gap; a TCP bridge cannot guarantee RS485 bus arbitration."""
        deadline = monotonic() + timeout
        while True:
            if self.writer is None or self.writer.is_closing() or self.last_rx is None:
                raise ConnectionError("No active reception from the pump")
            remaining = quiet_seconds - (monotonic() - self.last_rx)
            if remaining <= 0:
                return
            if monotonic() + remaining > deadline:
                raise TimeoutError("No 100 ms receive gap; command was not sent")
            await asyncio.sleep(remaining)

    async def send(self, frame):
        writer = self.writer
        if writer is None or writer.is_closing():
            raise ConnectionError("No connection to the pump")
        writer.write(frame)
        await asyncio.wait_for(writer.drain(), timeout=5)
        if writer is not self.writer:
            raise ConnectionError("Connection changed during the write")

    async def run(self):
        delay = 2
        try:
            while True:
                parser = StreamParser()
                try:
                    reader, self.writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port), timeout=10)
                    while True:
                        data = await asyncio.wait_for(reader.read(4096), timeout=30)
                        if not data:
                            raise ConnectionError("TCP peer closed connection")
                        self.last_rx = monotonic()
                        values = {}
                        frames = parser.feed(data)
                        for frame in frames:
                            if self.on_frame is not None:
                                self.on_frame(frame)
                            values.update(decode_frame(frame))
                        if values:
                            delay = 2
                            self.on_update(values)
                        self._dispatch_poll_write(frames)
                except (OSError, TimeoutError, ConnectionError) as err:
                    _LOGGER.debug("AUX TCP disconnected: %s; retry in %ss", err, delay)
                finally:
                    self.on_disconnect()
                    await self.close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
        finally:
            self.on_disconnect()
            await self.close()

    async def close(self):
        if self._poll_write is not None:
            _, _, future = self._poll_write
            self._poll_write = None
            if not future.done():
                future.set_exception(ConnectionError("Disconnected before receiving 55; E2 was not sent"))
        writer, self.writer = self.writer, None
        self.last_rx = None
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except (OSError, TimeoutError):
                pass

async def validate_connection(host, port):
    """Require a known CRC-valid telemetry frame, without sending any bytes."""
    writer = None
    try:
        async with asyncio.timeout(15):
            reader, writer = await asyncio.open_connection(host, port)
            parser = StreamParser()
            while data := await reader.read(4096):
                for frame in parser.feed(data):
                    if decode_frame(frame):
                        return
            raise ConnectionError("No AUX telemetry received")
    finally:
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except (OSError, TimeoutError):
                pass
