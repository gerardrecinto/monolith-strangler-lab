"""New inventory service. Owns parts. Writes a stock event to an outbox in the same transaction as the stock change."""
import json
import sqlite3
import threading
import uuid


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS parts (sku TEXT PRIMARY KEY, name TEXT, stock INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0);""")

    def backfill(self, parts):
        """Copy existing rows from the monolith. Safe to run again: existing rows are left alone."""
        with self.lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO parts (sku, name, stock) VALUES (?,?,?)",
                                [(p["sku"], p["name"], p["stock"]) for p in parts])

    def get_part(self, sku):
        with self.lock:
            r = self.db.execute("SELECT sku, name, stock FROM parts WHERE sku=?", (sku,)).fetchone()
        return {"sku": r[0], "name": r[1], "stock": r[2]} if r else None

    def reserve(self, sku, qty):
        with self.lock, self.db:
            r = self.db.execute("SELECT stock, version FROM parts WHERE sku=?", (sku,)).fetchone()
            if r is None:
                raise KeyError(sku)
            if r[0] < qty:
                raise OutOfStock(sku)
            stock, version = r[0] - qty, r[1] + 1
            self.db.execute("UPDATE parts SET stock=?, version=? WHERE sku=?", (stock, version, sku))
            event = {"id": str(uuid.uuid4()), "type": "part.reserved", "sku": sku, "qty": qty,
                     "stock_after": stock, "version": version}
            self.db.execute("INSERT INTO outbox (id, payload) VALUES (?,?)", (event["id"], json.dumps(event)))
        return {"sku": sku, "reserved": qty, "stock": stock}

    def relay(self, publish):
        """Send unpublished events with publish(event); mark each only after publish returns. Returns the count sent."""
        with self.lock:
            rows = self.db.execute("SELECT id, payload FROM outbox WHERE published=0 ORDER BY rowid").fetchall()
        sent = 0
        for eid, payload in rows:
            publish(json.loads(payload))
            with self.lock, self.db:
                self.db.execute("UPDATE outbox SET published=1 WHERE id=?", (eid,))
            sent += 1
        return sent


def route_for(inv):
    def route(method, path, body):
        parts = path.strip("/").split("/")
        if method == "GET" and len(parts) == 2 and parts[0] == "parts":
            p = inv.get_part(parts[1])
            return (200, p) if p else (404, {"error": "unknown sku"})
        if method == "POST" and len(parts) == 3 and parts[0] == "parts" and parts[2] == "reserve":
            try:
                return 200, inv.reserve(parts[1], int(body["qty"]))
            except KeyError:
                return 404, {"error": "unknown sku"}
            except OutOfStock:
                return 409, {"error": "out of stock"}
        return 404, {"error": "no route"}
    return route
