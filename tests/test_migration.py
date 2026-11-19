import pytest

from common import JsonServer, call
from facade.strangler import ConfigError, Facade, validate
from legacy.monolith import Legacy, route_for as legacy_route
from services.consumer import LegacySync
from services.inventory import Inventory, route_for as inv_route

SEED = [("P-100", "Brake pad", 10), ("P-200", "Air filter", 5), ("P-300", "Oil filter", 0)]


@pytest.fixture()
def stack():
    legacy, inv = Legacy(), Inventory()
    legacy.seed(SEED)
    inv.backfill(legacy.all_parts())
    ls, ns = JsonServer(legacy_route(legacy)).start(), JsonServer(inv_route(inv)).start()
    facade = Facade(ls.url, ns.url)
    fs = JsonServer(facade.route).start()
    yield legacy, inv, facade, fs
    for s in (ls, ns, fs):
        s.stop()


def test_backfill_is_repeatable():
    legacy, inv = Legacy(), Inventory()
    legacy.seed(SEED)
    inv.backfill(legacy.all_parts())
    inv.reserve("P-100", 3)
    inv.backfill(legacy.all_parts())  # must not overwrite the new service's own change
    assert inv.get_part("P-100")["stock"] == 7


def test_passthrough_serves_from_the_monolith(stack):
    _, _, _, fs = stack
    assert call("GET", fs.url + "/parts/P-100") == (200, {"sku": "P-100", "name": "Brake pad", "stock": 10})
    assert call("POST", fs.url + "/parts/P-100/reserve", {"qty": 4})[1]["stock"] == 6
    assert call("GET", fs.url + "/parts/NOPE")[0] == 404
    assert call("POST", fs.url + "/parts/P-300/reserve", {"qty": 1})[0] == 409


def test_shadow_mode_finds_a_difference_before_cutover(stack):
    legacy, inv, _, fs = stack
    call("POST", fs.url + "/_config", {"read": "shadow"})
    call("GET", fs.url + "/parts/P-100")
    assert call("GET", fs.url + "/_parity")[1]["mismatches"] == 0
    legacy.set_stock("P-200", 99)  # drift: someone changed the monolith directly
    call("GET", fs.url + "/parts/P-200")
    parity = call("GET", fs.url + "/_parity")[1]
    assert parity["compared"] == 2 and parity["mismatches"] == 1
    assert parity["examples"][0]["legacy"]["stock"] == 99 and parity["examples"][0]["new"]["stock"] == 5


def test_unsafe_cutover_is_refused(stack):
    _, _, _, fs = stack
    status, body = call("POST", fs.url + "/_config", {"read": "new", "write": "legacy"})
    assert status == 422 and "stale stock" in body["error"]
    with pytest.raises(ConfigError):
        validate({"read": "new", "write": "legacy", "percent": 50})
    assert call("POST", fs.url + "/_config", {"read": "bogus"})[0] == 422


def test_write_cutover_then_events_keep_the_monolith_in_step(stack):
    legacy, inv, _, fs = stack
    assert call("POST", fs.url + "/_config", {"read": "new", "write": "new"})[0] == 200
    assert call("POST", fs.url + "/parts/P-100/reserve", {"qty": 4})[1]["stock"] == 6
    assert legacy.get_part("P-100")["stock"] == 10  # the monolith has not heard yet
    sync = LegacySync(legacy)
    assert inv.relay(sync.handle) == 1
    assert legacy.get_part("P-100")["stock"] == 6
    assert inv.relay(sync.handle) == 0  # outbox rows are marked published


def test_consumer_ignores_duplicates_and_old_events():
    legacy = Legacy()
    legacy.seed(SEED)
    sync = LegacySync(legacy)
    e2 = {"id": "b", "sku": "P-100", "stock_after": 8, "version": 2}
    e1 = {"id": "a", "sku": "P-100", "stock_after": 9, "version": 1}
    assert sync.handle(e2) is True
    assert sync.handle(e2) is False  # duplicate delivery
    assert sync.handle(e1) is False  # arrived late
    assert legacy.get_part("P-100")["stock"] == 8


def test_outbox_event_survives_a_failed_publish():
    inv = Inventory()
    inv.backfill([{"sku": "P-1", "name": "x", "stock": 5}])
    inv.reserve("P-1", 1)

    def boom(event):
        raise ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        inv.relay(boom)
    got = []
    assert inv.relay(got.append) == 1 and got[0]["stock_after"] == 4  # delivered on the next try


def test_percent_routes_a_stable_share_of_skus(stack):
    _, _, facade, _ = stack
    facade.configure({"percent": 50})
    first = {s: facade._uses_new(s) for s in ("P-100", "P-200", "P-300", "P-400", "P-500", "P-600")}
    assert first == {s: facade._uses_new(s) for s in first}
    assert 0 < sum(first.values()) < len(first)
