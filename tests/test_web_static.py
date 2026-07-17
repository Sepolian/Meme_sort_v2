from __future__ import annotations

from io import BytesIO
import tempfile
import unittest
from pathlib import Path

from memesort_worker.webapp import create_app


STATIC_ROOT = Path(__file__).resolve().parents[1] / "memesort_worker" / "web_static"


class AssetDetailStylesTests(unittest.TestCase):
    def test_detail_disclosure_uses_theme_colors(self) -> None:
        styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn(".technical-info {", styles)
        disclosure_rule = styles.split(".technical-info {", 1)[1].split("}", 1)[0]
        self.assertIn("background: var(--surface);", disclosure_rule)
        self.assertIn("color: var(--ink);", disclosure_rule)
        self.assertNotIn("#fffaf0", disclosure_rule)

    def test_ocr_output_has_dedicated_detail_styles(self) -> None:
        app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
        styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn('class="ocr-result-card"', app_script)
        self.assertIn('class="ocr-text"', app_script)
        self.assertIn(".ocr-result-card {", styles)
        self.assertIn(".ocr-text {", styles)

    def test_removed_runtime_mutation_endpoints_return_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(str(Path(temp_dir) / "library"))
            try:
                for path in ("/api/runtime-settings", "/api/first-run"):
                    with self.subTest(path=path):
                        response: dict[str, str] = {}

                        def start_response(status, _headers):
                            response["status"] = status

                        body = b"".join(
                            app(
                                {
                                    "REQUEST_METHOD": "POST",
                                    "PATH_INFO": path,
                                    "QUERY_STRING": "",
                                    "CONTENT_LENGTH": "2",
                                    "wsgi.input": BytesIO(b"{}"),
                                },
                                start_response,
                            )
                        )
                        self.assertTrue(response["status"].startswith("404 "))
                        self.assertIn(b"Unknown API endpoint", body)
            finally:
                app.shutdown()


if __name__ == "__main__":
    unittest.main()
