"""Fake amdsmi module for vLLM in WSL/DXG environments.

vLLM's `vllm/platforms/rocm.py` does explicit
    from amdsmi import (
        AmdSmiException, AmdSmiMemoryType,
        amdsmi_get_gpu_asic_info,
        amdsmi_get_gpu_device_uuid,
        amdsmi_get_processor_handles,
        amdsmi_get_gpu_memory_total, amdsmi_init, amdsmi_shut_down,
        amdsmi_topo_get_link_type, amdsmi_topo_get_numa_node_number,
    )
and uses these for GPU probing/identification only (not for actual inference).

Real amdsmi requires /dev/kfd which doesn't exist in WSL. This fake provides
every symbol vLLM imports, all returning successful no-op values, so vLLM's
RocmPlatform path activates. torch.cuda (via librocdxg → /dev/dxg) handles
real GPU access.
"""

from enum import IntEnum


class AmdSmiException(Exception):
    """Top-level exception class vLLM imports."""

    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        self.err_code: int = 0
        self.err_info: str = "fake-amdsmi"

    def get_error_info(self) -> str:
        return "fake-amdsmi"


# Older alias some code paths use
AmdSmiLibraryException = AmdSmiException


class AmdSmiMemoryType(IntEnum):
    VRAM = 0


class _FakeHandle:
    def __init__(self, idx: int) -> None:
        self.idx: int = idx

    def __repr__(self) -> str:
        return f"<FakeAMDSMIHandle idx={self.idx}>"


_FAKE_HANDLES: list[_FakeHandle] = [_FakeHandle(0)]


def amdsmi_init(flag: int = 0) -> None:
    return None


def amdsmi_shut_down() -> None:
    return None


def amdsmi_get_processor_handles() -> list[_FakeHandle]:
    return _FAKE_HANDLES


def amdsmi_get_gpu_asic_info(handle: object) -> dict[str, object]:
    """Minimal ASIC info — vLLM reads `target_graphics_version` for arch."""
    _ = handle
    return {
        "market_name": "AMD Radeon(TM) 890M Graphics",
        "vendor_id": 0x1002,
        "device_id": 0x150E,
        "rev_id": 0,
        "asic_serial": "0000000000000000",
        "oam_id": 0,
        "num_of_compute_units": 8,
        "target_graphics_version": "gfx1150",  # vLLM expects string form
        "subsystem_id": 0,
        "subsystem_vendor_id": 0x1002,
    }


def amdsmi_get_gpu_device_uuid(handle: object) -> str:
    _ = handle
    return "00000000-0000-0000-0000-000000000000"


def amdsmi_get_gpu_memory_total(handle: object, memory_type: AmdSmiMemoryType) -> int:
    _ = handle
    _ = memory_type
    # WSL is capped at 32 GiB; do not expose the larger host UMA aperture to vLLM.
    return 32 * 1024**3


def amdsmi_topo_get_link_type(handle_a: object, handle_b: object) -> dict[str, object]:
    _ = handle_a
    _ = handle_b
    # Single-GPU: any link query is to "self" or N/A.
    return {"type": 0, "hops": 0}


def amdsmi_topo_get_numa_node_number(handle: object) -> int:
    _ = handle
    return 0
