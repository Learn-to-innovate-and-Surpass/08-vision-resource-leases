from resource_leases import FakeHandle, OutputCache, ResourceHost


def main():
    host = ResourceHost(FakeHandle())
    lease = host.lend()
    borrowed = host.checkout(lease)
    print("during loan:", host.state, "local handle open:", host.handle.opened)
    borrowed.close()
    host.release(lease)
    print("after return:", host.state, "running:", host.handle.running)
    cache = OutputCache()
    cache.observe(0b10100000)
    payload = cache.prepare(0b00000011, 0b00000001)
    cache.finish(len(payload))
    print("synthetic committed byte:", format(cache.committed, "08b"))


if __name__ == "__main__":
    main()
