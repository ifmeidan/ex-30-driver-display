"""TCP receiver for the AAOS → Pi bridge.

Implements the Pi side of ex30-companion-aaos/pi_bridge/protocol.md:
- TCP server on 0.0.0.0:7878, one connection at a time (drops the previous
  on a new connect).
- Reads line-delimited JSON, max 4 KB per line.
- For `state` frames, dispatches whitelisted wire fields into
  SharedVehicleData via update(**fields).
- Logs every accepted message to logs/aaos_bridge_YYYYMMDD.jsonl so the
  raw stream can be replayed.
- Marks the AAOS source stale (`aaos_stale=True`) after 15s without any
  message, matching the protocol's heartbeat contract.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime
from typing import Optional

from shared.vehicle_data import SharedVehicleData
from aaos_bridge.clock_sync import ClockSyncer

logger = logging.getLogger("aaos_bridge")

# Whitelist of wire field names the protocol allows in `state.fields`. Anything
# outside this set is dropped with a warning — the Pi must not let a future
# schema bump silently set arbitrary attributes on VehicleData via setattr.
WIRE_FIELDS = frozenset({
    "speed_aaos",
    "speed_display_aaos",
    "soc_aaos",
    "battery_power_mw",
    "range_m_aaos",
    "gear",
    "gear_selected",
    "parking_brake",
    "ignition_state",
    "charge_port_connected",
    "ambient_temp_aaos",
    "night_mode",
    "pack_capacity_wh", 
})

DEFAULT_PORT = 7878
MAX_LINE_BYTES = 4096
STALE_AFTER_S = 15.0
ACCEPT_TIMEOUT_S = 1.0

# When AAOS provides a value, the receiver also writes the canonical
# OBD-shared field so existing UI bindings pick it up without per-widget
# branching. Per-source disambiguation (the `_aaos`-suffixed copy) stays
# available for divergence diagnostics. The poller-side suppression in
# `obd2.poller._push_to_shared` keeps OBD2 from clobbering these while
# AAOS is fresh. When AAOS goes stale, the suppression lifts — but only
# fields with an *active* OBD lane actually get fresh writes: today that
# is power_kw/throttle/regen/energy (from HV V×I, MEDIUM lane). speed,
# soc and ambient_temp have no active OBD lane (their PIDs are parked at
# PollGroup.NONE), so they hold their last value; the UI blanks the speed
# numeral on staleness instead.
AAOS_COVERED_OBD_FIELDS = frozenset({
    "speed", "soc", "ambient_temp", "gear",
    "power_kw", "throttle_pct", "regen_pct", "consumed_kwh", "regen_kwh",
})


class AaosBridgeReceiver:
    """Threaded TCP receiver. Call start()/stop() from the main thread."""

    def __init__(
        self,
        shared: SharedVehicleData,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        log_dir: str = "logs",
        stale_after_s: float = STALE_AFTER_S,
        pack_capacity_wh: float = 64000.0,
        clock_syncer: Optional[ClockSyncer] = None,
    ) -> None:
        self._shared = shared
        self._host = host
        self._port = port
        self._log_dir = log_dir
        self._stale_after_s = stale_after_s
        self._pack_capacity_wh = pack_capacity_wh
        self._clock_syncer = clock_syncer if clock_syncer is not None else ClockSyncer()
        # Receiver-local energy integration state for battery_power_mw.
        # Resets when the receiver instance is constructed; not persisted
        # across reboots — same scope as the OBD2 poller's counterpart.
        self._consumed_kwh: float = 0.0
        self._regen_kwh: float = 0.0
        self._last_power_ts: Optional[float] = None
        self._stop = threading.Event()
        self._serve_thread: Optional[threading.Thread] = None
        self._stale_thread: Optional[threading.Thread] = None
        self._conn_lock = threading.Lock()
        self._active: Optional[socket.socket] = None
        os.makedirs(self._log_dir, exist_ok=True)

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._serve_thread is not None:
            return
        self._serve_thread = threading.Thread(
            target=self._serve, name="aaos-bridge", daemon=True
        )
        self._stale_thread = threading.Thread(
            target=self._stale_loop, name="aaos-bridge-stale", daemon=True
        )
        self._serve_thread.start()
        self._stale_thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._conn_lock:
            conn = self._active
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # --- accept loop -------------------------------------------------------

    def _serve(self) -> None:
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self._host, self._port))
            srv.listen(2)
            srv.settimeout(ACCEPT_TIMEOUT_S)
        except OSError:
            logger.exception("aaos_bridge: failed to bind %s:%d", self._host, self._port)
            return
        logger.info("listening on %s:%d", self._host, self._port)
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    continue
                except OSError:
                    logger.exception("accept failed")
                    continue
                threading.Thread(
                    target=self._handle, args=(conn, addr), daemon=True
                ).start()
        finally:
            try:
                srv.close()
            except OSError:
                pass

    # --- per-connection handler -------------------------------------------

    def _handle(self, conn: socket.socket, addr) -> None:
        with self._conn_lock:
            prev = self._active
            self._active = conn
        if prev is not None:
            logger.info("dropping previous connection in favor of %s", addr)
            try:
                prev.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        logger.info("connected: %s", addr)
        try:
            stream = conn.makefile("rb")
            while not self._stop.is_set():
                raw = stream.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_LINE_BYTES:
                    logger.warning("oversized line (%d bytes), dropping", len(raw))
                    # Drain to next newline so we don't desync on the next call.
                    while raw and not raw.endswith(b"\n"):
                        raw = stream.readline(MAX_LINE_BYTES + 1)
                    continue
                self._process_line(raw.rstrip(b"\r\n"))
        except OSError as exc:
            logger.warning("connection error from %s: %s", addr, exc)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            with self._conn_lock:
                if self._active is conn:
                    self._active = None
            logger.info("disconnected: %s", addr)

    # --- frame handling ---------------------------------------------------

    def _process_line(self, raw: bytes) -> None:
        if not raw:
            return
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("bad JSON line: %s", exc)
            return
        if not isinstance(msg, dict):
            logger.warning("non-object frame, dropping")
            return
        kind = msg.get("type")
        if kind not in ("hello", "state", "heartbeat", "goodbye"):
            logger.warning("unknown frame type: %r", kind)
            return
        # Sync the wall-clock from `ts` BEFORE _mark_fresh() so the freshness
        # timestamp is recorded against the (possibly just-corrected) clock.
        # Otherwise a 9h jump forward would make the very next stale-loop tick
        # mark the source stale immediately.
        self._clock_syncer.consider(msg.get("ts"))
        self._log_jsonl(msg)
        self._mark_fresh()

        if kind == "state":
            self._apply_state(msg)
        elif kind == "hello":
            subs = msg.get("fields_subscribed")
            logger.info(
                "hello v=%s schema=%s fields=%d",
                msg.get("app_version"),
                msg.get("schema_version"),
                len(subs) if isinstance(subs, list) else -1,
            )
        elif kind == "goodbye":
            logger.info("goodbye reason=%s", msg.get("reason"))
        # heartbeat: nothing to do beyond freshness, already handled.

    def _apply_state(self, msg: dict) -> None:
        fields = msg.get("fields")
        if not isinstance(fields, dict):
            logger.warning("state without fields dict, dropping")
            return
        clean = {k: v for k, v in fields.items() if k in WIRE_FIELDS}
        unknown = set(fields) - WIRE_FIELDS
        if unknown:
            logger.warning("dropping unknown wire fields: %s", sorted(unknown))
        if clean:
            self._shared.update(**clean)
            self._shared.update(**self._canonical_aliases(clean))

    def _canonical_aliases(self, wire: dict) -> dict:
        """Map wire fields onto the OBD-shared canonical fields with unit
        conversion, so the UI sees a single source of truth when AAOS is
        fresh. Anything not covered here stays in the `_aaos` namespace only."""
        out: dict = {}

        # If this frame carries the static pack capacity, adopt it before
        # computing soc% so the conversion uses the real car's value rather
        # than the Settings default. INFO_EV_BATTERY_CAPACITY is STATIC, so
        # it normally arrives once per connect in the seed snapshot.
        if "pack_capacity_wh" in wire:
            try:
                cap = float(wire["pack_capacity_wh"])
                if cap > 0:
                    self._pack_capacity_wh = cap
            except (TypeError, ValueError):
                pass

        # Speed: the displayed value comes ONLY from the dash-calibrated
        # `speed_display_aaos`, so the screen always matches the car's own
        # speedometer. A frame that omits it simply leaves the last shown
        # value in place until the next calibrated frame arrives.
        #
        # DO NOT add a fallback to raw `speed_aaos` here. Raw arrives at a
        # higher cadence than the calibrated copy, so mixing the two makes
        # the digits bounce between sources on every raw-only tick. This was
        # deliberately removed; the raw value stays in the `_aaos` namespace
        # for divergence diagnostics only and must never drive the display.
        display_ms = wire.get("speed_display_aaos")
        if display_ms is not None:
            try:
                out["speed"] = int(round(float(display_ms) * 3.6))
            except (TypeError, ValueError):
                pass

        if "soc_aaos" in wire and self._pack_capacity_wh > 0:
            try:
                pct = float(wire["soc_aaos"]) / self._pack_capacity_wh * 100.0
                out["soc"] = max(0.0, min(100.0, pct))
            except (TypeError, ValueError):
                pass
        if "ambient_temp_aaos" in wire:
            try:
                out["ambient_temp"] = float(wire["ambient_temp_aaos"])
            except (TypeError, ValueError):
                pass
        if "charge_port_connected" in wire:
            # Plug presence is the most reliable trigger for the charging UI.
            # Regen-while-driving (which shows as positive battery_power_mw)
            # does not flip the screen because the plug isn't connected.
            out["is_charging"] = bool(wire["charge_port_connected"])

        # battery_power_mw is signed per VHAL spec after AAOS-side flip:
        # positive = charging / regen, negative = discharging. Translate to
        # the Pi's existing convention (`power_kw` positive = draw) by
        # negating, then derive the throttle/regen gauge percentages and
        # integrate consumed / regen energy over the dt between frames.
        if "battery_power_mw" in wire:
            try:
                mw = float(wire["battery_power_mw"])
            except (TypeError, ValueError):
                mw = None
            if mw is not None:
                power_kw = -mw / 1_000_000.0  # +draw / -regen, matches OBD2
                out["power_kw"] = power_kw
                out["charge_power_kw"] = mw / 1_000_000.0  # kW, +charging on plug
                bar_max = 100.0  # mirrors obd2.poller.POWER_BAR_MAX_KW
                if power_kw > 0:
                    out["throttle_pct"] = min(power_kw / bar_max, 1.0)
                    out["regen_pct"] = 0.0
                else:
                    out["throttle_pct"] = 0.0
                    out["regen_pct"] = min(-power_kw / bar_max, 1.0)
                now = time.monotonic()
                if self._last_power_ts is not None:
                    dt_h = (now - self._last_power_ts) / 3600.0
                    if power_kw > 0:
                        self._consumed_kwh += power_kw * dt_h
                    else:
                        self._regen_kwh += -power_kw * dt_h
                self._last_power_ts = now
                out["consumed_kwh"] = self._consumed_kwh
                out["regen_kwh"] = self._regen_kwh
        # `gear` already shares its name on the wire; the bare update(**clean)
        # call above writes it directly. Nothing to do here.
        return out

    def _mark_fresh(self) -> None:
        self._shared.update(aaos_stale=False, aaos_last_msg_ts=time.time())

    # --- jsonl logger -----------------------------------------------------

    def _log_jsonl(self, msg: dict) -> None:
        path = os.path.join(
            self._log_dir, f"aaos_bridge_{datetime.now().strftime('%Y%m%d')}.jsonl"
        )
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, separators=(",", ":")) + "\n")
        except OSError as exc:
            logger.warning("log write failed (%s): %s", path, exc)

    # --- staleness watchdog -----------------------------------------------

    def _stale_loop(self) -> None:
        while not self._stop.wait(1.0):
            last = self._shared.get("aaos_last_msg_ts")
            if not last:
                continue
            if time.time() - last > self._stale_after_s:
                if not self._shared.get("aaos_stale"):
                    self._shared.update(aaos_stale=True)
                    logger.info("AAOS source marked stale (>%.0fs idle)", self._stale_after_s)


# --- standalone entry point (debug / dev) --------------------------------


def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="AAOS bridge receiver (standalone)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--log-dir", default="logs")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    shared = SharedVehicleData()
    rec = AaosBridgeReceiver(shared, host=args.host, port=args.port, log_dir=args.log_dir)
    rec.start()
    try:
        while True:
            time.sleep(5)
            snap = shared.snapshot()
            logger.info(
                "snapshot stale=%s gear=%r speed_aaos=%.2f soc_aaos=%.0fWh",
                snap.aaos_stale, snap.gear, snap.speed_aaos, snap.soc_aaos,
            )
    except KeyboardInterrupt:
        logger.info("shutting down")
        rec.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
