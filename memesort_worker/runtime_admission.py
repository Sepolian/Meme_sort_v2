from __future__ import annotations

import ctypes
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from .runtime_manifest import RuntimeManifest


VK_SUCCESS = 0
VK_STRUCTURE_TYPE_APPLICATION_INFO = 0
VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO = 1
VK_API_VERSION_1_0 = 1 << 22
VK_MAX_PHYSICAL_DEVICE_NAME_SIZE = 256
_VK_PHYSICAL_DEVICE_PROPERTIES_BUFFER_SIZE = 4096
_VK_VENDOR_ID_OFFSET = 8
_VK_DEVICE_ID_OFFSET = 12
_VK_DEVICE_NAME_OFFSET = 20


class RuntimeAdmissionError(RuntimeError):
    pass


class _VkApplicationInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_uint32),
        ("pNext", ctypes.c_void_p),
        ("pApplicationName", ctypes.c_char_p),
        ("applicationVersion", ctypes.c_uint32),
        ("pEngineName", ctypes.c_char_p),
        ("engineVersion", ctypes.c_uint32),
        ("apiVersion", ctypes.c_uint32),
    ]


class _VkInstanceCreateInfo(ctypes.Structure):
    _fields_ = [
        ("sType", ctypes.c_uint32),
        ("pNext", ctypes.c_void_p),
        ("flags", ctypes.c_uint32),
        ("pApplicationInfo", ctypes.POINTER(_VkApplicationInfo)),
        ("enabledLayerCount", ctypes.c_uint32),
        ("ppEnabledLayerNames", ctypes.c_void_p),
        ("enabledExtensionCount", ctypes.c_uint32),
        ("ppEnabledExtensionNames", ctypes.c_void_p),
    ]


@dataclass(frozen=True)
class VulkanDeviceInfo:
    index: int
    vendor_id: int
    vendor_name: str
    device_id: int
    device_name: str

    @property
    def vendor_id_hex(self) -> str:
        return f"0x{self.vendor_id:04x}"


def validate_windows_x64(manifest: RuntimeManifest) -> None:
    if sys.platform != "win32":
        raise RuntimeAdmissionError(
            f"MemeSort's Vulkan runtime supports Windows only, not {sys.platform}."
        )
    architecture = platform.machine().lower()
    if architecture not in {"amd64", "x86_64"}:
        raise RuntimeAdmissionError(
            f"MemeSort's Vulkan runtime requires Windows x64, not {architecture or 'unknown'}."
        )
    if manifest.platform.os != "windows" or manifest.platform.architecture != "x86_64":
        raise RuntimeAdmissionError("Runtime manifest platform does not match Windows x64.")


def validate_pinned_runtime_files(manifest: RuntimeManifest) -> None:
    _require_exact_file(
        manifest.llama_server_path,
        manifest.llama_cpp.executable,
        expected_size=None,
        label="llama-server executable",
    )
    _require_exact_file(
        manifest.main_model_path,
        manifest.model.main.filename,
        expected_size=manifest.model.main.size_bytes,
        label="main GGUF",
    )
    _require_exact_file(
        manifest.projector_path,
        manifest.model.projector.filename,
        expected_size=manifest.model.projector.size_bytes,
        label="multimodal projector",
    )


def probe_vulkan0(manifest: RuntimeManifest) -> VulkanDeviceInfo:
    validate_windows_x64(manifest)
    try:
        loader = ctypes.WinDLL("vulkan-1.dll")
    except (AttributeError, OSError) as exc:
        raise RuntimeAdmissionError(
            "Windows Vulkan loader vulkan-1.dll is unavailable. Install a supported GPU driver."
        ) from exc

    create_instance = loader.vkCreateInstance
    create_instance.argtypes = [
        ctypes.POINTER(_VkInstanceCreateInfo),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    create_instance.restype = ctypes.c_int32
    enumerate_devices = loader.vkEnumeratePhysicalDevices
    enumerate_devices.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    enumerate_devices.restype = ctypes.c_int32
    get_properties = loader.vkGetPhysicalDeviceProperties
    get_properties.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    get_properties.restype = None
    destroy_instance = loader.vkDestroyInstance
    destroy_instance.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    destroy_instance.restype = None

    app_name = b"MemeSort Vulkan admission"
    app_info = _VkApplicationInfo(
        sType=VK_STRUCTURE_TYPE_APPLICATION_INFO,
        pNext=None,
        pApplicationName=app_name,
        applicationVersion=1,
        pEngineName=None,
        engineVersion=0,
        apiVersion=VK_API_VERSION_1_0,
    )
    create_info = _VkInstanceCreateInfo(
        sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO,
        pNext=None,
        flags=0,
        pApplicationInfo=ctypes.pointer(app_info),
        enabledLayerCount=0,
        ppEnabledLayerNames=None,
        enabledExtensionCount=0,
        ppEnabledExtensionNames=None,
    )
    instance = ctypes.c_void_p()
    result = int(create_instance(ctypes.byref(create_info), None, ctypes.byref(instance)))
    if result != VK_SUCCESS:
        raise RuntimeAdmissionError(f"vkCreateInstance failed with VkResult {result}.")

    try:
        count = ctypes.c_uint32(0)
        _check_vk_result(
            enumerate_devices(instance, ctypes.byref(count), None),
            "vkEnumeratePhysicalDevices(count)",
        )
        if count.value == 0:
            raise RuntimeAdmissionError("Vulkan reported no physical GPU devices.")
        devices = (ctypes.c_void_p * count.value)()
        _check_vk_result(
            enumerate_devices(instance, ctypes.byref(count), devices),
            "vkEnumeratePhysicalDevices(list)",
        )
        properties = ctypes.create_string_buffer(
            _VK_PHYSICAL_DEVICE_PROPERTIES_BUFFER_SIZE
        )
        get_properties(devices[0], properties)
        vendor_id, device_id, device_name = _parse_physical_device_properties(properties)
    finally:
        destroy_instance(instance, None)

    vendor_name = _supported_vendor_name(manifest, vendor_id)
    return VulkanDeviceInfo(
        index=0,
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        device_id=device_id,
        device_name=device_name,
    )


def crosscheck_llama_vulkan0(
    device: VulkanDeviceInfo,
    llama_device_output: str,
    expected_label: str,
) -> str:
    prefix = f"{expected_label}:"
    line = next(
        (
            candidate.strip()
            for candidate in llama_device_output.splitlines()
            if candidate.strip().lower().startswith(prefix.lower())
        ),
        None,
    )
    if line is None:
        raise RuntimeAdmissionError(
            f"llama.cpp did not enumerate the required {expected_label} device."
        )
    if device.device_name.casefold() not in line.casefold():
        raise RuntimeAdmissionError(
            f"Vulkan API {expected_label} is {device.device_name!r}, but llama.cpp reported "
            f"{line!r}; refusing an ambiguous device mapping."
        )
    return line


def _supported_vendor_name(manifest: RuntimeManifest, vendor_id: int) -> str:
    vendor_id_hex = f"0x{vendor_id:04x}"
    for vendor_name, supported_id in manifest.platform.vendor_ids.items():
        if supported_id.casefold() == vendor_id_hex:
            return vendor_name
    supported = ", ".join(
        f"{name}={vendor_id}" for name, vendor_id in manifest.platform.vendor_ids.items()
    )
    raise RuntimeAdmissionError(
        f"Vulkan0 vendor ID {vendor_id_hex} is unsupported; expected one of {supported}."
    )


def _parse_physical_device_properties(
    properties: ctypes.Array[ctypes.c_char],
) -> tuple[int, int, str]:
    raw = properties.raw
    vendor_id = int.from_bytes(
        raw[_VK_VENDOR_ID_OFFSET : _VK_VENDOR_ID_OFFSET + 4], "little"
    )
    device_id = int.from_bytes(
        raw[_VK_DEVICE_ID_OFFSET : _VK_DEVICE_ID_OFFSET + 4], "little"
    )
    name_bytes = raw[
        _VK_DEVICE_NAME_OFFSET : _VK_DEVICE_NAME_OFFSET
        + VK_MAX_PHYSICAL_DEVICE_NAME_SIZE
    ].split(b"\0", 1)[0]
    device_name = name_bytes.decode("utf-8", errors="replace").strip()
    if not device_name:
        raise RuntimeAdmissionError("Vulkan0 returned an empty physical-device name.")
    return vendor_id, device_id, device_name


def _require_exact_file(
    path: Path,
    expected_filename: str,
    expected_size: int | None,
    label: str,
) -> None:
    if path.name != expected_filename:
        raise RuntimeAdmissionError(
            f"Pinned {label} filename mismatch: expected {expected_filename}, got {path.name}."
        )
    if not path.is_file():
        raise RuntimeAdmissionError(f"Pinned {label} does not exist: {path}")
    if expected_size is not None:
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise RuntimeAdmissionError(
                f"Pinned {label} size mismatch for {path.name}: expected "
                f"{expected_size} bytes, got {actual_size}. Run setup to repair it."
            )


def _check_vk_result(result: int, operation: str) -> None:
    if int(result) != VK_SUCCESS:
        raise RuntimeAdmissionError(f"{operation} failed with VkResult {int(result)}.")
