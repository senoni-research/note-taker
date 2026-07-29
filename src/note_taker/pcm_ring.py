"""Bounded PCM ring that never grows and zeroises on clear."""

from __future__ import annotations

import threading
from array import array


class SecurePcmRing:
    """Fixed-capacity int16 PCM ring. Overwrites oldest samples when full."""

    def __init__(self, capacity_samples: int) -> None:
        if capacity_samples <= 0:
            raise ValueError("capacity_samples must be positive")
        self.capacity = capacity_samples
        self._buf = array("h", [0]) * capacity_samples
        self._start = 0
        self._len = 0
        self._dropped = 0
        self._lock = threading.Lock()

    @property
    def dropped_samples(self) -> int:
        with self._lock:
            return self._dropped

    def push(self, samples: array | list[int] | bytes) -> int:
        """Append int16 samples. Returns number of overwritten (dropped) samples."""
        if isinstance(samples, bytes):
            incoming = array("h")
            incoming.frombytes(samples)
        elif isinstance(samples, array):
            incoming = samples
        else:
            incoming = array("h", samples)

        n = len(incoming)
        dropped = 0
        with self._lock:
            if n >= self.capacity:
                # Keep only the newest capacity samples
                dropped = self._len + (n - self.capacity)
                self._buf[:] = incoming[-self.capacity :]
                self._start = 0
                self._len = self.capacity
                self._dropped += dropped
                return dropped

            free = self.capacity - self._len
            if n > free:
                drop = n - free
                self._start = (self._start + drop) % self.capacity
                self._len -= drop
                dropped = drop
                self._dropped += drop

            for sample in incoming:
                idx = (self._start + self._len) % self.capacity
                self._buf[idx] = sample
                self._len += 1
        return dropped

    def pop_exact(self, n: int) -> bytes | None:
        """Remove and return exactly n samples as s16le bytes, or None if insufficient."""
        with self._lock:
            if self._len < n:
                return None
            out = array("h")
            for _ in range(n):
                out.append(self._buf[self._start])
                self._buf[self._start] = 0  # zeroise consumed slot
                self._start = (self._start + 1) % self.capacity
                self._len -= 1
            return out.tobytes()

    def clear(self) -> None:
        with self._lock:
            for i in range(self.capacity):
                self._buf[i] = 0
            self._start = 0
            self._len = 0

    def __len__(self) -> int:
        with self._lock:
            return self._len
