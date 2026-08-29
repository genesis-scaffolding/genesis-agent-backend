"""CLI: REPL driving an HF acquire session from stdin.

Used by Phase 10 retirement; not wired into the Makefile in v1.
"""

from .. import GenesisWorker
from ..contracts import AcquireChoice, AcquireStateKind
from ..sources.huggingface import HfAcquireChoice, HfAcquireView


def main() -> int:
    repo_id = input("repo (org/name): ").strip()
    if not repo_id:
        return 1
    worker = GenesisWorker()
    session = worker.start_acquire("huggingface", repo_id)
    while True:
        view = worker.acquire_step(session)
        print(f"[{view.kind}] {view.title}")
        if view.kind == AcquireStateKind.COMPLETE:
            return 0
        if view.kind in (AcquireStateKind.FAILED, AcquireStateKind.CANCELLED):
            return 1
        if view.kind == AcquireStateKind.CONFIRMING:
            ans = input("confirm? [y/N]: ").strip().lower()
            worker.submit_acquire(session, AcquireChoice(confirm=ans == "y"))
        elif view.kind == AcquireStateKind.SELECTING and isinstance(view, HfAcquireView):
            for group in view.targets:
                print(f"  {group.label}: {group.paths}")
                idx = int(input("index: "))
                worker.submit_acquire(
                    session,
                    HfAcquireChoice(main_indexes=[idx]),
                )
        else:
            input("press enter to refresh...")


if __name__ == "__main__":
    raise SystemExit(main())
