# Development notes

Working notes for `sems_plus` and its client library,
[`sems-plus-client`](https://github.com/haids3/sems-plus-client). Read this
before changing either.

The API research behind both (endpoint reference, request shapes, what the
decompiled app got wrong) lives in the predecessor fork:
[`docs/sems-plus-api-notes.md`](https://github.com/haids3/goodwe-sems-home-assistant/blob/feat/alarms-and-grid-status/docs/sems-plus-api-notes.md).
That fork (`haids3/goodwe-sems-home-assistant`, branch
`feat/alarms-and-grid-status`) is frozen; new work happens here.

## Why this exists

The fork of `TimSoethout/goodwe-sems-home-assistant` had diverged too far to
keep rebasing. It carried a legacy-API fallback that is dead code for SEMS+
accounts (the SEMS+ gateway 404s every classic route), and one config entry per
station, which made several stations on one account share a password and burst
the API into HTTP 429.

## Layout

| piece | role |
|---|---|
| `sems_plus_client` | aiohttp client: login and signing, request spacing, rate-limit pause, typed models. No Home Assistant dependency. |
| `__init__.py` | one client per account entry; one coordinator per station subentry |
| `coordinator.py` | polls one station; caches slow data; reads controls only when allowed |
| `entity.py` | device info, base entity, entity ID scheme, subentry-aware entity adding |
| `sensor.py` / `binary_sensor.py` | descriptions keyed by SEMS+ factor codes |
| `switch.py` / `number.py` / `select.py` / `button.py` / `time.py` / `control.py` | inverter controls, work modes, TOU slots, battery immediate charging |
| `config_flow.py` | account flow plus the station subentry flow |

### Design decisions

- **SEMS+ only.** No legacy fallback.
- **One entry per account, stations as subentries.** An installer account can
  see hundreds of stations belonging to other people; only chosen ones are
  added. Setup preselects all of them when there are 8 or fewer, otherwise the
  first 8, because every station shares the account's request queue.
- **Controls are opt-in per station and off by default.** SEMS+ gives no
  reliable "this station is mine" signal (`isShared` is true even on an owner's
  station). With them on, they also need the station's `permissions` to include
  `INVERTER_REMOTE`, the check the web portal makes before enabling any
  control. With controls off, the inverter's control menus are never read, but
  the work mode and TOU schedule are, read-only, whenever the station grants
  `INVERTER_REMOTE_READ`: a work-mode sensor and one sensor per TOU slot,
  which exist in both modes so toggling controls does not churn entities.
- **Values keyed by SEMS+ factor codes** (`pAc`, `MPPT-1:Vpv`, `soc`,
  `proPvStatsToday`), not renamed to the old integration's legacy keys.
- **A sensor is created once its value has been seen.** Devices list factors
  they never fill (a meter's phase voltage), and SEMS+ returns nothing at all
  for offline devices, which are not polled and show as unavailable.
- **Controls are discovered, not hard-coded.** Inverter controls come from the
  device's `GENERAL_FUNCTIONS` menu (the web device page's quick settings,
  about 40 KB against 175 KB for the full tree), matched by the `_ControlSpec`
  table in `coordinator.py`: key, widget type, and where needed unit and menu.
  Keys and `funcKey`s both repeat (two `PWLimitThr` limits on an All-in-One,
  a W and a % `limit_setting` on a grid-tie inverter), so neither alone is
  enough. Grid-tie inverters have no `run_stop`; they get Start and Shut down
  buttons. Number controls are only created for `gain: 1` functions until the
  write scaling is confirmed.
  Immediate charging comes from a battery system's `GENERAL_FUNCTIONS` menu.
  Battery writes send the battery's `translateCode` (e.g. `mppt1_battery`) as
  the device name, as the old integration did.
- **Work modes and TOU go through named settings** (`remote/get`, `remote/set`)
  rather than registers: the register groups in the menu are not in slot
  order. They are read for battery inverters on work-mode versions 2 and 3
  only (`get-work-mode`), in one request per poll; version 1 has a single
  exclusive mode and is not handled. A TOU write sends the whole slot with the
  same audit log the web sends. Enabling a slot that has no days or months
  fills in all of them, or it would never run. A slot is edited the way the web
  editor does it: a mode select (charge at zero or negative power, discharge
  above zero), a 0–100 % power, and, on firmware with ARMFunction4 bit 12, a
  discharge limit select (battery or export, stored as month `12`). A
  discharge slot cannot have zero power; that change is refused. The limit
  select only appears when the inverter reports ARMFunction4 bit 12, as in the
  web; some All-in-One firmware returns no ARMFunction4 at all. Each slot is
  its own device under the inverter ("All-in-One 1 TOU slot N"), which is how
  Home Assistant groups a slot's controls on one card.
- **Firmware updates are a binary sensor, not an update entity.**
  `device-upgrade-list` names each waiting release (component, version, date)
  but SEMS+ gives no installed version per component, which an update entity
  needs. Read hourly for inverters and dongles; it needs no controls. The
  station-level `exist-remind` stays false even with updates waiting.
  `can_apply` mirrors the web's upgrade button: the station grants
  `FIRMWARE_UPGRADE` (the installer who owns it), or `exist-force-upgrade`
  says `canOwnerForceUpgrade` for a forced release. Shared stations get
  neither, so the web shows no button there.
- **Live flow over MQTT.** One `SemsPlusLiveFeed` per account subscribes to
  each station's second-data topic. A push only rewrites the flow sensors
  (`StationSensorDescription.live`), not every entity, and a poll returning an
  older `refreshTime` does not overwrite a newer push. Device topics are not
  used yet.
- **Station energy totals only when they add information.** Production,
  import, export and battery charge/discharge are created on the station only
  when it has several inverters or meters (the sum), or none (import/export
  exist only at station level then). With exactly one they duplicate that
  device.

### Naming

- Account entry: titled with the login email.
- Station subentry: `<station name> Station`.
- Station device: "SEMS+ Station". Other devices keep SEMS+'s names ("All-in-One
  1", "Battery Rack 1", "Meter 1", "BAT1").
- Entity IDs: `<domain>.sems_plus_<station>_<device>_<key>`, built from
  description keys rather than translated names, so they are language-proof and
  unique across stations while displayed names stay short.

HA remembers deleted entities and restores their old entity ID when the same
unique ID returns, so a changed ID scheme does not apply to existing
installations by deleting and re-adding the entry. Use the entity table's
**Recreate entity IDs** action instead.

## API behaviour worth knowing

- **Multi-year statistics are all zeros.** `stations/statistics` with
  `dimension: "year"` over a range spanning more than one calendar year returns
  every year as 0.0; a single-year range is correct. Lifetime totals are one
  request per year from `StationInfo.created`, past years cached for a day, and
  a failed year keeps the last complete sum (a partial one would make a
  `total_increasing` sensor drop). Unconfirmed whether the trigger is "several
  years" or "starts before the station existed"; per-year avoids both.
- **Period counters roll over late.** Around midnight SEMS+ serves the previous
  day's `*Today`/`Week`/`Month`/`Year` counters for several minutes; they are
  held from 23:58 to 00:20.
- **pGrid is positive while exporting.** The client negates it so
  `PowerFlow.grid` is import-positive, as Home Assistant expects. The earlier
  API notes had this backwards; a day of 1-minute history settled it.
- **Values refresh once a minute** (`refreshTime`, telemetry); the live feed
  pushes every 5 seconds.
- **Writes wait for the device** (`waitingForDevice`) for 1–30 s and reply with
  no data; `P0215` means the device refused. The client gives writes 90 s and
  does not hold its request queue while one waits.
- **Function values are raw ÷ gain.** `get-cache-device-function-parameters`
  returns values divided by the function's `gain`. Whether writes expect raw or
  divided values for a `gain ≠ 1` function is unconfirmed; run/stop and the
  immediate-charging controls all have gain 1.
- **Device status codes are partly guessed:** 0 offline (confirmed), 1 and 5
  normal, 2 fault, 3 waiting. The app's `DEVICE_STATUS_MAP` could not be
  decoded. Unknown codes read as unknown.
- **A fresh login may replace another session's token**, so the station
  subentry flow reuses the running client instead of logging in again.

## Tests

```
uv venv .venv --python 3.14
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest -q
```

- Fixtures in `tests/fixtures/` are **synthetic**: invented values in the real
  response shapes, run through the client's own parsers. Do not commit data
  captured from a live account; accounts can hold other people's stations.
- `tests/conftest.py` defines its own `snapshot` fixture on purpose. Syrupy
  and the Home Assistant test plugin both define one, and when syrupy's wins it
  looks in `__snapshots__` and reports every snapshot missing (this only showed
  up in CI).
- Update snapshots with `--snapshot-update`, then read the diff.

The client library has its own suite (`.venv/bin/python -m pytest` in its
repo). It uses a `FakeSession` rather than `aioresponses`, which is broken on
aiohttp 3.14.

## Running in a development Home Assistant

1. Install the client editable into HA's environment:
   `uv pip install --python <ha-venv>/bin/python -e <path>/sems-plus-client`
2. Symlink `custom_components/sems_plus` into the HA config's
   `custom_components/`.
3. Start HA with `--skip-pip-packages sems-plus-client`. HA treats every URL
   requirement as "not installed" and would otherwise reinstall the pinned git
   commit over the editable copy on each start.

## CI and validation

- **Lint** and **Test** run on every push to `main`.
- **Validate** (manual) runs hassfest and HACS. HACS passes. **hassfest fails**
  on the manifest requirement: it rejects `name @ git+https://…` ("contains a
  space"). The fix is publishing `sems-plus-client` to PyPI and requiring
  `sems-plus-client==x.y.z`.
- After changing the client, push it first, then bump the commit pinned in
  `manifest.json` and `requirements_test.txt`.

## Open items

- **Not yet exercised on hardware:** every write: run/stop, start/shutdown,
  restart, export limit, work modes, TOU slots and immediate charging. The
  request shapes match the web capture.
- **Not covered by a test:** the midnight counter hold.
- **Not done yet:** work-mode version 1, peak shaving, delayed charge, green
  mode and off-grid mode (all decoded in the API notes); live device topics;
  writes to `gain ≠ 1` numbers.
- **Not carried over from the old integration:** Income Today/Total (legacy-only
  fields, always unknown on SEMS+), Energy Last Month, the HomeKit naming.
- **PyPI release of the client,** which also fixes hassfest.
