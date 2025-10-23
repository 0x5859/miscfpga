"""
Low-level Red Pitaya DDS AXI DMA hardware access through /dev/mem.
"""

from __future__ import annotations

import mmap
import os
import struct
import time
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

from axi_dma_mm2s import AXIDMAError, AXIDMAMM2S
from dds_regs import (
    AXI_DMA_BASE_ADDR,
    AXI_DMA_MAP_SIZE,
    CHANNEL_TO_CTRL,
    DAC_SAMPLE_RATE_HZ,
    DDS_BASE_ADDR,
    DDS_MAP_SIZE,
    DMA_BUFFER_BASE_ADDR,
    DMA_BUFFER_SIZE,
    DMA_ERROR_CODE_TO_NAME,
    DMA_STAGING_SLOT_BYTES,
    PHASE_WIDTH,
    REG_CONTROL,
    REG_DMA_CONTROL,
    REG_DMA_ERROR_CODE,
    REG_DMA_EXPECTED_WORDS,
    REG_DMA_RECEIVED_WORDS,
    REG_DMA_TARGET,
    REG_FEATURES,
    REG_ID,
    REG_LUT_LENGTH,
    REG_SAMPLE_RATE,
    REG_STATUS,
    REG_VERSION,
    STATUS_CHA_ACTIVE_BANK,
    STATUS_CHB_ACTIVE_BANK,
    STATUS_CHB_ENABLE,
    STATUS_CHA_ENABLE,
    STATUS_CFG_BUSY,
    STATUS_CFG_DONE,
    STATUS_DMA_ARMED,
    STATUS_DMA_BUSY,
    STATUS_DMA_DONE,
    STATUS_DMA_ERROR,
    LUT_LENGTH,
    WAVE_ARB,
    WAVE_CODE_TO_NAME,
    WAVE_NAME_TO_CODE,
)

_PAGE_SIZE = mmap.PAGESIZE


class DDSHardwareError(RuntimeError):
    """Raised when the custom DDS hardware reports an invalid state."""


@dataclass
class ChannelConfig:
    enabled: bool
    wave_name: str
    freq_hz: float
    phase_deg: float
    amplitude: float
    dc_offset: float = 0.0
    arb_bank: int = 0


class _MMIORegion:
    def __init__(self, base_addr: int, map_size: int) -> None:
        self.base_addr = base_addr
        self.map_size = map_size
        self._fd: int | None = None
        self._mmap: mmap.mmap | None = None
        self._page_offset = 0

    def open(self) -> None:
        page_base = self.base_addr & ~(_PAGE_SIZE - 1)
        self._page_offset = self.base_addr - page_base
        map_span = ((self.map_size + self._page_offset + _PAGE_SIZE - 1) // _PAGE_SIZE) * _PAGE_SIZE

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

    def write_bytes(self, offset: int, payload: bytes) -> None:
        self.mm.seek(self._page_offset + offset)
        self.mm.write(payload)
        # msync() on /dev/mem MAP_SHARED returns EINVAL on some ARM
        # kernels (observed on Red Pitaya Linux 5.15 armv7l). The
        # mapping is uncached via /dev/mem anyway, so the flush is
        # unnecessary for coherency with the AXI DMA controller; we
        # still call it opportunistically on kernels that accept it.
        try:
            self.mm.flush()
        except OSError:
            pass

    def write_words(self, offset: int, words: list[int]) -> None:
        payload = struct.pack(f"<{len(words)}I", *[word & 0xFFFFFFFF for word in words])
        self.write_bytes(offset, payload)


def _q1_15(value: float) -> int:
    clipped = max(-1.0, min(0.999969482421875, value))
    return int(round(clipped * (1 << 15)))


def _q1_15_to_float(value: int) -> float:
    return float(value) / float(1 << 15)


def _dc_to_i14(value: float) -> int:
    clipped = max(-1.0, min(0.9998779296875, value))
    return int(round(clipped * 8191.0))


def _i14_to_float(value: int) -> float:
    return float(value) / 8191.0 if value >= 0 else float(value) / 8192.0


def _phase_word_from_deg(phase_deg: float) -> int:
    phase_norm = (phase_deg % 360.0) / 360.0
    return int(round(phase_norm * (1 << PHASE_WIDTH))) & ((1 << PHASE_WIDTH) - 1)


def _phase_word_to_deg(phase_word: int) -> float:
    return (float(phase_word & ((1 << PHASE_WIDTH) - 1)) * 360.0) / float(1 << PHASE_WIDTH)


def _ftw_from_hz(freq_hz: float) -> int:
    ftw = int(round(freq_hz * (1 << PHASE_WIDTH) / float(DAC_SAMPLE_RATE_HZ)))
    return ftw & ((1 << PHASE_WIDTH) - 1)


def _hz_from_ftw(ftw: int) -> float:
    return float(ftw) * float(DAC_SAMPLE_RATE_HZ) / float(1 << PHASE_WIDTH)


def _to_signed14_raw(sample: int) -> int:
    clipped = max(-8192, min(8191, int(sample)))
    return clipped & 0x3FFF


def _sign_extend(value: int, width: int) -> int:
    sign_bit = 1 << (width - 1)
    return (value & (sign_bit - 1)) - (value & sign_bit)


class DDSHardware:
    """
    Thin wrapper around the Red Pitaya DDS AXI DMA memory map.

    Notes
    -----
    - This class expects the custom DDS AXI DMA bitstream to already be loaded.
    - It uses /dev/mem, so it must run as root.
    """

    def __init__(
        self,
        dds_base_addr: int = DDS_BASE_ADDR,
        dds_map_size: int = DDS_MAP_SIZE,
        dma_base_addr: int = AXI_DMA_BASE_ADDR,
        dma_map_size: int = AXI_DMA_MAP_SIZE,
        dma_buffer_base: int = DMA_BUFFER_BASE_ADDR,
        dma_buffer_size: int = DMA_BUFFER_SIZE,
    ) -> None:
        self.base_addr = dds_base_addr
        self.map_size = dds_map_size
        self.buffer_base = dma_buffer_base
        self.buffer_size = dma_buffer_size
        self._core = _MMIORegion(dds_base_addr, dds_map_size)
        self._buffer = _MMIORegion(dma_buffer_base, dma_buffer_size)
        self._dma = AXIDMAMM2S(dma_base_addr, dma_map_size)

    def open(self) -> None:
        self._core.open()
        self._buffer.open()
        self._dma.open()

    def close(self) -> None:
        self._dma.close()
        self._buffer.close()
        self._core.close()

    def __enter__(self) -> "DDSHardware":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def read_u32(self, offset: int) -> int:
        return self._core.read_u32(offset)

    def write_u32(self, offset: int, value: int) -> None:
        self._core.write_u32(offset, value)

    def get_core_info(self) -> Mapping[str, int | bool]:
        status = self.read_u32(REG_STATUS)
        return {
            "id": self.read_u32(REG_ID),
            "version": self.read_u32(REG_VERSION),
            "status": status,
            "sample_rate_hz": self.read_u32(REG_SAMPLE_RATE),
            "lut_length": self.read_u32(REG_LUT_LENGTH),
            "features": self.read_u32(REG_FEATURES),
            "cfg_busy": bool(status & STATUS_CFG_BUSY),
            "cfg_done": bool(status & STATUS_CFG_DONE),
            "dma_armed": bool(status & STATUS_DMA_ARMED),
            "dma_busy": bool(status & STATUS_DMA_BUSY),
            "dma_done": bool(status & STATUS_DMA_DONE),
            "dma_error": bool(status & STATUS_DMA_ERROR),
            "active_bank_a": 1 if status & STATUS_CHA_ACTIVE_BANK else 0,
            "active_bank_b": 1 if status & STATUS_CHB_ACTIVE_BANK else 0,
            "channel_a_enabled": bool(status & STATUS_CHA_ENABLE),
            "channel_b_enabled": bool(status & STATUS_CHB_ENABLE),
        }

    def get_dma_loader_status(self) -> dict[str, int | bool | str]:
        status = self.read_u32(REG_STATUS)
        target = self.read_u32(REG_DMA_TARGET)
        error_code = self.read_u32(REG_DMA_ERROR_CODE)
        return {
            "armed": bool(status & STATUS_DMA_ARMED),
            "busy": bool(status & STATUS_DMA_BUSY),
            "done": bool(status & STATUS_DMA_DONE),
            "error": bool(status & STATUS_DMA_ERROR),
            "target_channel": "b" if (target & 0x1) else "a",
            "target_bank": 1 if (target & 0x2) else 0,
            "expected_words": self.read_u32(REG_DMA_EXPECTED_WORDS),
            "received_words": self.read_u32(REG_DMA_RECEIVED_WORDS),
            "error_code": error_code,
            "error_name": DMA_ERROR_CODE_TO_NAME.get(error_code, "unknown"),
            "mm2s": self._dma.status(),
        }

    def read_channel_config(self, channel: str) -> ChannelConfig:
        ch = CHANNEL_TO_CTRL[channel]
        ctrl = self.read_u32(ch["ctrl"])
        ftw = self.read_u32(ch["ftw_lo"]) | ((self.read_u32(ch["ftw_hi"]) & 0xFFFF) << 32)
        phase = self.read_u32(ch["phase_lo"]) | ((self.read_u32(ch["phase_hi"]) & 0xFFFF) << 32)
        amp_raw = _sign_extend(self.read_u32(ch["amp"]) & 0xFFFF, 16)
        dc_raw = _sign_extend(self.read_u32(ch["dc"]) & 0x3FFF, 14)
        arb_bank = self.read_u32(ch["arb_bank"]) & 0x1

        return ChannelConfig(
            enabled=bool(ctrl & 0x1),
            wave_name=WAVE_CODE_TO_NAME.get((ctrl >> 1) & 0x7, "unknown"),
            freq_hz=_hz_from_ftw(ftw),
            phase_deg=_phase_word_to_deg(phase),
            amplitude=_q1_15_to_float(amp_raw),
            dc_offset=_i14_to_float(dc_raw),
            arb_bank=arb_bank,
        )

    def write_channel_config(self, channel: str, cfg: ChannelConfig) -> None:
        if cfg.wave_name not in WAVE_NAME_TO_CODE:
            raise ValueError(f"Unsupported wave_name: {cfg.wave_name}")
        if not (0 <= int(cfg.arb_bank) <= 1):
            raise ValueError("arb_bank must be 0 or 1")

        ch = CHANNEL_TO_CTRL[channel]
        wave_code = WAVE_NAME_TO_CODE[cfg.wave_name]

        self.write_u32(ch["ctrl"], (wave_code << 1) | int(cfg.enabled))

        ftw = _ftw_from_hz(cfg.freq_hz)
        self.write_u32(ch["ftw_lo"], ftw & 0xFFFFFFFF)
        self.write_u32(ch["ftw_hi"], (ftw >> 32) & 0xFFFF)

        phase_word = _phase_word_from_deg(cfg.phase_deg)
        self.write_u32(ch["phase_lo"], phase_word & 0xFFFFFFFF)
        self.write_u32(ch["phase_hi"], (phase_word >> 32) & 0xFFFF)

        self.write_u32(ch["amp"], _q1_15(cfg.amplitude))
        self.write_u32(ch["dc"], _dc_to_i14(cfg.dc_offset))
        self.write_u32(ch["arb_bank"], int(cfg.arb_bank))

    def apply(self, clear_phase: bool = False) -> None:
        control = 0x1
        if clear_phase:
            control |= 0x2
        self.write_u32(REG_CONTROL, control)

    def clear_dma_loader_status(self) -> None:
        self.write_u32(REG_DMA_CONTROL, 0x2)

    def abort_dma_loader(self) -> None:
        self.write_u32(REG_DMA_CONTROL, 0x4)

    def arm_dma_loader(self, channel: str, bank: int, expected_words: int) -> None:
        target_channel_bit = CHANNEL_TO_CTRL[channel]["dma_target_channel_bit"]
        self.write_u32(REG_DMA_TARGET, (int(bank) << 1) | target_channel_bit)
        self.write_u32(REG_DMA_EXPECTED_WORDS, int(expected_words))
        self.write_u32(REG_DMA_CONTROL, 0x1)

    def choose_inactive_bank(self, channel: str) -> int:
        status = self.read_u32(REG_STATUS)
        if channel == "a":
            return 0 if (status & STATUS_CHA_ACTIVE_BANK) else 1
        if channel == "b":
            return 0 if (status & STATUS_CHB_ACTIVE_BANK) else 1
        raise ValueError(f"Unsupported channel: {channel}")

    def stage_samples(self, channel: str, samples: Iterable[int]) -> tuple[int, int]:
        words = [_to_signed14_raw(sample) for sample in samples]
        if len(words) != LUT_LENGTH:
            raise ValueError(f"Expected {LUT_LENGTH} samples, got {len(words)}")

        slot_offset = CHANNEL_TO_CTRL[channel]["dma_staging_offset"]
        if slot_offset + DMA_STAGING_SLOT_BYTES > self.buffer_size:
            raise DDSHardwareError("DMA staging offset exceeds reserved buffer size")

        self._buffer.write_words(slot_offset, words)
        return self.buffer_base + slot_offset, len(words) * 4

    def load_lut_via_dma(
        self,
        channel: str,
        samples: Iterable[int],
        target_bank: int | None = None,
        arm_timeout_s: float = 0.1,
        transfer_timeout_s: float = 1.0,
    ) -> dict[str, int | bool | str]:
        if target_bank is None:
            target_bank = self.choose_inactive_bank(channel)
        if target_bank not in (0, 1):
            raise ValueError("target_bank must be 0 or 1")

        source_addr, byte_count = self.stage_samples(channel, samples)

        self._dma.soft_reset()
        self.clear_dma_loader_status()
        self.arm_dma_loader(channel, target_bank, byte_count // 4)

        deadline = time.monotonic() + arm_timeout_s
        while time.monotonic() < deadline:
            loader = self.get_dma_loader_status()
            if loader["armed"]:
                break
            time.sleep(0.001)
        else:
            raise DDSHardwareError("PL DMA loader did not arm")

        self._dma.start_transfer(source_addr, byte_count)
        self._dma.wait_complete(timeout_s=transfer_timeout_s)

        deadline = time.monotonic() + transfer_timeout_s
        while time.monotonic() < deadline:
            loader = self.get_dma_loader_status()
            if loader["error"]:
                raise DDSHardwareError(
                    f"PL DMA loader error: {loader['error_name']} ({loader['error_code']})"
                )
            if loader["done"]:
                if loader["received_words"] != LUT_LENGTH:
                    raise DDSHardwareError(
                        f"PL loader received {loader['received_words']} words, expected {LUT_LENGTH}"
                    )
                return loader
            time.sleep(0.001)

        raise DDSHardwareError("PL DMA loader did not finish")

    def status_payload(self, metadata: dict[str, object] | None = None) -> dict[str, object]:
        return {
            "hardware": self.get_core_info(),
            "channels": {
                "a": asdict(self.read_channel_config("a")),
                "b": asdict(self.read_channel_config("b")),
            },
            "dma_loader": self.get_dma_loader_status(),
            "metadata": metadata or {},
        }
