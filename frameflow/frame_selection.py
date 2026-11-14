"""Frame-selection state. Ranges are inclusive, one-based timeline positions."""


class FrameSelection:
    def __init__(self):
        self.base_range = None
        self.overrides = {}
        self.ranges = ()
        self._drag_origin = (None, {})

    def reset(self):
        self.base_range = None
        self.overrides.clear()

    def set_range(self, first, last):
        self.base_range = first, last

    def contains(self, frame):
        return any(first <= frame <= last for first, last in self.ranges)

    def sync(self):
        spans = [(frame, frame) for frame, selected in self.overrides.items() if selected]
        invalid = False
        if self.base_range is not None:
            first, last = self.base_range
            invalid = first > last
            for frame in sorted(self.overrides):
                if first <= frame <= last and not self.overrides[frame]:
                    if first < frame:
                        spans.append((first, frame - 1))
                    first = frame + 1
            if first <= last:
                spans.append((first, last))
        merged = []
        for first, last in sorted(spans):
            if merged and first <= merged[-1][1] + 1:
                merged[-1] = merged[-1][0], max(merged[-1][1], last)
            else:
                merged.append((first, last))
        self.ranges = () if invalid else tuple(merged)
        return invalid

    def click(self, frame, current, *, control, shift):
        self._drag_origin = self.base_range, dict(self.overrides)
        if control:
            selected = self.contains(frame)
            in_range = self.base_range is not None and self.base_range[0] <= frame <= self.base_range[1]
            if selected != in_range:
                self.overrides.pop(frame, None)
            else:
                self.overrides[frame] = not selected
        elif shift:
            first, last = self.base_range or (current, current)
            if first > last:
                first = last = current
            self.set_range(min(first, frame), max(last, frame))
        else:
            self.overrides.clear()
            self.set_range(frame, frame)

    def drag(self, anchor, frame, *, control, shift):
        first, last = sorted((anchor, frame))
        base_range, base_overrides = self._drag_origin
        self.overrides = dict(base_overrides)
        if control:
            for selected_frame in range(first, last + 1):
                in_range = base_range is not None and base_range[0] <= selected_frame <= base_range[1]
                was_selected = base_overrides.get(selected_frame, in_range)
                if not was_selected:
                    self.overrides[selected_frame] = True
                elif in_range:
                    self.overrides[selected_frame] = False
                else:
                    self.overrides.pop(selected_frame, None)
            self.base_range = base_range
        elif shift and base_range is not None:
            self.base_range = min(base_range[0], first), max(base_range[1], last)
        else:
            self.overrides.clear()
            self.base_range = first, last
        return self.base_range or (first, last)
