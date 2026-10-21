"""CLI: preview / install the pi-agent config (models.json) for llama-swap."""

import argparse

from .. import GenesisWorker


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the pi-agent models.json config for llama-swap."
    )
    parser.parse_args()  # exits 0 on --help before any side effects
    worker = GenesisWorker()
    svc = worker.service("llama_swap")
    data = svc.export_for_agent(catalog=worker.catalog())
    print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
