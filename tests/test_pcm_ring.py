from array import array

import pytest

from note_taker.endpoints import assert_loopback
from note_taker.pcm_ring import SecurePcmRing


def test_ring_capacity_bounded():
    ring = SecurePcmRing(8)
    dropped = ring.push(array("h", range(20)))
    assert len(ring) == 8
    assert dropped >= 12


def test_ring_pop_and_clear_zeroises():
    ring = SecurePcmRing(4)
    ring.push(array("h", [1, 2, 3, 4]))
    raw = ring.pop_exact(2)
    assert raw is not None
    assert len(raw) == 4
    ring.clear()
    assert len(ring) == 0
    assert ring.pop_exact(1) is None


def test_loopback_validator():
    assert assert_loopback("http://127.0.0.1:8000/health").startswith("http://")
    with pytest.raises(ValueError):
        assert_loopback("http://example.com/api")
