"""CLI: preview / install the pi-agent config (models.json) for llama-swap."""

from .. import GenesisWorker


def main() -> int:
    worker = GenesisWorker()
    svc = worker.service("llama_swap")
    data = svc.export_for_agent(catalog=worker.catalog())
    print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
