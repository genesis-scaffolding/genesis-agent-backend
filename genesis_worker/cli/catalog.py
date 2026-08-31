"""CLI: rescan the catalog and print summary."""

from .. import GenesisWorker


def main() -> int:
    worker = GenesisWorker()
    catalog = worker.rescan_catalog()
    by_source = catalog.by_source()
    total = sum(len(v) for v in by_source.values())
    print(f"entries: {total}")
    for source, entries in by_source.items():
        for entry in entries:
            print(f"  {source}: {entry.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
