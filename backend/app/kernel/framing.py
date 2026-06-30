"""Length-prefixed framing for the parent <-> worker pipe.

4-byte big-endian length + UTF-8 JSON body. The worker reads blocking on stdin;
the parent reads with a wall-clock deadline so a hung worker can be killed.
"""

from __future__ import annotations

import os
import select
import struct
import time
from typing import IO

_HDR = struct.Struct(">I")


def write_frame(f: IO[bytes], data: bytes) -> None:
    f.write(_HDR.pack(len(data)))
    f.write(data)
    f.flush()


def _read_exact(f: IO[bytes], n: int) -> bytes | None:
    chunks: list[bytes] = []
    got = 0
    while got < n:
        chunk = f.read(n - got)
        if not chunk:
            return None
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_frame(f: IO[bytes]) -> bytes | None:
    """Blocking read of one frame (worker side)."""
    hdr = _read_exact(f, 4)
    if hdr is None:
        return None
    (n,) = _HDR.unpack(hdr)
    return _read_exact(f, n)


def _read_exact_fd(fd: int, n: int, deadline: float) -> bytes | None:
    chunks: list[bytes] = []
    got = 0
    while got < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        readable, _, _ = select.select([fd], [], [], remaining)
        if not readable:
            return None
        chunk = os.read(fd, n - got)
        if not chunk:
            return None
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_frame_timeout(fd: int, timeout: float) -> bytes | None:
    """Read one frame from ``fd`` within ``timeout`` seconds, else None
    (parent side). None means timeout *or* EOF (dead worker)."""
    deadline = time.monotonic() + timeout
    hdr = _read_exact_fd(fd, 4, deadline)
    if hdr is None:
        return None
    (n,) = _HDR.unpack(hdr)
    return _read_exact_fd(fd, n, deadline)
