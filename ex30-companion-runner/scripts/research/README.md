# Research scripts

These are the one-off tools the OBD2 reverse-engineering was done with. They are
**not** used at runtime — they exist so you can see (and reproduce) how the
confirmed PIDs in [`docs/pid_map.md`](../../docs/pid_map.md) were discovered and
validated against the live car.

| Script | Purpose |
| --- | --- |
| `abs_module_scan.py` | Sweep the ABS/ESC module for readable DIDs |
| `becm_did_sweep.py` | Sweep the battery ECU (BECM) DID range for responses |
| `becm_temp_scan.py` | Narrow scan for battery-temperature candidate DIDs |
| `brake_temp_watch.py` | Live monitor for brake-temperature candidates during drives |
| `candidate_calibration.py` | Log candidate DIDs alongside reference values to fit scale/offset |
| `ecu_f_brake_scan.py` | Scan the 0x0F ECU range for brake-related data |
| `temp_watch.py` | General live temperature-candidate watcher |
| `trip_walkthrough.py` | Offline trip-computer simulation (no car needed) |

All of the live scripts expect the Pi's Bluetooth RFCOMM link to the OBD2
adapter to be up (see `scripts/setup_lite.sh`). Run them parked, never while
driving. They only ever *read* (UDS `ReadDataByIdentifier` / OBD mode 01) —
nothing is written to the car.
