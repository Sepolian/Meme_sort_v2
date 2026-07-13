from __future__ import annotations

import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
