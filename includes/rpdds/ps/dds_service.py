"""
Minimal HTTP service for the Red Pitaya DDS AXI DMA demo application.

The service uses only the Python standard library so it can run on a fresh
Red Pitaya OS image without extra package installation.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple

from axi_dma_mm2s import AXIDMAError
from dds_hw import ChannelConfig, DDSHardware, DDSHardwareError
from dds_regs import LUT_LENGTH
from expr_engine import SafeExpressionError, sample_expression


class DDSState:
    def __init__(self) -> None:
        self.last_expression: Dict[str, str] = {}
        self.last_lut_source: Dict[str, Dict[str, Any]] = {}

    def metadata_payload(self) -> Dict[str, Any]:
        return {
            "last_expression": self.last_expression,
            "last_lut_source": self.last_lut_source,
        }


STATE = DDSState()


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8"))


def _parse_channel_path(path: str) -> Tuple[str, str] | None:
    tokens = [token for token in path.strip("/").split("/") if token]
    if len(tokens) >= 4 and tokens[0] == "api" and tokens[1] == "channel":
        return tokens[2], tokens[3]
    return None


class DDSRequestHandler(BaseHTTPRequestHandler):
    server_version = "DDSDMASVC/2.0"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep stdout concise on the embedded target.
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/status":
            with DDSHardware() as hw:
                _json_response(self, HTTPStatus.OK, hw.status_payload(STATE.metadata_payload()))
            return

        _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/apply":
            payload = _read_json(self)
            with DDSHardware() as hw:
                hw.apply(clear_phase=bool(payload.get("clear_phase", False)))
                _json_response(self, HTTPStatus.OK, hw.status_payload(STATE.metadata_payload()))
            return

        parsed = _parse_channel_path(self.path)
        if parsed is None:
            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
            return

        channel, action = parsed
        if channel not in {"a", "b"}:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid channel"})
            return

        payload = _read_json(self)

        try:
            if action == "config":
                self._handle_config(channel, payload)
                return

            if action == "expression":
                self._handle_expression(channel, payload)
                return

            if action == "lut":
                self._handle_raw_lut(channel, payload)
                return

            _json_response(self, HTTPStatus.NOT_FOUND, {"error": "Unknown action"})
        except (ValueError, SafeExpressionError, DDSHardwareError, AXIDMAError) as exc:
            _json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _handle_config(self, channel: str, payload: Dict[str, Any]) -> None:
        with DDSHardware() as hw:
            current = hw.read_channel_config(channel)
            cfg = ChannelConfig(
                enabled=bool(payload.get("enabled", current.enabled)),
                wave_name=str(payload.get("wave_name", current.wave_name)),
                freq_hz=float(payload.get("freq_hz", current.freq_hz)),
                phase_deg=float(payload.get("phase_deg", current.phase_deg)),
                amplitude=float(payload.get("amplitude", current.amplitude)),
                dc_offset=float(payload.get("dc_offset", current.dc_offset)),
                arb_bank=int(payload.get("arb_bank", current.arb_bank)),
            )
            hw.write_channel_config(channel, cfg)
            hw.apply(clear_phase=bool(payload.get("clear_phase", False)))
            _json_response(self, HTTPStatus.OK, hw.status_payload(STATE.metadata_payload()))

    def _handle_expression(self, channel: str, payload: Dict[str, Any]) -> None:
        expression = str(payload["expression"])
        with DDSHardware() as hw:
            current = hw.read_channel_config(channel)
            target_bank = int(payload.get("target_bank", hw.choose_inactive_bank(channel)))
            samples = sample_expression(expression, LUT_LENGTH)

            cfg = ChannelConfig(
                enabled=bool(payload.get("enabled", True)),
                wave_name="arb",
                freq_hz=float(payload.get("freq_hz", current.freq_hz)),
                phase_deg=float(payload.get("phase_deg", current.phase_deg)),
                amplitude=float(payload.get("amplitude", current.amplitude)),
                dc_offset=float(payload.get("dc_offset", current.dc_offset)),
                arb_bank=target_bank,
            )

            hw.load_lut_via_dma(channel, samples, target_bank=target_bank)
            hw.write_channel_config(channel, cfg)
            hw.apply(clear_phase=bool(payload.get("clear_phase", True)))

            STATE.last_expression[channel] = expression
            STATE.last_lut_source[channel] = {
                "kind": "expression",
                "points": len(samples),
                "target_bank": target_bank,
            }

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    **hw.status_payload(STATE.metadata_payload()),
                    "expression": expression,
                    "points": len(samples),
                    "target_bank": target_bank,
                },
            )

    def _handle_raw_lut(self, channel: str, payload: Dict[str, Any]) -> None:
        raw_samples = payload.get("samples")
        if not isinstance(raw_samples, list):
            raise ValueError("samples must be a JSON list of signed integers")
        samples = [int(item) for item in raw_samples]

        with DDSHardware() as hw:
            current = hw.read_channel_config(channel)
            target_bank = int(payload.get("target_bank", hw.choose_inactive_bank(channel)))

            cfg = ChannelConfig(
                enabled=bool(payload.get("enabled", current.enabled)),
                wave_name=str(payload.get("wave_name", "arb")),
                freq_hz=float(payload.get("freq_hz", current.freq_hz)),
                phase_deg=float(payload.get("phase_deg", current.phase_deg)),
                amplitude=float(payload.get("amplitude", current.amplitude)),
                dc_offset=float(payload.get("dc_offset", current.dc_offset)),
                arb_bank=target_bank,
            )

            hw.load_lut_via_dma(channel, samples, target_bank=target_bank)
            hw.write_channel_config(channel, cfg)
            hw.apply(clear_phase=bool(payload.get("clear_phase", True)))

            STATE.last_lut_source[channel] = {
                "kind": "raw_lut",
                "points": len(samples),
                "target_bank": target_bank,
            }

            _json_response(
                self,
                HTTPStatus.OK,
                {
                    **hw.status_payload(STATE.metadata_payload()),
                    "points": len(samples),
                    "target_bank": target_bank,
                },
            )


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 18888), DDSRequestHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
