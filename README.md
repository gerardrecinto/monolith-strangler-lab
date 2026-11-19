# monolith-strangler-lab

A small, runnable model of moving one slice out of a monolith: a facade that decides who answers, a shadow mode that compares old and new before cutover, an outbox that keeps both databases in step, and a review gate for the kinds of mistakes AI-assisted code tends to contain.

It is for engineers planning a monolith migration who want to see the safety steps in code. Everything is Python standard library, in memory, with made-up parts data. It models the pattern; it is not a framework and has not moved a real system.

## Run it

Needs Python 3.10 or later. No install step, about 5 seconds.

```bash
git clone https://github.com/gerardrecinto/monolith-strangler-lab.git && cd monolith-strangler-lab
python3 -m pip install pytest
python3 -m pytest -q                                   # 24 tests
python3 -m review.gate common.py legacy services facade review   # prints: review gate: clean
```

If `pytest` is not found, run `python3 -m pip install pytest` first. Try the gate on your own file with `python3 -m review.gate path/to/file.py`.

## The migration steps it models

1. **Pass through.** The facade forwards everything to the monolith.
2. **Backfill.** Copy existing parts into the new inventory service. Running it twice does not overwrite newer data.
3. **Shadow.** Reads still come from the monolith; the facade also asks the new service and counts mismatches at `/_parity`. A test changes the monolith directly and the report shows the difference with both values.
4. **Cut over writes, then reads.** The facade refuses to read from the new service while writes still go to the monolith, because that would serve stale stock. A per-sku percentage uses a stable hash so a sku does not flip between services.
5. **Keep the monolith current.** The new service writes an event to an outbox in the same transaction as the stock change. A relay publishes it and marks it only after the publish succeeds; the consumer ignores duplicates and older versions.

## The review gate

`review/gate.py` reads Python with `ast` and reports: imports of modules that do not exist, attributes that do not exist on standard library modules, SQL built from strings, hardcoded secrets, swallowed errors, `eval`, `os.system` and `shell=True`, and imports that cross a layer (`services` may not import `legacy`). Tests hold nine bad snippets and five good ones, and the lab runs the gate on itself.

## Layout

```
legacy/      the monolith stand-in: parts, orders and stock in one transaction
services/    inventory service with outbox, and the event consumer
facade/      strangler facade, modes, parity report
review/      the gate
common.py    small JSON over HTTP helpers
tests/       migration steps and gate cases
```

## Limits

- One slice, one table, in-memory SQLite. No real broker: the relay calls a function. A real one would add retries, ordering per key and a dead-letter queue.
- Dual writes are avoided on purpose: only one side takes writes at a time. Reverse sync (new to monolith for reads of monolith-owned data) is only the one consumer shown.
- The gate is pattern based. It misses wrong logic, bad algorithms and anything that needs data flow analysis. It is a first filter before human review, not a replacement.
- Not a claim of experience migrating a production monolith.

MIT license.
