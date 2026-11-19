"""The strangler facade: every client call goes through here, and per-route settings decide who answers.

read:  legacy | shadow | new   (shadow answers from legacy and compares with the new service)
write: legacy | new
percent: share of skus, chosen by a stable hash, that use the new service
"""
import zlib

from common import call


class ConfigError(ValueError):
    pass


def validate(cfg):
    if cfg["read"] not in ("legacy", "shadow", "new") or cfg["write"] not in ("legacy", "new"):
        raise ConfigError("read must be legacy, shadow or new; write must be legacy or new")
    if not 0 <= cfg["percent"] <= 100:
        raise ConfigError("percent must be 0 to 100")
    if cfg["read"] == "new" and cfg["write"] == "legacy" and cfg["percent"] > 0:
        raise ConfigError("reads from the new service while writes go to the monolith would serve stale stock")


class Facade:
    def __init__(self, legacy_url, new_url):
        self.legacy_url, self.new_url = legacy_url, new_url
        self.cfg = {"read": "legacy", "write": "legacy", "percent": 100}
        self.parity = {"compared": 0, "mismatches": 0, "examples": []}

    def configure(self, cfg):
        merged = {**self.cfg, **cfg}
        validate(merged)
        self.cfg = merged

    def _uses_new(self, sku):
        return zlib.crc32(sku.encode()) % 100 < self.cfg["percent"]

    def route(self, method, path, body):
        if path == "/_parity":
            return 200, self.parity
        if method == "POST" and path == "/_config":
            try:
                self.configure(body)
            except (ConfigError, KeyError) as e:
                return 422, {"error": str(e)}
            return 200, self.cfg
        parts = path.strip("/").split("/")
        sku = parts[1] if len(parts) > 1 else ""
        if method == "GET":
            return self._read(path, sku)
        base = self.new_url if self.cfg["write"] == "new" and self._uses_new(sku) else self.legacy_url
        return call(method, base + path, body)

    def _read(self, path, sku):
        mode = self.cfg["read"]
        if mode == "new" and self._uses_new(sku):
            return call("GET", self.new_url + path)
        status, body = call("GET", self.legacy_url + path)
        if mode == "shadow" and self._uses_new(sku):
            n_status, n_body = call("GET", self.new_url + path)
            self.parity["compared"] += 1
            if (n_status, n_body) != (status, body):
                self.parity["mismatches"] += 1
                if len(self.parity["examples"]) < 5:
                    self.parity["examples"].append({"path": path, "legacy": body, "new": n_body})
        return status, body
