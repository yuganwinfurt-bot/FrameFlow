"""Frame-clock and geometry for transient drag layouts."""

from bisect import bisect_right
from dataclasses import dataclass
from math import floor
from time import perf_counter

from PySide6.QtCore import QObject, Qt, QTimer


@dataclass
class Spring:
    position: float
    target: float
    velocity: float = 0.0
    stiffness: float = 410.0
    damping: float = 36.0

    def advance(self, dt):
        self.velocity += (self.stiffness * (self.target - self.position) - self.damping * self.velocity) * dt
        self.position += self.velocity * dt
        if abs(self.position - self.target) < .15 and abs(self.velocity) < 2:
            self.position, self.velocity = self.target, 0.0
        return self.position != self.target or self.velocity != 0


class SpringClock(QObject):
    def __init__(self, parent, tick):
        super().__init__(parent)
        self.tick = tick
        self.states = {}
        self.timer = QTimer(self, interval=16, timerType=Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._advance)

    def aim(self, key, initial, target):
        if key not in self.states:
            self.states[key] = Spring(initial, target)
        else:
            self.states[key].target = target
        return self.states[key].position

    def wake(self):
        if not self.timer.isActive():
            self.last = perf_counter()
            self.timer.start()

    def reset(self):
        self.timer.stop()
        self.states.clear()

    def _advance(self):
        now = perf_counter()
        elapsed = min(.05, now - self.last)
        self.last = now
        # Small integration steps keep the springs stable even after a delayed UI frame.
        steps = max(1, int(elapsed * 240) + 1)
        moving = False
        for _ in range(steps):
            moving = False
            for state in self.states.values():
                moving |= state.advance(elapsed / steps)
        if not self.tick(elapsed, moving) and not moving:
            self.timer.stop()


def insertion_slot(center, spacing, count, previous=None):
    slot = min(count - 1, max(0, floor(center / spacing)))
    if previous is not None and abs(slot - previous) == 1:
        if abs(center - max(slot, previous) * spacing) < min(3, spacing * .15):
            return previous
    return slot


def edge_speed(coordinate, extent, *, band=30, maximum=430):
    band = min(band, extent / 3)
    if not band:
        return 0
    if coordinate < band:
        return -maximum * min(1, (band - coordinate) / band)
    if coordinate > extent - band:
        return maximum * min(1, (coordinate - extent + band) / band)
    return 0


class FrameCompaction:
    """Translate between original frame boundaries and a track with the selection removed."""

    def __init__(self, ranges):
        self.ranges = tuple(ranges)
        self.starts = [start for start, _ in self.ranges]
        self.removed = [0]
        self.compacted = []
        for start, end in self.ranges:
            self.compacted.append(start - self.removed[-1])
            self.removed.append(self.removed[-1] + end - start)

    def collapse(self, position):
        index = bisect_right(self.starts, position) - 1
        if index < 0:
            return position
        start, end = self.ranges[index]
        return position - self.removed[index] - min(end - start, position - start)

    def expand(self, position):
        return position + self.removed[bisect_right(self.compacted, position)]

    @property
    def count(self):
        return self.removed[-1]
