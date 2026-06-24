from __future__ import annotations

"""
PROTOTYPE ONLY.

Logic-branch prototype chosen intentionally:
the question is about state transitions and data shape, not UI design.
"""

import json

from .prototype_indexing_logic import (
    complete_next_pending_job,
    fail_next_pending_embedding_job,
    import_source_pool,
    initial_state,
    remove_source_record,
    renderable_state,
    retry_failed_jobs,
    seed_demo_sources,
    switch_active_recipe,
)


BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
CLEAR = "\x1b[2J\x1b[H"


def main() -> None:
    state = initial_state()
    while True:
        print(CLEAR, end="")
        _render_frame(state)
        command = input("> ").strip()
        if not command:
            continue

        lowered = command.lower()
        if lowered in {"q", "quit"}:
            break
        if lowered in {"s", "seed"}:
            state = seed_demo_sources(state)
            continue
        if lowered in {"i", "import"}:
            state = import_source_pool(state)
            continue
        if lowered in {"t", "tick"}:
            state = complete_next_pending_job(state)
            continue
        if lowered in {"f", "fail"}:
            state = fail_next_pending_embedding_job(state)
            continue
        if lowered in {"r", "retry"}:
            state = retry_failed_jobs(state)
            continue
        if lowered in {"w", "switch"}:
            state = switch_active_recipe(state)
            continue
        if lowered in {"d", "reset"}:
            state = initial_state()
            continue
        if lowered.startswith("x "):
            parts = lowered.split()
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                state = remove_source_record(state, int(parts[1]), int(parts[2]))
            else:
                _append_input_error(state, "Use `x <asset-index> <source-index>`.")
            continue

        _append_input_error(state, f"Unknown command: {command}")


def _render_frame(state) -> None:
    view = renderable_state(state)
    print(f"{BOLD}Prototype Question{RESET}")
    print(
        "Does the import/index/job state model feel right when duplicate imports, "
        "recipe switches, failures, retries, and source deletions happen?"
    )
    print()

    print(f"{BOLD}Current State{RESET}")
    print(json.dumps(view, indent=2))
    print()

    print(f"{BOLD}Commands{RESET}")
    print(f"{BOLD}s{RESET} {DIM}seed demo sources{RESET}")
    print(f"{BOLD}i{RESET} {DIM}import the whole source pool{RESET}")
    print(f"{BOLD}t{RESET} {DIM}complete the next pending job{RESET}")
    print(f"{BOLD}f{RESET} {DIM}fail the next pending embedding job{RESET}")
    print(f"{BOLD}r{RESET} {DIM}retry all failed jobs{RESET}")
    print(f"{BOLD}w{RESET} {DIM}switch active recipe between 2B and 8B{RESET}")
    print(f"{BOLD}x a s{RESET} {DIM}remove source record by asset/source index, e.g. x 0 1{RESET}")
    print(f"{BOLD}d{RESET} {DIM}reset state{RESET}")
    print(f"{BOLD}q{RESET} {DIM}quit{RESET}")


def _append_input_error(state, message: str) -> None:
    state.event_log.append(message)


if __name__ == "__main__":
    main()
