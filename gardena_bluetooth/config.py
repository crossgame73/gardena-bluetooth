import json
from datetime import datetime
from pathlib import Path

from .parse import ProductType


def get_config_path() -> Path:
    return Path.home() / ".gardena-bluetooth" / "devices.json"


def load_config() -> dict:
    path = get_config_path()
    if path.exists():
        return json.loads(path.read_text())
    return {"default": None, "devices": {}}


def save_config(config: dict) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False))


def get_default_address(config: dict) -> str | None:
    return config.get("default")


def register_device(
    address: str, product_type: ProductType, name: str | None = None
) -> None:
    config = load_config()
    config["devices"][address] = {
        "name": name or address,
        "product_type": product_type.name,
        "registered_at": datetime.now().isoformat(timespec="seconds"),
    }
    config["default"] = address
    save_config(config)
