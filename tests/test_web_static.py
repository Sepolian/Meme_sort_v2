from __future__ import annotations

from io import BytesIO
import tempfile
import unittest
from pathlib import Path

from memesort_worker.webapp import create_app


STATIC_ROOT = Path(__file__).resolve().parents[1] / "memesort_worker" / "web_static"


class AssetDetailStylesTests(unittest.TestCase):
    def test_empty_library_message_spans_the_masonry_wall(self) -> None:
        styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn(
            ".asset-grid > .state-message { column-span: all; }",
            styles,
        )

    def test_queue_monitor_can_shrink_without_horizontal_overflow(self) -> None:
        styles = (STATIC_ROOT / "styles.css").read_text(encoding="utf-8")

        self.assertIn(
            ".queue-monitor { display: grid; grid-template-columns: minmax(150px, 1fr) auto auto minmax(0, 1.6fr) auto;",
            styles,
        )
        self.assertIn(".queue-monitor > * { min-width: 0; }", styles)
        self.assertIn(
            ".queue-progress p { margin: 5px 0 0; overflow-wrap: anywhere; }",
            styles,
        )

    def test_leaving_search_tab_cancels_each_active_search_request(self) -> None:
        app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn('previousConfig?.tabId === "searchTab"', app_script)
        self.assertIn("cancelActiveSearches();", app_script)
        self.assertIn("cancelSearch(previousConfig.searchMode);", app_script)
        self.assertIn('api("/api/search/cancel"', app_script)
        self.assertIn("active.controller.abort()", app_script)

    def test_semantic_search_uses_addressable_sidebar_subpages(self) -> None:
        markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="searchNavGroup" class="nav-group"', markup)
        for route in ("/search", "/search/text", "/search/image", "/search/similar"):
            with self.subTest(route=route):
                self.assertIn(f'data-route="{route}"', markup)
                self.assertIn(f'"{route}"', app_script)
        self.assertIn('window.history.pushState({}, "",', app_script)
        self.assertIn('window.addEventListener("popstate"', app_script)

    def test_search_result_opens_accessible_asset_detail_modal(self) -> None:
        markup = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
        app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="searchAssetModal"', markup)
        self.assertIn('role="dialog" aria-modal="true"', markup)
        self.assertIn("openSearchAssetModal(result.asset_id, item)", app_script)
        self.assertIn('event.key === "Escape"', app_script)
        self.assertNotIn('switchTab("libraryTab")', app_script)

    def test_asset_detail_can_navigate_to_find_similar(self) -> None:
        app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn('class="find-similar-btn"', app_script)
        self.assertIn(
            "navigate(`/search/similar?asset=${encodeURIComponent(button.dataset.assetId)}`)",
            app_script,
        )

    def test_search_subroute_refresh_serves_the_app_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(str(Path(temp_dir) / "library"))
            try:
                response: dict[str, str] = {}

                def start_response(status, _headers):
                    response["status"] = status

                body = b"".join(
                    app(
                        {
                            "REQUEST_METHOD": "GET",
                            "PATH_INFO": "/search/text",
                            "QUERY_STRING": "",
                            "CONTENT_LENGTH": "0",
                            "wsgi.input": BytesIO(b""),
                        },
                        start_response,
                    )
                )
                self.assertTrue(response["status"].startswith("200 "))
                self.assertIn(b'id="searchNavGroup"', body)
            finally:
                app.shutdown()

    def test_runtime_ui_consumes_one_read_only_runtime_descriptor(self) -> None:
        app_script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

        self.assertIn("runtime: null", app_script)
        self.assertIn("state.runtime = payload.runtime || null", app_script)
        for legacy_name in (
            "runtimeProfiles",
            "modelVariants",
            "runtimeSettings",
            "selectedProfile",
            "selectedModelVariant",
            "renderProfiles",
            "renderModelVariants",
        ):
            with self.subTest(legacy_name=legacy_name):
                self.assertNotIn(legacy_name, app_script)

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
