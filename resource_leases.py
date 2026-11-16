"""Cooperative resource handoff and a committed-output cache; no device APIs."""
from dataclasses import dataclass, field
import time
import uuid


class LeaseError(RuntimeError):
    pass


@dataclass
class FakeHandle:
    opened: bool = True
    running: bool = True
    fail_close: bool = False
    fail_open: bool = False
    _revoked: bool = field(default=False, init=False, repr=False)

    def stop(self):
        self.running = False

    def close(self):
        if self.fail_close:
            raise LeaseError("fake close failed")
        self.opened = False
        self.running = False

    def open(self):
        if self._revoked:
            raise LeaseError("borrower handle was revoked")
        if self.fail_open:
            raise LeaseError("fake open failed")
        self.opened = True

    def start(self):
        if self._revoked:
            raise LeaseError("borrower handle was revoked")
        if not self.opened:
            raise LeaseError("cannot start a closed handle")
        self.running = True


@dataclass(frozen=True)
class Lease:
    host_epoch: str
    token: str
    deadline: float


class ResourceHost:
    """One cooperative owner. The fake borrower must close before restoration."""
    def __init__(self, handle, clock=time.monotonic):
        if handle.running and not handle.opened:
            raise ValueError("invalid initial handle state")
        self.handle = handle
        self.clock = clock
        self.epoch = uuid.uuid4().hex
        self.state = "LOCAL"
        self.lease = None
        self.borrower = None
        self.original = None
        self.first_error = None
        self.cleanup_errors = []

    def _error(self, error):
        if self.first_error is None:
            self.first_error = str(error)
        self.state = "ERROR"

    def lend(self, ttl=10.0):
        import math
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be finite and positive")
        if self.state != "LOCAL":
            raise LeaseError("resource is not available")
        self.original = (self.handle.opened, self.handle.running)
        self.state = "STOPPING"
        try:
            self.handle.stop()
            self.handle.close()
        except Exception as error:
            self._error(error)
            raise
        # The credential is issued only after close really succeeds.
        self.lease = Lease(self.epoch, uuid.uuid4().hex, self.clock() + ttl)
        self.borrower = FakeHandle(opened=False, running=False)
        self.state = "LENT"
        return self.lease

    def _validate(self, lease):
        if self.state != "LENT" or lease != self.lease or lease.host_epoch != self.epoch:
            raise LeaseError("unknown or old-session credential")
        if self.clock() >= lease.deadline:
            raise LeaseError("expired credential; explicit reclamation required")

    def checkout(self, lease):
        self._validate(lease)
        if self.handle.opened or self.borrower.opened:
            raise LeaseError("exclusive checkout unavailable")
        self.borrower.open()
        return self.borrower

    def _restore(self):
        if self.borrower is not None and self.borrower.opened:
            raise LeaseError("borrower has not closed its handle")
        if self.borrower is not None:
            self.borrower._revoked = True
        self.state = "RESTORING"
        try:
            was_open, was_running = self.original
            if was_open:
                self.handle.open()
            if was_running:
                self.handle.start()
        except Exception as error:
            self._error(error)
            self.cleanup_errors.append(str(error))
            raise
        self.lease = None
        self.borrower = None
        self.state = "LOCAL"

    def release(self, lease):
        self._validate(lease)
        self._restore()

    def reclaim_expired(self):
        if self.state != "LENT" or self.clock() < self.lease.deadline:
            raise LeaseError("no expired lease")
        # Expiration cannot prove an external process released a resource.
        if self.borrower.opened:
            raise LeaseError("expired borrower still owns its handle")
        self._restore()


class OutputCache:
    """Last fully submitted byte, never a claim about physical output state."""
    def __init__(self):
        self.committed = None
        self.pending = None

    def observe(self, value):
        if self.pending is not None:
            raise LeaseError("write in progress")
        self._byte(value)
        self.committed = value

    @staticmethod
    def _byte(value):
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("expected an unsigned byte")

    def prepare(self, mask, value):
        self._byte(mask)
        self._byte(value)
        if self.committed is None:
            raise LeaseError("output unknown; an explicit observation is required")
        if self.pending is not None:
            raise LeaseError("write in progress")
        self.pending = (self.committed & ~mask) | (value & mask)
        # This byte is a fake payload, not a real device protocol.
        return bytes([self.pending])

    def finish(self, bytes_written, expected=1):
        if self.pending is None:
            raise LeaseError("no pending write")
        pending, self.pending = self.pending, None
        if type(expected) is not int or expected != 1 or type(bytes_written) is not int or bytes_written != expected:
            self.committed = None
            raise LeaseError("short/failed write; output cache is now unknown")
        self.committed = pending

    def failed(self):
        self.pending = None
        self.committed = None
