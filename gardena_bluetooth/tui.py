from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass

from textual import work, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll, Grid
from textual.screen import Screen, ModalScreen
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from .client import Client
from .config import get_default_address, load_config
from .const import (
    Battery,
    DeviceConfiguration,
    DeviceInformation,
    Schedule_1,
    Schedule_2,
    Schedule_3,
    Schedule_4,
    Schedule_5,
    Sensor,
    Valve,
    Valve1,
)
from .parse import ProductType

from bleak import BleakScanner

SCHEDULES = [Schedule_1, Schedule_2, Schedule_3, Schedule_4, Schedule_5]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# --- Data classes for edit results ---


@dataclass
class ScheduleData:
    number: int
    start_time: int | None = None  # seconds from midnight
    duration: int | None = None  # seconds
    weekdays: list[bool] | None = None
    active: bool | None = None


@dataclass
class SensorData:
    threshold: int | None = None
    force_measure: bool = False


@dataclass
class ConfigData:
    rain_pause: int | None = None  # seconds
    seasonal_adjust: int | None = None
    device_name: str | None = None
    sync_time: bool = False


# --- CSS ---

APP_CSS = """
Screen {
    background: $surface;
}

#main-container {
    height: 1fr;
}

.section {
    border: solid $primary;
    padding: 1 2;
    margin: 0 1 1 1;
    height: auto;
}

.section-title {
    text-style: bold;
    color: $text;
}

.section-header {
    height: 1;
    margin-bottom: 1;
}

.section-header Button {
    dock: right;
    min-width: 8;
    height: 1;
}

.info-label {
    width: 16;
    color: $text-muted;
}

.info-value {
    width: 1fr;
}

#watering-controls {
    height: 3;
    margin-top: 1;
}

#watering-controls Button {
    margin-right: 1;
}

#duration-input {
    width: 20;
    margin-right: 1;
}

.schedule-btn {
    width: 1fr;
    height: 1;
    min-width: 10;
    background: $surface;
    border: none;
    text-align: left;
    padding: 0;
}

.schedule-btn:hover {
    background: $primary-background;
}

#device-list {
    height: 1fr;
}

#connection-status {
    dock: bottom;
    height: 1;
    background: $primary-background;
    padding: 0 2;
}

/* Modal styles */

ModalScreen {
    align: center middle;
}

.modal-dialog {
    width: 60;
    height: auto;
    max-height: 80%;
    border: thick $primary;
    background: $surface;
    padding: 1 2;
}

.modal-title {
    text-style: bold;
    width: 1fr;
    content-align: center middle;
    margin-bottom: 1;
}

.modal-row {
    height: 3;
    margin-bottom: 0;
}

.modal-label {
    width: 20;
    height: 3;
    content-align: left middle;
}

.modal-input {
    width: 1fr;
}

.modal-days {
    height: auto;
    margin-bottom: 1;
}

.modal-days Checkbox {
    width: auto;
    height: 1;
    margin-right: 1;
    padding: 0;
}

.modal-buttons {
    height: 3;
    margin-top: 1;
    align: center middle;
}

.modal-buttons Button {
    margin: 0 1;
}
"""


# --- Widgets ---


class InfoRow(Horizontal):
    DEFAULT_CSS = """
    InfoRow { height: 1; }
    """

    def __init__(self, label: str, value_id: str, **kwargs):
        super().__init__(**kwargs)
        self._label = label
        self._value_id = value_id

    def compose(self) -> ComposeResult:
        yield Label(self._label, classes="info-label")
        yield Label("-", id=self._value_id, classes="info-value")


# --- Modal Screens ---


class ScheduleEditModal(ModalScreen[ScheduleData | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        number: int,
        start_time: int = 0,
        duration: int = 0,
        weekdays: list[bool] | None = None,
        active: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.number = number
        self._start_time = start_time
        self._duration = duration
        self._weekdays = weekdays or [False] * 7
        self._active = active

    def compose(self) -> ComposeResult:
        h = self._start_time // 3600
        m = (self._start_time % 3600) // 60

        with Vertical(classes="modal-dialog"):
            yield Static(f"Edit Schedule {self.number}", classes="modal-title")

            with Horizontal(classes="modal-row"):
                yield Label("Start time (HH:MM):", classes="modal-label")
                yield Input(f"{h:02d}:{m:02d}", id="edit-start", classes="modal-input")

            with Horizontal(classes="modal-row"):
                yield Label("Duration (min):", classes="modal-label")
                yield Input(
                    str(self._duration // 60),
                    id="edit-duration",
                    type="integer",
                    classes="modal-input",
                )

            yield Label("Weekdays:")
            with Horizontal(classes="modal-days"):
                for i, day in enumerate(DAY_NAMES):
                    yield Checkbox(day, self._weekdays[i], id=f"day-{i}")

            with Horizontal(classes="modal-row"):
                yield Label("Active:", classes="modal-label")
                yield Checkbox("", self._active, id="edit-active")

            with Horizontal(classes="modal-buttons"):
                yield Button("Save", id="modal-save", variant="success")
                yield Button("Cancel", id="modal-cancel")

    @on(Button.Pressed, "#modal-save")
    def on_save(self) -> None:
        start_str = self.query_one("#edit-start", Input).value.strip()
        try:
            parts = start_str.split(":")
            start_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60
        except (ValueError, IndexError):
            return

        dur_str = self.query_one("#edit-duration", Input).value.strip()
        try:
            duration_seconds = int(dur_str) * 60
        except ValueError:
            return

        weekdays = [self.query_one(f"#day-{i}", Checkbox).value for i in range(7)]
        active = self.query_one("#edit-active", Checkbox).value

        self.dismiss(
            ScheduleData(
                number=self.number,
                start_time=start_seconds,
                duration=duration_seconds,
                weekdays=weekdays,
                active=active,
            )
        )

    @on(Button.Pressed, "#modal-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class SensorEditModal(ModalScreen[SensorData | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, threshold: int = 0, **kwargs):
        super().__init__(**kwargs)
        self._threshold = threshold

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("Sensor Settings", classes="modal-title")

            with Horizontal(classes="modal-row"):
                yield Label("Threshold (%):", classes="modal-label")
                yield Input(
                    str(self._threshold),
                    id="edit-threshold",
                    type="integer",
                    classes="modal-input",
                )

            with Horizontal(classes="modal-row"):
                yield Label("Force measure:", classes="modal-label")
                yield Checkbox("", False, id="edit-measure")

            with Horizontal(classes="modal-buttons"):
                yield Button("Save", id="modal-save", variant="success")
                yield Button("Cancel", id="modal-cancel")

    @on(Button.Pressed, "#modal-save")
    def on_save(self) -> None:
        thr_str = self.query_one("#edit-threshold", Input).value.strip()
        try:
            threshold = int(thr_str)
        except ValueError:
            return

        force = self.query_one("#edit-measure", Checkbox).value
        self.dismiss(SensorData(threshold=threshold, force_measure=force))

    @on(Button.Pressed, "#modal-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfigEditModal(ModalScreen[ConfigData | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        rain_pause: int = 0,
        seasonal_adjust: int = 100,
        device_name: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._rain_pause = rain_pause
        self._seasonal_adjust = seasonal_adjust
        self._device_name = device_name

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("Device Configuration", classes="modal-title")

            with Horizontal(classes="modal-row"):
                yield Label("Device name:", classes="modal-label")
                yield Input(
                    self._device_name, id="edit-name", classes="modal-input"
                )

            with Horizontal(classes="modal-row"):
                yield Label("Rain pause (hours):", classes="modal-label")
                yield Input(
                    str(self._rain_pause // 3600),
                    id="edit-rain",
                    type="integer",
                    classes="modal-input",
                )

            with Horizontal(classes="modal-row"):
                yield Label("Seasonal adjust (%):", classes="modal-label")
                yield Input(
                    str(self._seasonal_adjust),
                    id="edit-seasonal",
                    type="integer",
                    classes="modal-input",
                )

            with Horizontal(classes="modal-row"):
                yield Label("Sync time:", classes="modal-label")
                yield Checkbox("Sync device clock to local time", False, id="edit-sync")

            with Horizontal(classes="modal-buttons"):
                yield Button("Save", id="modal-save", variant="success")
                yield Button("Cancel", id="modal-cancel")

    @on(Button.Pressed, "#modal-save")
    def on_save(self) -> None:
        name = self.query_one("#edit-name", Input).value.strip() or None
        try:
            rain = int(self.query_one("#edit-rain", Input).value.strip()) * 3600
        except ValueError:
            rain = None
        try:
            seasonal = int(self.query_one("#edit-seasonal", Input).value.strip())
        except ValueError:
            seasonal = None

        sync = self.query_one("#edit-sync", Checkbox).value

        self.dismiss(
            ConfigData(
                rain_pause=rain,
                seasonal_adjust=seasonal,
                device_name=name,
                sync_time=sync,
            )
        )

    @on(Button.Pressed, "#modal-cancel")
    def on_cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --- Screens ---


class DeviceSelectScreen(Screen):
    BINDINGS = [Binding("escape", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(" Select a device:", id="select-title")
        yield ListView(id="device-list")
        yield Footer()

    def on_mount(self) -> None:
        config = load_config()
        device_list = config.get("devices", {})
        default = config.get("default")
        lv = self.query_one("#device-list", ListView)

        for addr, info in device_list.items():
            name = info.get("name", addr)
            product = info.get("product_type", "UNKNOWN")
            marker = " *" if addr == default else ""
            item = ListItem(Label(f"{name}{marker}  ({product})  {addr}"), name=addr)
            lv.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.app.open_dashboard(event.item.name)

    def action_quit(self) -> None:
        self.app.exit()


class DashboardScreen(Screen):
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("w", "focus_duration", "Water"),
        Binding("s", "stop_water", "Stop"),
        Binding("d", "switch_device", "Devices"),
        Binding("escape", "quit", "Quit"),
    ]

    address: reactive[str] = reactive("")
    product_type: reactive[ProductType] = reactive(ProductType.UNKNOWN)

    def __init__(self, address: str, product_type: ProductType, **kwargs):
        super().__init__(**kwargs)
        self.address = address
        self.product_type = product_type
        # Cache current values for edit modals
        self._schedule_cache: dict[int, dict] = {}
        self._sensor_cache: dict = {}
        self._config_cache: dict = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="main-container"):
            # Device Status
            with Vertical(classes="section", id="status-section"):
                yield Static("Device Status", classes="section-title")
                yield InfoRow("Name:", "val-name")
                yield InfoRow("Model:", "val-model")
                yield InfoRow("Firmware:", "val-firmware")
                yield InfoRow("Battery:", "val-battery")
                yield InfoRow("Valve:", "val-valve")
                yield InfoRow("Remaining:", "val-remaining")

            # Watering Control
            with Vertical(classes="section", id="watering-section"):
                yield Static("Watering Control", classes="section-title")
                with Horizontal(id="watering-controls"):
                    yield Input(
                        placeholder="Minutes",
                        id="duration-input",
                        type="integer",
                    )
                    yield Button("Start", id="btn-start", variant="success")
                    yield Button("Stop", id="btn-stop", variant="error")

            # Sensor
            with Vertical(classes="section", id="sensor-section"):
                with Horizontal(classes="section-header"):
                    yield Static("Sensor", classes="section-title")
                    yield Button("Edit", id="btn-sensor-edit", variant="primary")
                yield InfoRow("Connected:", "val-sensor-connected")
                yield InfoRow("Moisture:", "val-sensor-value")
                yield InfoRow("Threshold:", "val-sensor-threshold")
                yield InfoRow("Battery:", "val-sensor-battery")
                yield InfoRow("Measured:", "val-sensor-time")

            # Schedules
            with Vertical(classes="section", id="schedule-section"):
                yield Static("Schedules (click to edit)", classes="section-title")
                for i in range(1, 6):
                    yield Button(
                        f"  Schedule {i}: -",
                        id=f"sched-btn-{i}",
                        classes="schedule-btn",
                    )

            # Config
            with Vertical(classes="section", id="config-section"):
                with Horizontal(classes="section-header"):
                    yield Static("Configuration", classes="section-title")
                    yield Button("Edit", id="btn-config-edit", variant="primary")
                yield InfoRow("Device name:", "val-cfg-name")
                yield InfoRow("Rain pause:", "val-cfg-rain")
                yield InfoRow("Seasonal adj:", "val-cfg-seasonal")
                yield InfoRow("Device time:", "val-cfg-time")

        yield Static("Connecting...", id="connection-status")
        yield Footer()

    def on_mount(self) -> None:
        self.load_data()

    # --- BLE helper ---

    async def _connect(self) -> Client | None:
        ble_device = await BleakScanner.find_device_by_address(
            self.address, timeout=10
        )
        if ble_device is None:
            self._status("Device not found nearby")
            return None
        return Client(ble_device, self.product_type)

    def _status(self, msg: str) -> None:
        self.query_one("#connection-status", Static).update(msg)

    def _set_val(self, widget_id: str, value: str | None) -> None:
        self.query_one(f"#{widget_id}", Label).update(value or "-")

    # --- Load all data ---

    @work(exclusive=True, group="ble")
    async def load_data(self) -> None:
        self._status(f"Connecting to {self.address}...")
        try:
            client = await self._connect()
            if client is None:
                return
            try:
                await self._read_status(client)
                await self._read_sensor(client)
                await self._read_schedules(client)
                await self._read_config(client)
                self._status(f"Connected: {self.address}")
            finally:
                await client.disconnect()
        except Exception as exc:
            self._status(f"Error: {exc}")

    async def _read_status(self, client: Client) -> None:
        name = await client.read_char(DeviceConfiguration.custom_device_name, None)
        model = await client.read_char(DeviceInformation.model_number, None)
        firmware = await client.read_char(DeviceInformation.firmware_version, None)
        battery = await client.read_char(Battery.battery_level, None)

        v1_state = await client.read_char(Valve1.state, None)
        if v1_state is not None:
            valve_str = "Open" if v1_state else "Closed"
            remaining = await client.read_char(Valve1.remaining_time_open, None)
        else:
            v_state = await client.read_char(Valve.state, None)
            valve_str = (
                ("Open" if v_state else "Closed") if v_state is not None else "-"
            )
            remaining = await client.read_char(Valve.remaining_open_time, None)

        remaining_str = (
            f"{remaining // 60}m {remaining % 60}s"
            if remaining is not None and remaining > 0
            else "-"
        )

        self._set_val("val-name", name)
        self._set_val("val-model", model)
        self._set_val("val-firmware", firmware)
        self._set_val("val-battery", f"{battery}%" if battery is not None else None)
        self._set_val("val-valve", valve_str)
        self._set_val("val-remaining", remaining_str)

    async def _read_sensor(self, client: Client) -> None:
        value = await client.read_char(Sensor.value, None)
        connected = await client.read_char(Sensor.connected_state, None)
        threshold = await client.read_char(Sensor.threshold, None)
        battery = await client.read_char(Sensor.battery_level, None)
        timestamp = await client.read_char(Sensor.measurement_timestamp, None)

        self._sensor_cache = {"threshold": threshold or 0}

        self._set_val(
            "val-sensor-connected",
            ("Yes" if connected else "No") if connected is not None else None,
        )
        self._set_val("val-sensor-value", f"{value}%" if value is not None else None)
        self._set_val(
            "val-sensor-threshold", f"{threshold}%" if threshold is not None else None
        )
        self._set_val(
            "val-sensor-battery", f"{battery}%" if battery is not None else None
        )
        self._set_val(
            "val-sensor-time", str(timestamp) if timestamp is not None else None
        )

    async def _read_schedules(self, client: Client) -> None:
        for i, sched in enumerate(SCHEDULES, 1):
            active = await client.read_char(sched.active, None)
            if active is None:
                continue

            start_time = await client.read_char(sched.start_time, None) or 0
            duration = await client.read_char(sched.duration, None) or 0
            weekdays = await client.read_char(sched.weekdays, None) or [False] * 7

            self._schedule_cache[i] = {
                "start_time": start_time,
                "duration": duration,
                "weekdays": weekdays,
                "active": active,
            }

            self._update_schedule_label(i, active, start_time, duration, weekdays)

    def _update_schedule_label(
        self,
        i: int,
        active: bool,
        start_time: int,
        duration: int,
        weekdays: list[bool],
    ) -> None:
        status_str = "ON " if active else "OFF"
        time_str = f"{start_time // 3600:02d}:{(start_time % 3600) // 60:02d}"
        dur_str = f"{duration // 60}min"
        days_str = ",".join(d for d, on in zip(DAY_NAMES, weekdays) if on) or "-"
        btn = self.query_one(f"#sched-btn-{i}", Button)
        btn.label = f"  Schedule {i}: [{status_str}] {time_str}  {dur_str}  {days_str}"

    async def _read_config(self, client: Client) -> None:
        name = await client.read_char(DeviceConfiguration.custom_device_name, None)
        rain = await client.read_char(DeviceConfiguration.rain_pause, None)
        seasonal = await client.read_char(DeviceConfiguration.seasonal_adjust, None)
        timestamp = await client.read_char(DeviceConfiguration.unix_timestamp, None)

        self._config_cache = {
            "device_name": name or "",
            "rain_pause": rain or 0,
            "seasonal_adjust": seasonal if seasonal is not None else 100,
        }

        self._set_val("val-cfg-name", name)
        self._set_val(
            "val-cfg-rain",
            f"{rain}s ({rain // 3600}h)" if rain is not None else None,
        )
        self._set_val(
            "val-cfg-seasonal", f"{seasonal}%" if seasonal is not None else None
        )
        self._set_val("val-cfg-time", str(timestamp) if timestamp is not None else None)

    # --- Button handlers ---

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-start":
            inp = self.query_one("#duration-input", Input)
            val = inp.value.strip()
            if val and val.isdigit() and int(val) > 0:
                self.do_water_start(int(val))
            else:
                self._status("Enter a valid duration in minutes")
        elif bid == "btn-stop":
            self.do_water_stop()
        elif bid == "btn-sensor-edit":
            self._open_sensor_edit()
        elif bid == "btn-config-edit":
            self._open_config_edit()
        elif bid and bid.startswith("sched-btn-"):
            num = int(bid.split("-")[-1])
            self._open_schedule_edit(num)

    # --- Watering ---

    @work(exclusive=True, group="ble")
    async def do_water_start(self, minutes: int) -> None:
        self._status(f"Starting watering for {minutes}min...")
        try:
            client = await self._connect()
            if client is None:
                return
            try:
                seconds = minutes * 60
                chars = await client.get_all_characteristics_uuid()
                if Valve1.manual_watering_duration.uuid in chars:
                    await client.write_char(Valve1.manual_watering_duration, seconds)
                    await client.write_char(Valve1.start_watering, {})
                elif Valve.remaining_open_time.uuid in chars:
                    await client.write_char(Valve.remaining_open_time, seconds)
                else:
                    self._status("No valve control found")
                    return
                self._status(f"Watering started for {minutes}min")
                self._set_val("val-valve", "Open")
                self._set_val("val-remaining", f"{minutes}m 0s")
            finally:
                await client.disconnect()
        except Exception as exc:
            self._status(f"Error: {exc}")

    @work(exclusive=True, group="ble")
    async def do_water_stop(self) -> None:
        self._status("Stopping watering...")
        try:
            client = await self._connect()
            if client is None:
                return
            try:
                chars = await client.get_all_characteristics_uuid()
                if Valve1.stop_watering.uuid in chars:
                    await client.write_char(Valve1.stop_watering, {})
                elif Valve.remaining_open_time.uuid in chars:
                    await client.write_char(Valve.remaining_open_time, 0)
                else:
                    self._status("No valve control found")
                    return
                self._status("Watering stopped")
                self._set_val("val-valve", "Closed")
                self._set_val("val-remaining", "-")
            finally:
                await client.disconnect()
        except Exception as exc:
            self._status(f"Error: {exc}")

    # --- Schedule edit ---

    def _open_schedule_edit(self, number: int) -> None:
        cached = self._schedule_cache.get(number, {})
        self.app.push_screen(
            ScheduleEditModal(
                number=number,
                start_time=cached.get("start_time", 0),
                duration=cached.get("duration", 0),
                weekdays=cached.get("weekdays"),
                active=cached.get("active", False),
            ),
            callback=self._on_schedule_result,
        )

    def _on_schedule_result(self, result: ScheduleData | None) -> None:
        if result is not None:
            self.do_save_schedule(result)

    @work(exclusive=True, group="ble")
    async def do_save_schedule(self, data: ScheduleData) -> None:
        self._status(f"Saving schedule {data.number}...")
        try:
            client = await self._connect()
            if client is None:
                return
            try:
                sched = SCHEDULES[data.number - 1]
                if data.start_time is not None:
                    await client.write_char(sched.start_time, data.start_time)
                if data.duration is not None:
                    await client.write_char(sched.duration, data.duration)
                if data.weekdays is not None:
                    await client.write_char(sched.weekdays, data.weekdays)
                if data.active is not None:
                    await client.write_char(sched.active, data.active)

                # Update cache and label
                self._schedule_cache[data.number] = {
                    "start_time": data.start_time,
                    "duration": data.duration,
                    "weekdays": data.weekdays,
                    "active": data.active,
                }
                self._update_schedule_label(
                    data.number,
                    data.active,
                    data.start_time,
                    data.duration,
                    data.weekdays,
                )
                self._status(f"Schedule {data.number} saved")
            finally:
                await client.disconnect()
        except Exception as exc:
            self._status(f"Error: {exc}")

    # --- Sensor edit ---

    def _open_sensor_edit(self) -> None:
        self.app.push_screen(
            SensorEditModal(threshold=self._sensor_cache.get("threshold", 0)),
            callback=self._on_sensor_result,
        )

    def _on_sensor_result(self, result: SensorData | None) -> None:
        if result is not None:
            self.do_save_sensor(result)

    @work(exclusive=True, group="ble")
    async def do_save_sensor(self, data: SensorData) -> None:
        self._status("Saving sensor settings...")
        try:
            client = await self._connect()
            if client is None:
                return
            try:
                if data.threshold is not None:
                    await client.write_char(Sensor.threshold, data.threshold)
                    self._sensor_cache["threshold"] = data.threshold
                    self._set_val("val-sensor-threshold", f"{data.threshold}%")

                if data.force_measure:
                    await client.write_char(Sensor.force_measurement, 1)

                self._status("Sensor settings saved")
            finally:
                await client.disconnect()
        except Exception as exc:
            self._status(f"Error: {exc}")

    # --- Config edit ---

    def _open_config_edit(self) -> None:
        self.app.push_screen(
            ConfigEditModal(
                rain_pause=self._config_cache.get("rain_pause", 0),
                seasonal_adjust=self._config_cache.get("seasonal_adjust", 100),
                device_name=self._config_cache.get("device_name", ""),
            ),
            callback=self._on_config_result,
        )

    def _on_config_result(self, result: ConfigData | None) -> None:
        if result is not None:
            self.do_save_config(result)

    @work(exclusive=True, group="ble")
    async def do_save_config(self, data: ConfigData) -> None:
        self._status("Saving configuration...")
        try:
            client = await self._connect()
            if client is None:
                return
            try:
                if data.device_name is not None:
                    await client.write_char(
                        DeviceConfiguration.custom_device_name, data.device_name
                    )
                    self._set_val("val-cfg-name", data.device_name)
                    self._set_val("val-name", data.device_name)

                if data.rain_pause is not None:
                    await client.write_char(
                        DeviceConfiguration.rain_pause, data.rain_pause
                    )
                    self._set_val(
                        "val-cfg-rain",
                        f"{data.rain_pause}s ({data.rain_pause // 3600}h)",
                    )

                if data.seasonal_adjust is not None:
                    await client.write_char(
                        DeviceConfiguration.seasonal_adjust, data.seasonal_adjust
                    )
                    self._set_val("val-cfg-seasonal", f"{data.seasonal_adjust}%")

                if data.sync_time:
                    await client.update_timestamp(
                        DeviceConfiguration.unix_timestamp, datetime.now()
                    )
                    self._set_val("val-cfg-time", str(datetime.now().replace(microsecond=0)))

                self._config_cache.update(
                    {
                        k: v
                        for k, v in {
                            "device_name": data.device_name,
                            "rain_pause": data.rain_pause,
                            "seasonal_adjust": data.seasonal_adjust,
                        }.items()
                        if v is not None
                    }
                )
                self._status("Configuration saved")
            finally:
                await client.disconnect()
        except Exception as exc:
            self._status(f"Error: {exc}")

    # --- Key bindings ---

    def action_refresh(self) -> None:
        self.load_data()

    def action_focus_duration(self) -> None:
        self.query_one("#duration-input", Input).focus()

    def action_stop_water(self) -> None:
        self.do_water_stop()

    def action_switch_device(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()


# --- App ---


class GardenaTUI(App):
    TITLE = "Gardena Water Control"
    CSS = APP_CSS
    BINDINGS = [Binding("escape", "quit", "Quit")]

    def on_mount(self) -> None:
        config = load_config()
        device_list = config.get("devices", {})

        if not device_list:
            self.exit(message="No devices registered. Use 'register' first.")
            return

        default = get_default_address(config)
        if default and len(device_list) == 1:
            self.open_dashboard(default)
        else:
            self.push_screen(DeviceSelectScreen())

    def open_dashboard(self, address: str) -> None:
        config = load_config()
        device_info = config.get("devices", {}).get(address, {})
        product_type = ProductType[device_info.get("product_type", "UNKNOWN")]
        self.push_screen(DashboardScreen(address, product_type))

    def action_quit(self) -> None:
        self.exit()
