from __future__ import annotations

import sqlite3
import unittest

from memesort_worker import job_queue


class JobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        job_queue.create_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_embedding_contract_and_lifecycle_are_owned_by_queue(self) -> None:
        job_queue.enqueue_embedding(
            self.conn,
            asset_id="asset-1",
            recipe_id="recipe-1",
            media_type="image/png",
            now="2026-07-18T00:00:00+00:00",
        )

        queue = job_queue.JobQueue(self.conn)
        requeued, retried, jobs = queue.prepare(max_jobs=1)

        self.assertEqual((0, 0), (requeued, retried))
        self.assertEqual(1, len(jobs))
        self.assertIs(job_queue.JobType.EMBED_ASSET, jobs[0].job_type)
        self.assertEqual(
            {
                "asset_id": "asset-1",
                "media_type": "image/png",
                "recipe_id": "recipe-1",
            },
            jobs[0].payload,
        )
        self.assertTrue(queue.claim(jobs[0]))
        queue.complete(jobs[0])
        status, attempts = self.conn.execute(
            "SELECT status, attempt_count FROM job WHERE id = ?",
            (jobs[0].job_id,),
        ).fetchone()
        self.assertEqual(("completed", 1), (status, attempts))


if __name__ == "__main__":
    unittest.main()
