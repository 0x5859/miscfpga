"""
Command-line helper for the Red Pitaya DDS AXI DMA demo.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from dds_hw import ChannelConfig, DDSHardware
from expr_engine import sample_expression


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Red Pitaya DDS AXI DMA CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Read status and active channel configuration")

    dma_p = sub.add_parser("dma-status", help="Read AXI DMA / PL loader status")
    dma_p.add_argument("--json", action="store_true", help="Print JSON only")

    cfg = sub.add_parser("config", help="Write channel configuration and apply")
    cfg.add_argument("--channel", choices=["a", "b"], required=True)
    cfg.add_argument("--wave", choices=["sine", "square", "triangle", "saw", "arb"], required=True)
    cfg.add_argument("--freq", type=float, required=True)
    cfg.add_argument("--phase", type=float, default=0.0)
    cfg.add_argument("--amp", type=float, default=1.0)
    cfg.add_argument("--dc", type=float, default=0.0)
    cfg.add_argument("--enable", action="store_true")
    cfg.add_argument("--arb-bank", type=int, choices=[0, 1], default=0)
    cfg.add_argument("--clear-phase", action="store_true")

    expr = sub.add_parser("expr", help="Generate an expression LUT and load it via AXI DMA")
    expr.add_argument("--channel", choices=["a", "b"], required=True)
    expr.add_argument("--expression", required=True)
    expr.add_argument("--freq", type=float, required=True)
    expr.add_argument("--phase", type=float, default=0.0)
    expr.add_argument("--amp", type=float, default=1.0)
    expr.add_argument("--dc", type=float, default=0.0)
    expr.add_argument("--bank", type=int, choices=[0, 1], default=None)
    expr.add_argument("--no-clear-phase", action="store_true")

    return parser


def main() -> None:
    args = build_parser().parse_args()

    with DDSHardware() as hw:
        if args.cmd == "status":
            print(json.dumps(hw.status_payload(), indent=2))
            return

        if args.cmd == "dma-status":
            payload = hw.get_dma_loader_status()
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                for key, value in payload.items():
                    print(f"{key}: {value}")
            return

        if args.cmd == "config":
            cfg = ChannelConfig(
                enabled=bool(args.enable),
                wave_name=args.wave,
                freq_hz=args.freq,
                phase_deg=args.phase,
                amplitude=args.amp,
                dc_offset=args.dc,
                arb_bank=args.arb_bank,
            )
            hw.write_channel_config(args.channel, cfg)
            hw.apply(clear_phase=bool(args.clear_phase))
            print(json.dumps(hw.status_payload(), indent=2))
            return

        if args.cmd == "expr":
            target_bank = args.bank if args.bank is not None else hw.choose_inactive_bank(args.channel)
            samples = sample_expression(args.expression, 16384)
            hw.load_lut_via_dma(args.channel, samples, target_bank=target_bank)

            cfg = ChannelConfig(
                enabled=True,
                wave_name="arb",
                freq_hz=args.freq,
                phase_deg=args.phase,
                amplitude=args.amp,
                dc_offset=args.dc,
                arb_bank=target_bank,
            )
            hw.write_channel_config(args.channel, cfg)
            hw.apply(clear_phase=not args.no_clear_phase)

            payload = hw.status_payload(
                metadata={
                    "last_expression": {args.channel: args.expression},
                    "last_lut_source": {args.channel: {"kind": "expression", "points": len(samples), "target_bank": target_bank}},
                }
            )
            print(json.dumps(payload, indent=2))
            return

        raise RuntimeError(f"Unhandled command: {args.cmd}")


if __name__ == "__main__":
    main()
