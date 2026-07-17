from __future__ import annotations

import ctypes
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from memesort_worker.runtime_admission import (
    RuntimeAdmissionError,
    VulkanDeviceInfo,
    _parse_physical_device_properties,
    _supported_vendor_name,
    crosscheck_llama_vulkan0,
    validate_pinned_runtime_files,
)
from memesort_worker.runtime_manifest import load_runtime_manifest


class RuntimeAdmissionTests(unittest.TestCase):
    def test_parses_pci_ids_and_name_from_vulkan_properties(self) -> None:
        properties = ctypes.create_string_buffer(4096)
        properties[8:12] = (0x1002).to_bytes(4, "little")
        properties[12:16] = (0x15BF).to_bytes(4, "little")
        device_name = b"AMD Radeon 780M Graphics\0"
        properties[20 : 20 + len(device_name)] = device_name

        vendor_id, device_id, name = _parse_physical_device_properties(properties)

        self.assertEqual(0x1002, vendor_id)
        self.assertEqual(0x15BF, device_id)
        self.assertEqual("AMD Radeon 780M Graphics", name)

    def test_accepts_only_manifest_vendor_ids(self) -> None:
        manifest = load_runtime_manifest()

        self.assertEqual("amd", _supported_vendor_name(manifest, 0x1002))
        self.assertEqual("intel", _supported_vendor_name(manifest, 0x8086))
        self.assertEqual("nvidia", _supported_vendor_name(manifest, 0x10DE))
        with self.assertRaisesRegex(RuntimeAdmissionError, "0x1234 is unsupported"):
            _supported_vendor_name(manifest, 0x1234)

    def test_crosschecks_vulkan_api_device_against_llama_vulkan0(self) -> None:
        device = VulkanDeviceInfo(
            index=0,
            vendor_id=0x1002,
            vendor_name="amd",
            device_id=0x15BF,
            device_name="AMD Radeon 780M Graphics",
        )

        line = crosscheck_llama_vulkan0(
            device,
            "Vulkan0: AMD Radeon 780M Graphics (16384 MiB)",
            "Vulkan0",
        )

        self.assertTrue(line.startswith("Vulkan0:"))
        with self.assertRaisesRegex(RuntimeAdmissionError, "ambiguous device mapping"):
            crosscheck_llama_vulkan0(
                device,
                "Vulkan0: NVIDIA GeForce RTX 4090",
                "Vulkan0",
            )

    def test_runtime_files_require_exact_manifest_names_and_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest = load_runtime_manifest()
            manifest = replace(manifest, source_path=root / "runtime-manifest.json")
            server = manifest.llama_server_path
            main = manifest.main_model_path
            projector = manifest.projector_path
            server.parent.mkdir(parents=True)
            main.parent.mkdir(parents=True)
            server.write_bytes(b"server")
            main.write_bytes(b"main")
            projector.write_bytes(b"projector")
            manifest = replace(
                manifest,
                model=replace(
                    manifest.model,
                    main=replace(manifest.model.main, size_bytes=4),
                    projector=replace(manifest.model.projector, size_bytes=9),
                ),
            )

            validate_pinned_runtime_files(manifest)
            main.write_bytes(b"wrong")
            with self.assertRaisesRegex(RuntimeAdmissionError, "size mismatch"):
                validate_pinned_runtime_files(manifest)


if __name__ == "__main__":
    unittest.main()
