"""Applies inventory events to the monolith so both databases agree while the migration is under way."""


class LegacySync:
    def __init__(self, legacy):
        self.legacy = legacy
        self.seen = set()
        self.version = {}  # highest version applied per sku

    def handle(self, event):
        """Idempotent and order tolerant: a duplicate or an older event changes nothing. Returns True if applied."""
        if event["id"] in self.seen:
            return False
        self.seen.add(event["id"])
        if event["version"] <= self.version.get(event["sku"], 0):
            return False
        self.version[event["sku"]] = event["version"]
        self.legacy.set_stock(event["sku"], event["stock_after"])
        return True
