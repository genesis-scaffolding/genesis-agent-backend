"""CLI: REPL driving an HF acquire session from stdin.

Used by Phase 10 retirement; not wired into the Makefile in v1.
"""

from .. import GenesisWorker
from ..contracts import AcquireChoice


def main() -> int:
    repo_id = input("repo (org/name): ").strip()
    if not repo_id:
        return 1
    worker = GenesisWorker()
    session = worker.start_acquire("huggingface", repo_id)
    while True:
        step = worker.acquire_step(session)
        print(f"[{step.kind}] {step.title}")
        if step.kind == "complete":
            return 0
        if step.kind in ("failed", "cancelled"):
            return 1
        if step.kind == "confirm_storage":
            ans = input("confirm? [y/N]: ").strip().lower()
            worker.submit_acquire(session, AcquireChoice(confirm=ans == "y"))
        elif step.kind == "select_files" and step.file_groups:
            selections: dict[str, str] = {}
            for group in step.file_groups:
                print(f"  {group.label}: {group.paths}")
                idx = int(input("index: "))
                selections[group.role] = group.paths[idx]
            worker.submit_acquire(session, AcquireChoice(main_index=0))
        else:
            input("press enter to refresh...")


if __name__ == "__main__":
    raise SystemExit(main())