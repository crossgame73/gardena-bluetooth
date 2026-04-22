# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python library for controlling Gardena Bluetooth-enabled watering devices. Primary consumer is Home Assistant; also usable standalone via CLI and TUI. Devices only allow a single paired connection — factory reset is required to pair with a new controller.

## Commands

```bash
# Install dependencies (uses uv)
uv sync

# Run tests
uv run pytest -sxv

# Run a single test
uv run pytest tests/test_parse.py::test_int_keys -sxv

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Run CLI
uv run python -m gardena_bluetooth scan
uv run python -m gardena_bluetooth connect <ADDRESS>

# Run TUI
uv run python -m gardena_bluetooth tui
```

## Architecture

**BLE Characteristic Type System** (`parse.py`): Generic `Characteristic[T]` base with typed subclasses (`CharacteristicBool`, `CharacteristicLong`, `CharacteristicTime`, etc.) that handle encode/decode between Python types and BLE byte payloads. Each characteristic instance holds a UUID and optional variant for devices that share UUIDs across different services.

**Service Registry** (`parse.py` + `const.py`): `Service` subclasses auto-register via `__init_subclass__`. Each service declares a UUID, supported `ProductType` set, and characteristic attributes. `Service.find_service(uuid, product_type)` resolves the correct service variant at runtime. `Schedule` uses ABC + `__init_subclass__` with an `instance` parameter to dynamically generate 5 schedule services with computed UUIDs.

**Product Type Detection** (`parse.py`): `ManufacturerData` decodes Gardena's proprietary TLV advertisement format (company ID `0x0426`). Segmented advertisements are accumulated across multiple BLE packets. `ProductType.from_manufacturer_data()` maps group/model/variant to product types (WATER_COMPUTER, PUMP, AQUA_CONTOURS, etc.).

**Client** (`client.py`): `Client` wraps `CachedConnection` (recursive connection with delayed disconnect) and filters services/characteristics by `ProductType`. Accepts either `BLEDevice` or `CachedConnection`.

**CLI** (`__main__.py`): `asyncclick` commands. Uses `connect_device()` context manager that resolves address from config, looks up cached product type, finds BLE device, and yields a `Client`.

**TUI** (`tui.py`): Textual app with device selection screen and dashboard. Edit modals for schedules, sensor, and config. BLE operations run via `@work` decorators.

**Device Config** (`config.py`): Registered devices stored at `~/.gardena-bluetooth/devices.json` with cached product type to avoid 15-second manufacturer data scan on each command.

## Key Patterns

- Valve control differs by device: newer devices use `Valve1.start_watering`/`stop_watering` (CharacteristicIntKeys); legacy devices use `Valve.remaining_open_time` (write seconds to open, 0 to close). `Valve.state` is read-only on legacy devices.
- All read commands use `client.read_char(Characteristic, default=None)` to gracefully skip unsupported characteristics.
- Async tests use `asyncio_mode = "auto"` — no explicit `@pytest.mark.asyncio` needed.
