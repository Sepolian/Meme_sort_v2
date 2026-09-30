from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts import evaluate_gif_search, evaluate_still_image_search


CASES = (
    (evaluate_still_image_search, "still-image", "example", "labels/labels.json", "memesort_eval_", True),
    (evaluate_gif_search, "GIF", "gif_example", "labels/gif_labels.json", "memesort_gif_eval_", False),
)


class RetrievalEvaluationCliTests(unittest.TestCase):
    def test_defaults_and_explicit_options_reach_evaluator(self) -> None:
        for module, _, dataset, labels, prefix, still in CASES:
            defaults = {
                "dataset_dir": dataset,
                "labels_path": labels,
                "query_fields": ["ocr_translation", "people_appearance", "objects", "scene_context", "themes"],
                "top_k": 10,
                "keep_library": False,
                "output": None,
            }
            explicit = {
                "dataset_dir": "custom assets",
                "labels_path": "custom labels.json",
                "query_fields": ["objects", "themes"],
                "top_k": 3,
                "keep_library": True,
                "output": "custom report.json",
            }
            argv = [
                "--dataset-dir", "custom assets", "--labels-path", "custom labels.json",
                "--query-fields", "objects", "themes", "--top-k", "3",
                "--keep-library", "--output", "custom report.json",
            ]
            for args, expected in (([], defaults), (argv, explicit)):
                with self.subTest(command=module.__name__, args=args):
                    with (
                        patch.object(sys, "argv", [module.__name__, *args]),
                        patch.object(module, "run_evaluation") as evaluate,
                    ):
                        module.main()
                    evaluate.assert_called_once_with(
                        **expected,
                        temp_prefix=prefix,
                        reject_duplicate_filenames=still,
                        include_preprocess=still,
                    )

    def test_invalid_types_and_argument_counts_are_rejected(self) -> None:
        for module, *_ in CASES:
            for argv in (
                ["--dataset-dir"], ["--labels-path"], ["--query-fields"],
                ["--top-k"], ["--top-k", "three"], ["--keep-library", "false"],
                ["--output"], ["--dataset-dir", "one", "two"],
            ):
                with self.subTest(command=module.__name__, args=argv):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        module.build_parser().parse_args(argv)
                    self.assertEqual(2, error.exception.code)

    def test_script_and_module_help_describe_the_media_and_common_options(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(root), env.get("PYTHONPATH"))))
        for module, media, *_ in CASES:
            script = root / "scripts" / f"{module.__name__.rsplit('.', 1)[1]}.py"
            for entry in ([str(script)], ["-m", module.__name__]):
                with self.subTest(entry=entry):
                    result = subprocess.run(
                        [sys.executable, *entry, "--help"],
                        cwd=root, env=env, capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn(f"Evaluate {media} retrieval against labeled examples", result.stdout)
                    self.assertIn(f"Directory containing {media} assets", result.stdout)
                    for option in ("dataset-dir", "labels-path", "query-fields", "top-k", "keep-library", "output"):
                        self.assertIn(f"--{option}", result.stdout)


if __name__ == "__main__":
    unittest.main()
