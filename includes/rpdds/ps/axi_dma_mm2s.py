"""
Minimal AXI DMA MM2S direct-register-mode control.

This helper intentionally avoids Linux DMAEngine dependencies. The AXI DMA core
is controlled through its AXI-Lite registers mapped from /dev/mem.
"""

from __future__ import annotations

import mmap
import os
import struct
import time


class AXIDMAError(RuntimeError):
    """Raised when AXI DMA control or transfer status indicates a failure."""


class _MMIORegion:
    def __init__(self, base_addr: int, map_size: int) -> None:
        self.base_addr = base_addr
        self.map_size = map_size
        self._fd: int | None = None
        self._mmap: mmap.mmap | None = None
        self._page_offset = 0

    def open(self) -> None:
        page_size = mmap.PAGESIZE
        page_base = self.base_addr & ~(page_size - 1)
        self._page_offset = self.base_addr - page_base
        map_span = ((self.map_size + self._page_offset + page_size - 1) // page_size) * page_size

        self._fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self._mmap = mmap.mmap(
            self._fd,
            map_span,
            flags=mmap.MAP_SHARED,
            prot=mmap.PROT_READ | mmap.PROT_WRITE,
            offset=page_base,
        )

    def close(self) -> None:
        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @property
    def mm(self) -> mmap.mmap:
        if self._mmap is None:
            raise RuntimeError("MMIO region is not open")
        return self._mmap

    def read_u32(self, offset: int) -> int:
        self.mm.seek(self._page_offset + offset)
        return struct.unpack("<I", self.mm.read(4))[0]

    def write_u32(self, offset: int, value: int) -> None:
        self.mm.seek(self._page_offset + offset)
        self.mm.write(struct.pack("<I", value & 0xFFFFFFFF))


class AXIDMAMM2S:
    """
    AXI DMA MM2S controller in direct register mode.

    The transfer flow follows PG021:
    1. Reset the engine if needed.
    2. Set RS=1.
    3. Program MM2S_SA / MM2S_SA_MSB.
    4. Program MM2S_LENGTH last.
    """

    MM2S_DMACR = 0x00
    MM2S_DMASR = 0x04
    MM2S_SA = 0x18
    MM2S_SA_MSB = 0x1C
    MM2S_LENGTH = 0x28

    DMACR_RS = 1 << 0
    DMACR_RESET = 1 << 2

    DMASR_HALTED = 1 << 0
    DMASR_IDLE = 1 << 1
    DMASR_DMA_INT_ERR = 1 << 4
    DMASR_DMA_SLV_ERR = 1 << 5
    DMASR_DMA_DEC_ERR = 1 << 6
    DMASR_IOC_IRQ = 1 << 12
    DMASR_ERR_IRQ = 1 << 14

    ERROR_MASK = DMASR_DMA_INT_ERR | DMASR_DMA_SLV_ERR | DMASR_DMA_DEC_ERR | DMASR_ERR_IRQ
    CLEARABLE_MASK = DMASR_IOC_IRQ | DMASR_ERR_IRQ

    def __init__(self, base_addr: int, map_size: int = 0x1000) -> None:
        self.base_addr = base_addr
        self._region = _MMIORegion(base_addr, map_size)

    def open(self) -> None:
        self._region.open()

    def close(self) -> None:
        self._region.close()

    def __enter__(self) -> "AXIDMAMM2S":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def read_u32(self, offset: int) -> int:
        return self._region.read_u32(offset)

    def write_u32(self, offset: int, value: int) -> None:
        self._region.write_u32(offset, value)

    def status(self) -> dict[str, int | bool]:
        dmasr = self.read_u32(self.MM2S_DMASR)
        return {
            "dmasr": dmasr,
            "halted": bool(dmasr & self.DMASR_HALTED),
            "idle": bool(dmasr & self.DMASR_IDLE),
            "ioc_irq": bool(dmasr & self.DMASR_IOC_IRQ),
            "error": bool(dmasr & self.ERROR_MASK),
            "dma_int_err": bool(dmasr & self.DMASR_DMA_INT_ERR),
            "dma_slv_err": bool(dmasr & self.DMASR_DMA_SLV_ERR),
            "dma_dec_err": bool(dmasr & self.DMASR_DMA_DEC_ERR),
        }

    def clear_irq_status(self) -> None:
        self.write_u32(self.MM2S_DMASR, self.CLEARABLE_MASK)

    def soft_reset(self, timeout_s: float = 0.1) -> None:
        self.write_u32(self.MM2S_DMACR, self.DMACR_RESET)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            dmacr = self.read_u32(self.MM2S_DMACR)
            if (dmacr & self.DMACR_RESET) == 0:
                return
            time.sleep(0.001)
        raise AXIDMAError("AXI DMA reset timed out")

    def start_transfer(self, source_addr: int, length_bytes: int, timeout_s: float = 0.1) -> None:
        if length_bytes <= 0:
            raise ValueError("length_bytes must be > 0")
        if source_addr & 0x3:
            raise ValueError("source_addr must be 32-bit aligned")
        if length_bytes & 0x3:
            raise ValueError("length_bytes must be a multiple of 4 bytes")

        self.clear_irq_status()
        self.write_u32(self.MM2S_DMACR, self.DMACR_RS)

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            dmasr = self.read_u32(self.MM2S_DMASR)
            if (dmasr & self.DMASR_HALTED) == 0:
                break
            time.sleep(0.001)
        else:
            raise AXIDMAError("AXI DMA did not leave halted state")

        self.write_u32(self.MM2S_SA, source_addr & 0xFFFFFFFF)
        self.write_u32(self.MM2S_SA_MSB, (source_addr >> 32) & 0xFFFFFFFF)
        self.write_u32(self.MM2S_LENGTH, length_bytes)

    def wait_complete(self, timeout_s: float = 1.0) -> dict[str, int | bool]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snapshot = self.status()
            if snapshot["error"]:
                raise AXIDMAError(f"AXI DMA MM2S error, DMASR=0x{snapshot['dmasr']:08X}")
            if snapshot["idle"] and not snapshot["halted"]:
                return snapshot
            time.sleep(0.001)
        raise AXIDMAError("AXI DMA MM2S transfer timed out")
