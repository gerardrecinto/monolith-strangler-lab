"""Stand-in for the legacy monolith: parts, orders and stock in one database, changed in one transaction."""
import sqlite3
import threading


class OutOfStock(Exception):
    pass


class Legacy:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS parts (sku TEXT PRIMARY KEY, name TEXT, stock INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, sku TEXT, qty INTEGER, status TEXT);""")

    def seed(self, parts):
        with self.lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO parts VALUES (?,?,?)", parts)

    def get_part(self, sku):
        with self.lock:
            r = self.db.execute("SELECT sku, name, stock FROM parts WHERE sku=?", (sku,)).fetchone()
        return {"sku": r[0], "name": r[1], "stock": r[2]} if r else None

    def all_parts(self):
        with self.lock:
            return [{"sku": r[0], "name": r[1], "stock": r[2]} for r in self.db.execute("SELECT sku, name, stock FROM parts")]

    def reserve(self, sku, qty):
        with self.lock, self.db:
            r = self.db.execute("SELECT stock FROM parts WHERE sku=?", (sku,)).fetchone()
            if r is None:
                raise KeyError(sku)
            if r[0] < qty:
                raise OutOfStock(sku)
            self.db.execute("UPDATE parts SET stock=stock-? WHERE sku=?", (qty, sku))
            oid = self.db.execute("INSERT INTO orders (sku, qty, status) VALUES (?,?,'reserved')", (sku, qty)).lastrowid
        return {"sku": sku, "reserved": qty, "stock": r[0] - qty, "order_id": oid}

    def set_stock(self, sku, stock):
        """Used by the event consumer to keep this database in step while the new service owns writes."""
        with self.lock, self.db:
            self.db.execute("UPDATE parts SET stock=? WHERE sku=?", (stock, sku))


def route_for(legacy):
    def route(method, path, body):
        parts = path.strip("/").split("/")
        if method == "GET" and len(parts) == 2 and parts[0] == "parts":
            p = legacy.get_part(parts[1])
            return (200, p) if p else (404, {"error": "unknown sku"})
        if method == "POST" and len(parts) == 3 and parts[0] == "parts" and parts[2] == "reserve":
            try:
                return 200, legacy.reserve(parts[1], int(body["qty"]))
            except KeyError:
                return 404, {"error": "unknown sku"}
            except OutOfStock:
                return 409, {"error": "out of stock"}
        return 404, {"error": "no route"}
    return route
