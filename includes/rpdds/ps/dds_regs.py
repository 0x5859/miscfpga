"""
Red Pitaya DDS AXI DMA register definitions.

This file mirrors the RTL register map and the PS-side AXI DMA control plan.
Keep this file aligned with:
- rtl/rp_dds_defs.vh
- rtl/red_pitaya_dds_axi_dma.v
"""

from __future__ import annotations

DDS_BASE_ADDR = 0x40200000
DDS_MAP_SIZE = 0x50000

AXI_DMA_BASE_ADDR = 0x80400000
AXI_DMA_MAP_SIZE = 0x1000

DMA_BUFFER_BASE_ADDR = 0x1E000000
DMA_BUFFER_SIZE = 0x00400000

REG_ID = 0x00000
REG_VERSION = 0x00004
REG_CONTROL = 0x00008
REG_STATUS = 0x0000C
REG_SAMPLE_RATE = 0x00010
REG_LUT_LENGTH = 0x00014
REG_FEATURES = 0x00018
REG_DMA_CONTROL = 0x0001C
REG_DMA_TARGET = 0x00020
REG_DMA_EXPECTED_WORDS = 0x00024
REG_DMA_RECEIVED_WORDS = 0x00028
REG_DMA_ERROR_CODE = 0x0002C

REG_CHA_CTRL = 0x00100
REG_CHA_FTW_LO = 0x00104
REG_CHA_FTW_HI = 0x00108
REG_CHA_PHASE_LO = 0x0010C
REG_CHA_PHASE_HI = 0x00110
REG_CHA_AMP = 0x00114
REG_CHA_DC = 0x00118
REG_CHA_ARB_BANK = 0x0011C

REG_CHB_CTRL = 0x00200
REG_CHB_FTW_LO = 0x00204
REG_CHB_FTW_HI = 0x00208
REG_CHB_PHASE_LO = 0x0020C
REG_CHB_PHASE_HI = 0x00210
REG_CHB_AMP = 0x00214
REG_CHB_DC = 0x00218
REG_CHB_ARB_BANK = 0x0021C

LUTA_BANK0_BASE = 0x10000
LUTA_BANK1_BASE = 0x20000
LUTB_BANK0_BASE = 0x30000
LUTB_BANK1_BASE = 0x40000

PHASE_WIDTH = 48
LUT_LENGTH = 16384
DAC_SAMPLE_RATE_HZ = 125_000_000

DMA_WORD_BYTES = 4
DMA_TRANSFER_BYTES = LUT_LENGTH * DMA_WORD_BYTES
DMA_STAGING_SLOT_BYTES = 0x00010000
DMA_STAGING_OFFSET_A = 0x00000000
DMA_STAGING_OFFSET_B = 0x00010000

WAVE_SINE = 0
WAVE_SQUARE = 1
WAVE_TRIANGLE = 2
WAVE_SAW = 3
WAVE_ARB = 4

WAVE_NAME_TO_CODE = {
    "sine": WAVE_SINE,
    "square": WAVE_SQUARE,
    "triangle": WAVE_TRIANGLE,
    "saw": WAVE_SAW,
    "sawtooth": WAVE_SAW,
    "arb": WAVE_ARB,
    "arbitrary": WAVE_ARB,
}

WAVE_CODE_TO_NAME = {
    WAVE_SINE: "sine",
    WAVE_SQUARE: "square",
    WAVE_TRIANGLE: "triangle",
    WAVE_SAW: "saw",
    WAVE_ARB: "arb",
}

DMA_ERROR_CODE_TO_NAME = {
    0: "none",
    1: "expected_words_zero",
    2: "early_tlast",
    3: "missing_tlast",
    4: "overrun",
    5: "abort",
}

CHANNEL_TO_CTRL = {
    "a": {
        "ctrl": REG_CHA_CTRL,
        "ftw_lo": REG_CHA_FTW_LO,
        "ftw_hi": REG_CHA_FTW_HI,
        "phase_lo": REG_CHA_PHASE_LO,
        "phase_hi": REG_CHA_PHASE_HI,
        "amp": REG_CHA_AMP,
        "dc": REG_CHA_DC,
        "arb_bank": REG_CHA_ARB_BANK,
        "debug_bank_bases": [LUTA_BANK0_BASE, LUTA_BANK1_BASE],
        "dma_staging_offset": DMA_STAGING_OFFSET_A,
        "dma_target_channel_bit": 0,
    },
    "b": {
        "ctrl": REG_CHB_CTRL,
        "ftw_lo": REG_CHB_FTW_LO,
        "ftw_hi": REG_CHB_FTW_HI,
        "phase_lo": REG_CHB_PHASE_LO,
        "phase_hi": REG_CHB_PHASE_HI,
        "amp": REG_CHB_AMP,
        "dc": REG_CHB_DC,
        "arb_bank": REG_CHB_ARB_BANK,
        "debug_bank_bases": [LUTB_BANK0_BASE, LUTB_BANK1_BASE],
        "dma_staging_offset": DMA_STAGING_OFFSET_B,
        "dma_target_channel_bit": 1,
    },
}

STATUS_CFG_BUSY = 1 << 0
STATUS_CFG_DONE = 1 << 1
STATUS_DMA_ARMED = 1 << 2
STATUS_DMA_BUSY = 1 << 3
STATUS_DMA_DONE = 1 << 4
STATUS_DMA_ERROR = 1 << 5
STATUS_CHA_ACTIVE_BANK = 1 << 6
STATUS_CHB_ACTIVE_BANK = 1 << 7
STATUS_CHA_ENABLE = 1 << 8
STATUS_CHB_ENABLE = 1 << 9
