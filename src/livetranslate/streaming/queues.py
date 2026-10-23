from collections import deque
import threading


class BoundedFrames:
    def __init__(self, capacity):
        self.capacity = capacity
        self.items = deque()
        self.condition = threading.Condition()
        self.closed = False
        self.dropped = 0
        self.high_water = 0

    def put(self, item):
        with self.condition:
            if self.closed:
                return False
            if len(self.items) == self.capacity:
                self.items.popleft()
                self.dropped += 1
            self.items.append(item)
            self.high_water = max(self.high_water, len(self.items))
            self.condition.notify()
            return True

    def get(self):
        with self.condition:
            self.condition.wait_for(lambda: self.items or self.closed)
            return self.items.popleft() if self.items else None

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def stats(self):
        with self.condition:
            return {"depth": len(self.items), "capacity": self.capacity, "dropped": self.dropped, "high_water": self.high_water}


class WorkQueue:
    """Final FIFO plus one coalesced draft; producer never waits for inference."""
    def __init__(self, final_capacity):
        self.capacity = final_capacity
        self.finals = deque()
        self.partial = None
        self.closed = False
        self.condition = threading.Condition()
        self.dropped = 0
        self.coalesced = 0
        self.high_water = 0

    def put(self, item):
        with self.condition:
            if self.closed:
                return [item]
            dropped = []
            if item.final:
                if self.partial is not None and self.partial.uid == item.uid:
                    self.partial = None
                if any(j.uid == item.uid for j in self.finals):
                    return []
                if len(self.finals) == self.capacity:
                    dropped.append(self.finals.popleft())
                    self.dropped += 1
                self.finals.append(item)
            else:
                if any(j.uid == item.uid for j in self.finals):
                    return []
                if self.partial is not None:
                    self.coalesced += 1
                self.partial = item
            self.high_water = max(self.high_water, len(self.finals) + (self.partial is not None))
            self.condition.notify()
            return dropped

    def get(self):
        with self.condition:
            self.condition.wait_for(lambda: self.finals or self.partial is not None or self.closed)
            if self.finals:
                return self.finals.popleft()
            item, self.partial = self.partial, None
            return item

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()

    def stats(self):
        with self.condition:
            return {"final_depth": len(self.finals), "partial_depth": int(self.partial is not None),
                    "capacity": self.capacity + 1, "dropped": self.dropped, "coalesced": self.coalesced,
                    "high_water": self.high_water}
