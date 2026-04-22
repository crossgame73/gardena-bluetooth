import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

import asyncclick as click
from bleak import (
    AdvertisementData,
    BleakClient,
    BleakError,
    BleakScanner,
    BLEDevice,
)
from bleak.uuids import uuidstr_to_str

from .client import Client
from .config import get_default_address, load_config, register_device, save_config
from .const import (
    PRODUCT_NAMES,
    Battery,
    DeviceConfiguration,
    DeviceInformation,
    FlowStatistics,
    FotaService,
    ScanService,
    Schedule_1,
    Schedule_2,
    Schedule_3,
    Schedule_4,
    Schedule_5,
    Sensor,
    Valve,
    Valve1,
    WateringHistory,
)
from .parse import Characteristic, ManufacturerData, ProductType, Service
from .scan import async_get_manufacturer_data

SCHEDULES = [Schedule_1, Schedule_2, Schedule_3, Schedule_4, Schedule_5]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def address_option(f):
    return click.option("--address", "-a", default=None, help="Device BLE address")(f)


@asynccontextmanager
async def connect_device(address: str | None):
    config = load_config()
    if address is None:
        address = get_default_address(config)
        if address is None:
            raise click.ClickException(
                "No device address provided and no default device registered. "
                "Use 'register' first or pass --address."
            )

    device_info = config.get("devices", {}).get(address)
    if device_info and "product_type" in device_info:
        product_type = ProductType[device_info["product_type"]]
    else:
        click.echo("Scanning for device info...")
        mfg = await async_get_manufacturer_data({address})
        product_type = mfg[address].product_type

    click.echo(f"Connecting to {address}...")
    ble_device = await BleakScanner.find_device_by_address(address, timeout=10)
    if ble_device is None:
        raise click.ClickException(f"Device {address} not found nearby")

    client = Client(ble_device, product_type)
    try:
        yield client
    finally:
        await client.disconnect()


@click.group()
async def main():
    pass


# --- Existing commands ---


@main.command()
async def scan():
    click.echo("Scanning for devices")

    devices = set()

    def detected(device: BLEDevice, advertisement: AdvertisementData):
        if device not in devices:
            if ScanService not in advertisement.service_uuids:
                return
            devices.add(device)

        click.echo(f"Device: {device}")
        for service in advertisement.service_uuids:
            click.echo(f" - Service: {service} {uuidstr_to_str(service)}")
        click.echo(f" - Data: {advertisement.service_data}")
        click.echo(f" - Manu: {advertisement.manufacturer_data}")

        if data := advertisement.manufacturer_data.get(ManufacturerData.company):
            decoded = ManufacturerData.decode(data)
            click.echo(f" -     : {decoded}")

        click.echo(f" - RSSI: {advertisement.rssi}")
        click.echo()

    async with BleakScanner(detected, service_uuids=[ScanService, FotaService]):
        while True:
            await asyncio.sleep(1)


@main.command()
@click.argument("address")
async def connect(address: str):
    click.echo(f"Connecting to: {address}")

    product_types = await async_get_manufacturer_data({address})
    manufacturer_data = product_types[address]
    product_type = manufacturer_data.product_type

    click.echo(f"Advertised data: {manufacturer_data}")
    click.echo(f"Product type: {product_type}")

    async with BleakClient(address, timeout=20) as client:
        for service in client.services:
            service_parser = Service.find_service(service.uuid, product_type)

            click.echo(
                f"Service: {service.uuid}: {service_parser.__name__ if service_parser else service.description}"
            )

            for char in service.characteristics:
                char_parser = None
                if service_parser:
                    char_parser = service_parser.characteristics.get(char.uuid)

                click.echo(
                    f" -  {char.uuid}: {char_parser.name if char_parser else char.description}"
                )
                click.echo(f"    Prop: {char.properties}")

                data = None
                if "read" in char.properties:
                    try:
                        data = await client.read_gatt_char(char.uuid)
                    except BleakError as exc:
                        click.echo(f"    Failed: {repr(exc)}")

                if data is not None:
                    click.echo(f"    Raw: {data}")
                    if char_parser:
                        click.echo(f"    Data: {char_parser.decode(data)!r}")


@main.command()
async def chars():
    for char in Characteristic.registry.values():
        click.echo(char.name)


# --- New commands ---


@main.command()
@click.argument("address")
@click.option("--name", "-n", default=None, help="Friendly name for the device")
async def register(address: str, name: str | None):
    """Register a Gardena BLE device for quick access."""
    click.echo(f"Scanning for device {address}...")
    try:
        mfg = await async_get_manufacturer_data({address})
    except Exception as exc:
        raise click.ClickException(f"Failed to find device: {exc}") from exc

    manufacturer_data = mfg[address]
    product_type = manufacturer_data.product_type
    product_name = PRODUCT_NAMES.get(product_type, "Unknown")

    if name is None:
        name = product_name

    register_device(address, product_type, name)

    click.echo(f"Registered: {name}")
    click.echo(f"  Address:      {address}")
    click.echo(f"  Product type: {product_name} ({product_type.name})")
    click.echo(f"  Set as default device")


@main.group(invoke_without_command=True)
@click.pass_context
async def devices(ctx):
    """List registered devices."""
    if ctx.invoked_subcommand is not None:
        return

    config = load_config()
    device_list = config.get("devices", {})
    default = config.get("default")

    if not device_list:
        click.echo("No devices registered. Use 'register' to add one.")
        return

    for addr, info in device_list.items():
        marker = " (default)" if addr == default else ""
        product = info.get("product_type", "UNKNOWN")
        name = info.get("name", addr)
        click.echo(f"  {name}{marker}")
        click.echo(f"    Address: {addr}")
        click.echo(f"    Type:    {product}")


@devices.command("default")
@click.argument("name_or_address")
async def devices_default(name_or_address: str):
    """Set the default device by name or address."""
    config = load_config()
    device_list = config.get("devices", {})

    # Try exact address match first
    if name_or_address in device_list:
        config["default"] = name_or_address
        save_config(config)
        name = device_list[name_or_address].get("name", name_or_address)
        click.echo(f"Default set to: {name} ({name_or_address})")
        return

    # Try name match
    for addr, info in device_list.items():
        if info.get("name", "").lower() == name_or_address.lower():
            config["default"] = addr
            save_config(config)
            click.echo(f"Default set to: {info['name']} ({addr})")
            return

    raise click.ClickException(
        f"No device found matching '{name_or_address}'. Use 'devices' to see registered devices."
    )


@main.command()
@address_option
async def status(address: str | None):
    """Show device status: battery, valve state, firmware info."""
    async with connect_device(address) as client:
        # Device information
        model = await client.read_char(DeviceInformation.model_number, None)
        firmware = await client.read_char(DeviceInformation.firmware_version, None)
        manufacturer = await client.read_char(DeviceInformation.manufacturer_name, None)
        device_name = await client.read_char(
            DeviceConfiguration.custom_device_name, None
        )

        click.echo("Device Information:")
        if device_name:
            click.echo(f"  Name:         {device_name}")
        if model:
            click.echo(f"  Model:        {model}")
        if firmware:
            click.echo(f"  Firmware:     {firmware}")
        if manufacturer:
            click.echo(f"  Manufacturer: {manufacturer}")

        # Battery
        battery = await client.read_char(Battery.battery_level, None)
        if battery is not None:
            click.echo(f"  Battery:      {battery}%")

        # Valve state - try Valve1 first, fall back to legacy Valve
        click.echo()
        click.echo("Valve Status:")
        v1_state = await client.read_char(Valve1.state, None)
        if v1_state is not None:
            remaining = await client.read_char(Valve1.remaining_time_open, None)
            error = await client.read_char(Valve1.error, None)
            click.echo(f"  State:     {'Open' if v1_state else 'Closed'}")
            if remaining is not None:
                click.echo(f"  Remaining: {remaining // 60}m {remaining % 60}s")
            if error is not None and error != 0:
                click.echo(f"  Error:     {error}")
        else:
            v_state = await client.read_char(Valve.state, None)
            if v_state is not None:
                remaining = await client.read_char(Valve.remaining_open_time, None)
                click.echo(f"  State:     {'Open' if v_state else 'Closed'}")
                if remaining is not None:
                    click.echo(f"  Remaining: {remaining // 60}m {remaining % 60}s")
            else:
                click.echo("  (not available)")


@main.group()
async def water():
    """Control manual watering."""
    pass


@water.command()
@click.argument("minutes", type=int)
@address_option
async def start(minutes: int, address: str | None):
    """Start manual watering for MINUTES minutes."""
    if minutes <= 0:
        raise click.ClickException("Duration must be positive")

    seconds = minutes * 60
    async with connect_device(address) as client:
        # Try Valve1 (newer API) first, fall back to legacy Valve
        chars = await client.get_all_characteristics_uuid()
        if Valve1.manual_watering_duration.uuid in chars:
            await client.write_char(Valve1.manual_watering_duration, seconds)
            await client.write_char(Valve1.start_watering, {})
        elif Valve.remaining_open_time.uuid in chars:
            await client.write_char(Valve.remaining_open_time, seconds)
        else:
            raise click.ClickException("No supported valve control found on device")
        click.echo(f"Watering started for {minutes} minutes")


@water.command()
@address_option
async def stop(address: str | None):
    """Stop manual watering."""
    async with connect_device(address) as client:
        chars = await client.get_all_characteristics_uuid()
        if Valve1.stop_watering.uuid in chars:
            await client.write_char(Valve1.stop_watering, {})
        elif Valve.remaining_open_time.uuid in chars:
            await client.write_char(Valve.remaining_open_time, 0)
        else:
            raise click.ClickException("No supported valve control found on device")
        click.echo("Watering stopped")


@main.group("schedule")
async def schedule_group():
    """Manage watering schedules (1-5)."""
    pass


@schedule_group.command("list")
@address_option
async def schedule_list(address: str | None):
    """List all watering schedules."""
    async with connect_device(address) as client:
        for i, sched in enumerate(SCHEDULES, 1):
            active = await client.read_char(sched.active, None)
            if active is None:
                continue

            start_time = await client.read_char(sched.start_time, None)
            duration = await client.read_char(sched.duration, None)
            weekdays = await client.read_char(sched.weekdays, None)

            status_str = "ON" if active else "OFF"
            time_str = (
                f"{start_time // 3600:02d}:{(start_time % 3600) // 60:02d}"
                if start_time is not None
                else "--:--"
            )
            dur_str = f"{duration // 60}min" if duration is not None else "-"
            days_str = (
                ", ".join(d for d, on in zip(DAY_NAMES, weekdays) if on)
                if weekdays
                else "-"
            )

            click.echo(
                f"  Schedule {i}: [{status_str}] {time_str}  {dur_str}  {days_str}"
            )


@schedule_group.command("set")
@click.argument("number", type=click.IntRange(1, 5))
@click.option("--start", "start_time", default=None, help="Start time HH:MM")
@click.option("--duration", type=int, default=None, help="Duration in minutes")
@click.option("--weekdays", "weekdays_str", default=None, help="Days: Mon,Tue,Wed,...")
@click.option("--active/--inactive", default=None, help="Enable or disable schedule")
@address_option
async def schedule_set(
    number: int,
    start_time: str | None,
    duration: int | None,
    weekdays_str: str | None,
    active: bool | None,
    address: str | None,
):
    """Set a watering schedule."""
    sched = SCHEDULES[number - 1]

    if all(v is None for v in [start_time, duration, weekdays_str, active]):
        raise click.ClickException(
            "Provide at least one of: --start, --duration, --weekdays, --active/--inactive"
        )

    async with connect_device(address) as client:
        if start_time is not None:
            try:
                h, m = start_time.split(":")
                seconds = int(h) * 3600 + int(m) * 60
            except ValueError:
                raise click.ClickException("Invalid time format, use HH:MM")
            await client.write_char(sched.start_time, seconds)

        if duration is not None:
            await client.write_char(sched.duration, duration * 60)

        if weekdays_str is not None:
            day_map = {d.lower(): i for i, d in enumerate(DAY_NAMES)}
            bits = [False] * 7
            for day in weekdays_str.split(","):
                day = day.strip().lower()[:3]
                if day not in day_map:
                    raise click.ClickException(
                        f"Unknown day '{day}'. Use: {', '.join(DAY_NAMES)}"
                    )
                bits[day_map[day]] = True
            await client.write_char(sched.weekdays, bits)

        if active is not None:
            await client.write_char(sched.active, active)

        click.echo(f"Schedule {number} updated")


@main.group("config")
async def config_group():
    """View or change device configuration."""
    pass


@config_group.command("show")
@address_option
async def config_show(address: str | None):
    """Show device configuration."""
    async with connect_device(address) as client:
        name = await client.read_char(DeviceConfiguration.custom_device_name, None)
        rain = await client.read_char(DeviceConfiguration.rain_pause, None)
        seasonal = await client.read_char(DeviceConfiguration.seasonal_adjust, None)
        timestamp = await client.read_char(DeviceConfiguration.unix_timestamp, None)
        brightness = await client.read_char(
            DeviceConfiguration.display_brightness, None
        )
        language = await client.read_char(DeviceConfiguration.device_language, None)

        click.echo("Device Configuration:")
        if name is not None:
            click.echo(f"  Name:            {name}")
        if rain is not None:
            click.echo(f"  Rain pause:      {rain}s ({rain // 3600}h)")
        if seasonal is not None:
            click.echo(f"  Seasonal adjust: {seasonal}%")
        if timestamp is not None:
            click.echo(f"  Device time:     {timestamp}")
        if brightness is not None:
            click.echo(f"  Brightness:      {brightness}")
        if language is not None:
            click.echo(f"  Language:        {language}")


@config_group.command("set")
@click.option("--rain-pause", type=int, default=None, help="Rain pause in hours")
@click.option(
    "--seasonal-adjust", type=int, default=None, help="Seasonal adjustment percent"
)
@click.option("--name", default=None, help="Custom device name")
@address_option
async def config_set(
    rain_pause: int | None,
    seasonal_adjust: int | None,
    name: str | None,
    address: str | None,
):
    """Set device configuration values."""
    if all(v is None for v in [rain_pause, seasonal_adjust, name]):
        raise click.ClickException(
            "Provide at least one of: --rain-pause, --seasonal-adjust, --name"
        )

    async with connect_device(address) as client:
        if rain_pause is not None:
            await client.write_char(
                DeviceConfiguration.rain_pause, rain_pause * 3600
            )
            click.echo(f"  Rain pause set to {rain_pause}h")

        if seasonal_adjust is not None:
            await client.write_char(
                DeviceConfiguration.seasonal_adjust, seasonal_adjust
            )
            click.echo(f"  Seasonal adjust set to {seasonal_adjust}%")

        if name is not None:
            await client.write_char(DeviceConfiguration.custom_device_name, name)
            click.echo(f"  Device name set to '{name}'")


@config_group.command("sync-time")
@address_option
async def config_sync_time(address: str | None):
    """Sync device clock to local time."""
    async with connect_device(address) as client:
        await client.update_timestamp(
            DeviceConfiguration.unix_timestamp, datetime.now()
        )
        click.echo("Device time synced")


@main.command()
@address_option
async def history(address: str | None):
    """Show watering history and flow statistics."""
    async with connect_device(address) as client:
        # Watering history
        timestamps = await client.read_char(WateringHistory.timestamp_array, None)
        durations = await client.read_char(WateringHistory.watering_duration, None)
        count = await client.read_char(WateringHistory.timestamp_count, None)

        click.echo("Watering History:")
        if timestamps and durations:
            for ts, dur in zip(timestamps, durations):
                click.echo(f"  {ts}  {dur // 60}m {dur % 60}s")
        elif count is not None:
            click.echo(f"  {count} entries (no detail available)")
        else:
            click.echo("  (no history)")

        # Flow statistics
        click.echo()
        click.echo("Flow Statistics:")
        overall = await client.read_char(FlowStatistics.overall, None)
        resettable = await client.read_char(FlowStatistics.resettable, None)
        last_reset = await client.read_char(FlowStatistics.last_reset, None)
        current = await client.read_char(FlowStatistics.current, None)

        if overall is not None:
            click.echo(f"  Overall:    {overall}L")
        if resettable is not None:
            click.echo(f"  Resettable: {resettable}L")
        if last_reset is not None:
            click.echo(f"  Last reset: {last_reset}")
        if current is not None:
            click.echo(f"  Current:    {current}L/h")
        if all(v is None for v in [overall, resettable, last_reset, current]):
            click.echo("  (not available)")


@main.group("sensor")
async def sensor_group():
    """View or configure the soil moisture sensor."""
    pass


@sensor_group.command("show")
@address_option
async def sensor_show(address: str | None):
    """Show sensor status."""
    async with connect_device(address) as client:
        value = await client.read_char(Sensor.value, None)
        connected = await client.read_char(Sensor.connected_state, None)
        threshold = await client.read_char(Sensor.threshold, None)
        battery = await client.read_char(Sensor.battery_level, None)
        timestamp = await client.read_char(Sensor.measurement_timestamp, None)
        sensor_type = await client.read_char(Sensor.type, None)

        click.echo("Sensor Status:")
        if connected is not None:
            click.echo(f"  Connected: {'Yes' if connected else 'No'}")
        if value is not None:
            click.echo(f"  Value:     {value}%")
        if threshold is not None:
            click.echo(f"  Threshold: {threshold}%")
        if battery is not None:
            click.echo(f"  Battery:   {battery}%")
        if timestamp is not None:
            click.echo(f"  Measured:  {timestamp}")
        if sensor_type is not None:
            click.echo(f"  Type:      {sensor_type}")


@sensor_group.command("set")
@click.option("--threshold", type=int, required=True, help="Moisture threshold percent")
@address_option
async def sensor_set(threshold: int, address: str | None):
    """Set sensor threshold."""
    async with connect_device(address) as client:
        await client.write_char(Sensor.threshold, threshold)
        click.echo(f"Sensor threshold set to {threshold}%")


@sensor_group.command("measure")
@address_option
async def sensor_measure(address: str | None):
    """Trigger a sensor measurement."""
    async with connect_device(address) as client:
        await client.write_char(Sensor.force_measurement, 1)
        click.echo("Measurement triggered")


@main.command()
async def tui():
    """Launch interactive terminal UI."""
    from .tui import GardenaTUI

    app = GardenaTUI()
    await app.run_async()


try:
    main()
except KeyboardInterrupt:
    pass
