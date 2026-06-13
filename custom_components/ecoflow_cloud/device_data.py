from __future__ import annotations  # for DeviceData.parent: DeviceData

import dataclasses


@dataclasses.dataclass
class DeviceOptions:
    refresh_period: int
    power_step: int
    diagnostic_mode: bool
    verbose_status_mode: bool
    assume_offline_sec: int
    ack_timeout_sec: int
    ack_retries: int
    ack_retry_delay_sec: int


@dataclasses.dataclass
class DeviceData:
    sn: str
    name: str
    device_type: str
    options: DeviceOptions
    display_name: str | None
    parent: DeviceData | None
