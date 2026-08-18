"""Publisher protocol — destinations other than Discord."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class Publisher(Protocol):
    name: str

    async def publish(self, jobs: Sequence) -> bool:
        """Publish the given jobs. Returns True when something was written.

        Must return False (not raise) when the destination is unchanged or
        temporarily unavailable — publishing is best-effort and must never
        abort a scan.
        """
        ...
