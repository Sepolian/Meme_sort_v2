from __future__ import annotations

import threading
import time
import unittest
import uuid

from memesort_worker.inference_service import (
    InferenceCancelledError,
    InferenceScheduler,
    search_inference_request,
)


class InferenceServiceTests(unittest.TestCase):
    def test_search_runs_before_queued_background_without_preemption(self) -> None:
        scheduler = InferenceScheduler()
        running = threading.Event()
        release = threading.Event()
        order: list[str] = []
        errors: list[BaseException] = []

        def submit_background(name: str, block: bool = False) -> None:
            try:
                def operation() -> str:
                    order.append(name)
                    if block:
                        running.set()
                        self.assertTrue(release.wait(2))
                    return name

                scheduler.submit(operation)
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=submit_background, args=("background-1", True))
        second = threading.Thread(target=submit_background, args=("background-2",))
        first.start()
        self.assertTrue(running.wait(2))
        second.start()
        self._wait_for_queue(scheduler, "_background_queue", 1)

        request_id = str(uuid.uuid4())

        def submit_search() -> None:
            try:
                with search_inference_request(scheduler, request_id):
                    scheduler.submit(lambda: order.append("search"))
            except BaseException as exc:
                errors.append(exc)

        search = threading.Thread(target=submit_search)
        search.start()
        self._wait_for_queue(scheduler, "_search_queue", 1)
        self.assertEqual(["background-1"], order)
        release.set()
        for thread in (first, second, search):
            thread.join(2)

        self.assertEqual([], errors)
        self.assertEqual(["background-1", "search", "background-2"], order)

    def test_pending_search_can_be_cancelled_independently(self) -> None:
        scheduler = InferenceScheduler()
        running = threading.Event()
        release = threading.Event()
        first = threading.Thread(
            target=lambda: scheduler.submit(
                lambda: (running.set(), release.wait(2))
            )
        )
        first.start()
        self.assertTrue(running.wait(2))

        cancelled_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        outcomes: dict[str, str] = {}

        def submit_search(request_id: str) -> None:
            try:
                with search_inference_request(scheduler, request_id):
                    scheduler.submit(lambda: outcomes.__setitem__(request_id, "ran"))
            except InferenceCancelledError:
                outcomes[request_id] = "cancelled"

        cancelled = threading.Thread(target=submit_search, args=(cancelled_id,))
        other = threading.Thread(target=submit_search, args=(other_id,))
        cancelled.start()
        other.start()
        self._wait_for_queue(scheduler, "_search_queue", 2)
        self.assertTrue(scheduler.cancel(cancelled_id))
        release.set()
        for thread in (first, cancelled, other):
            thread.join(2)

        self.assertEqual("cancelled", outcomes[cancelled_id])
        self.assertEqual("ran", outcomes[other_id])

    def test_cancelled_running_work_finishes_non_preemptively_then_discards(self) -> None:
        scheduler = InferenceScheduler()
        request_id = str(uuid.uuid4())
        running = threading.Event()
        release = threading.Event()
        outcome: list[str] = []

        def submit_search() -> None:
            try:
                with search_inference_request(scheduler, request_id):
                    scheduler.submit(
                        lambda: (running.set(), release.wait(2), outcome.append("finished"))
                    )
            except InferenceCancelledError:
                outcome.append("cancelled")

        thread = threading.Thread(target=submit_search)
        thread.start()
        self.assertTrue(running.wait(2))
        self.assertTrue(scheduler.cancel(request_id))
        self.assertEqual([], outcome)
        release.set()
        thread.join(2)

        self.assertEqual(["finished", "cancelled"], outcome)

    def _wait_for_queue(
        self,
        scheduler: InferenceScheduler,
        attribute: str,
        size: int,
    ) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with scheduler._condition:
                if len(getattr(scheduler, attribute)) == size:
                    return
            time.sleep(0.01)
        self.fail(f"{attribute} did not reach size {size}")


if __name__ == "__main__":
    unittest.main()
