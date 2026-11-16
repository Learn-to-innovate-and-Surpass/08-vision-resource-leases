import unittest
from dataclasses import replace
from resource_leases import FakeHandle, LeaseError, OutputCache, ResourceHost


class LeaseTests(unittest.TestCase):
    def test_exclusive_and_restore(self):
        host = ResourceHost(FakeHandle())
        lease = host.lend()
        self.assertFalse(host.handle.opened)
        borrower = host.checkout(lease)
        with self.assertRaises(LeaseError):
            host.checkout(lease)
        with self.assertRaises(LeaseError):
            host.release(lease)
        borrower.close()
        host.release(lease)
        self.assertTrue(host.handle.running)
        with self.assertRaises(LeaseError):
            host.release(lease)

    def test_close_failures_do_not_grant_or_restore(self):
        host = ResourceHost(FakeHandle(fail_close=True))
        with self.assertRaises(LeaseError):
            host.lend()
        self.assertIsNone(host.lease)
        self.assertEqual(host.state, "ERROR")
        self.assertTrue(host.handle.opened)
        host = ResourceHost(FakeHandle())
        lease = host.lend()
        borrower = host.checkout(lease)
        borrower.fail_close = True
        with self.assertRaises(LeaseError):
            borrower.close()
        with self.assertRaises(LeaseError):
            host.release(lease)
        self.assertFalse(host.handle.opened)

    def test_expiry_and_old_session(self):
        clock = [0.0]
        host = ResourceHost(FakeHandle(), clock=lambda: clock[0])
        lease = host.lend(ttl=1)
        with self.assertRaises(LeaseError):
            host.checkout(replace(lease, host_epoch="old"))
        clock[0] = 1
        with self.assertRaises(LeaseError):
            host.release(lease)
        host.reclaim_expired()
        self.assertEqual(host.state, "LOCAL")
        other = ResourceHost(FakeHandle())
        with self.assertRaises(LeaseError):
            other.checkout(lease)

    def test_initially_closed_stays_closed(self):
        host = ResourceHost(FakeHandle(False, False))
        lease = host.lend()
        host.release(lease)
        self.assertFalse(host.handle.opened)
        self.assertFalse(host.handle.running)

    def test_return_and_reclamation_revoke_old_handle_references(self):
        for expired in (False, True):
            with self.subTest(expired=expired):
                clock = [0.0]
                host = ResourceHost(FakeHandle(), clock=lambda: clock[0])
                lease = host.lend(ttl=1)
                borrower = host.checkout(lease)
                borrower.close()
                if expired:
                    clock[0] = 1.0
                    host.reclaim_expired()
                else:
                    host.release(lease)
                with self.assertRaises(LeaseError):
                    borrower.open()
                with self.assertRaises(LeaseError):
                    borrower.start()
                self.assertFalse(borrower.opened)
                self.assertTrue(host.handle.opened)

    def test_modified_and_previous_round_credentials_are_rejected(self):
        host = ResourceHost(FakeHandle(), clock=lambda: 0.0)
        previous = host.lend()
        host.release(previous)
        current = host.lend()
        for forged in (replace(current, token="modified"),
                       replace(current, deadline=current.deadline + 1), previous):
            with self.subTest(credential=forged):
                for action in (host.checkout, host.release):
                    with self.assertRaises(LeaseError):
                        action(forged)
                self.assertEqual(host.state, "LENT")
        host.release(current)

    def test_restore_error_keeps_first_error(self):
        host = ResourceHost(FakeHandle())
        lease = host.lend()
        host.handle.fail_open = True
        with self.assertRaises(LeaseError):
            host.release(lease)
        self.assertEqual(host.state, "ERROR")
        self.assertEqual(host.first_error, "fake open failed")


class CacheTests(unittest.TestCase):
    def test_commit_preserves_other_bits_and_serializes(self):
        cache = OutputCache()
        with self.assertRaises(LeaseError):
            cache.prepare(3, 1)
        cache.observe(0b10100000)
        self.assertEqual(cache.prepare(3, 1), bytes([0b10100001]))
        self.assertEqual(cache.committed, 0b10100000)
        with self.assertRaises(LeaseError):
            cache.prepare(3, 2)
        cache.finish(1)
        self.assertEqual(cache.committed, 0b10100001)

    def test_short_write_and_exception_invalidate(self):
        cache = OutputCache()
        cache.observe(7)
        cache.prepare(1, 0)
        with self.assertRaises(LeaseError):
            cache.finish(0)
        self.assertIsNone(cache.committed)
        cache.observe(7)
        cache.prepare(1, 0)
        cache.failed()
        self.assertIsNone(cache.committed)


if __name__ == "__main__":
    unittest.main()
