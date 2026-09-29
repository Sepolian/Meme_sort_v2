import unittest
from unittest.mock import patch

from memesort_worker import recipe_provider
from memesort_worker.runtime_manifest import load_runtime_manifest


class DefaultProviderTests(unittest.TestCase):
    def test_retries_failed_load_and_build_then_reuses_success(self) -> None:
        manifest = load_runtime_manifest()
        provider = recipe_provider.from_manifest(manifest)
        recipe_provider.default_provider.cache_clear()
        self.addCleanup(recipe_provider.default_provider.cache_clear)

        with (
            patch.object(
                recipe_provider,
                "load_runtime_manifest",
                side_effect=[RuntimeError("read failed"), manifest, manifest],
            ) as load,
            patch.object(
                recipe_provider,
                "from_manifest",
                side_effect=[ValueError("build failed"), provider],
            ) as build,
        ):
            with self.assertRaisesRegex(RuntimeError, "read failed"):
                recipe_provider.default_provider()
            with self.assertRaisesRegex(ValueError, "build failed"):
                recipe_provider.default_provider()

            self.assertIs(provider, recipe_provider.default_provider())
            self.assertIs(provider, recipe_provider.default_provider())
            self.assertEqual(3, load.call_count)
            self.assertEqual(2, build.call_count)


if __name__ == "__main__":
    unittest.main()
