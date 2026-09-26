# Telemetry samples — synthetic, not measured

Both files here are **synthetic**. No battery, gateway, utility or vendor export was
read to make them: they are this repository's simulated fleet written out in the wire
format a real export would use, by `python scripts/make_telemetry_sample.py`
(deterministic — re-running it reproduces these bytes).

| file | what it is |
| --- | --- |
| `synthetic_sample.jsonl` | 48 valid rows, one per simulated battery, two reporting offline |
| `synthetic_bad_rows.jsonl` | one row per rejection reason the importer knows |

## Format

UTF-8 JSON lines: one JSON object per line, one row per device per report. Unknown keys
are ignored, blank lines and `#` comment lines are skipped.

| field | type | meaning |
| --- | --- | --- |
| `device_id` | string | the battery, e.g. `BAT-042` |
| `ts` | string | ISO-8601 **with an offset**, e.g. `2023-09-06T17:00:00Z` |
| `soc_kwh` | number | energy in the pack now, 0 to nameplate |
| `power_kw` | number | measured output, positive discharging, negative charging |
| `status` | string | `online`, `degraded`, `offline` or `unavailable` |
| `firmware` | string | build on the device, e.g. `2.4.1` |
| `gateway` | string | gateway the device reports through, e.g. `GW-07` |

```json
{"device_id": "BAT-001", "ts": "2023-09-06T16:59:43Z", "soc_kwh": 22.12, "power_kw": 0.08, "status": "online", "firmware": "2.4.1", "gateway": "GW-01"}
```

## Rules a row must pass

1. valid JSON object with all seven fields, `device_id` non-empty;
2. `ts` parses as ISO-8601 and carries a UTC offset;
3. `soc_kwh` and `power_kw` are finite numbers, `soc_kwh` is not negative;
4. `status` is one of the four above;
5. `firmware` and `gateway` are non-empty strings;
6. not stale: no more than 900 s behind the newest row in the same file (one ERCOT
   settlement interval; `--max-age-s` changes it);
7. the device exists in this fleet, `soc_kwh` is within 2% of its nameplate and
   `|power_kw|` within 10% of its inverter rating.

Where a device reports twice, the newest row wins and the older one is counted as
superseded rather than rejected. Everything else is rejected with a reason, and the
reasons are printed by the CLI and shown in the app.

```bash
python -m gridsignal.telemetry data/telemetry/synthetic_sample.jsonl
python -m gridsignal.telemetry data/telemetry/synthetic_bad_rows.jsonl
```
