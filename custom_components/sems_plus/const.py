"""Constants for the GoodWe SEMS+ integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "sems_plus"
MANUFACTURER: Final = "GoodWe"

SUBENTRY_STATION: Final = "station"
CONF_STATION_ID: Final = "station_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ALLOW_CONTROL: Final = "allow_control"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 30

# Static or slow-changing data is fetched less often than live values.
DEVICE_TOPOLOGY_REFRESH: Final = timedelta(hours=1)
CONTROL_TREE_REFRESH: Final = timedelta(hours=6)
DEVICE_INFORMATION_REFRESH: Final = timedelta(hours=6)
FIRMWARE_UPDATE_REFRESH: Final = timedelta(hours=1)
STATISTICS_REFRESH: Final = timedelta(minutes=5)
LIFETIME_STATISTICS_REFRESH: Final = timedelta(hours=1)
# A finished year's statistics no longer change.
PAST_YEAR_REFRESH: Final = timedelta(days=1)
ALARM_LIST_REFRESH: Final = timedelta(minutes=5)

# SEMS+ station status codes.
STATION_STATUS: Final = {
    0: "offline",
    1: "running",
    2: "fault",
    3: "waiting",
    11: "constructing",
}

# SEMS+ device status codes, labelled as the web does. 0 is offline for every
# device type (a device in that state returns no telemetry); a working
# inverter reports 5, a meter or dongle 1.
DEVICE_STATUS: Final = {
    -1: "offline",
    0: "offline",
    1: "online",
    2: "fault",
    3: "standby",
    4: "shutdown",
    5: "running",
    6: "charging",
    7: "discharging",
    8: "idle",
    9: "maintenance",
}
DEVICE_STATUS_OFFLINE: Final = frozenset({-1, 0})
