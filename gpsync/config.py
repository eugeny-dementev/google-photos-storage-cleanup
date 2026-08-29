from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DATA_DIR = Path("gpsync-data")
DEFAULT_OUTPUT_DIR = Path("photos")


@dataclass
class Settings:
    data_dir: Path
    output_dir: Path
    cookies_path: Path
    db_path: Path
    account_index: int = 0
    workers: int = 2
    request_pause_s: float = 0.15

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure_dirs()
        payload = {
            "output_dir": str(self.output_dir.resolve()),
            "account_index": self.account_index,
            "workers": self.workers,
            "request_pause_s": self.request_pause_s,
        }
        self.config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_settings(
    data_dir: Path | None = None,
    output_dir: Path | None = None,
    account_index: int | None = None,
    workers: int | None = None,
) -> Settings:
    data = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    config_path = data / "config.json"
    stored: dict[str, Any] = {}
    if config_path.exists():
        stored = json.loads(config_path.read_text(encoding="utf-8"))

    settings = Settings(
        data_dir=data,
        output_dir=Path(output_dir or stored.get("output_dir") or DEFAULT_OUTPUT_DIR),
        cookies_path=data / "cookies.txt",
        db_path=data / "photos.db",
        account_index=account_index if account_index is not None else int(stored.get("account_index", 0)),
        workers=workers if workers is not None else int(stored.get("workers", 2)),
        request_pause_s=float(stored.get("request_pause_s", 0.15)),
    )
    return settings
