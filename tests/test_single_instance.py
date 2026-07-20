from __future__ import annotations

import sys
import unittest
import uuid

from memesort_worker.single_instance import SingleInstanceGuard


@unittest.skipUnless(sys.platform == "win32", "Named mutex single-instance is Windows-only")
class SingleInstanceGuardTests(unittest.TestCase):
    def _unique_name(self) -> str:
        return f"MemeSortTest-{uuid.uuid4().hex}"

    def test_first_guard_is_primary(self) -> None:
        guard = SingleInstanceGuard(self._unique_name())
        try:
            self.assertTrue(guard.acquire())
            self.assertTrue(guard.is_primary)
        finally:
            guard.release()

    def test_second_guard_detects_the_running_instance(self) -> None:
        name = self._unique_name()
        first = SingleInstanceGuard(name)
        second = SingleInstanceGuard(name)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertFalse(second.is_primary)
        finally:
            second.release()
            first.release()

    def test_releasing_the_primary_allows_reacquisition(self) -> None:
        name = self._unique_name()
        first = SingleInstanceGuard(name)
        self.assertTrue(first.acquire())
        first.release()

        second = SingleInstanceGuard(name)
        try:
            self.assertTrue(second.acquire())
        finally:
            second.release()


if __name__ == "__main__":
    unittest.main()
