# GoodWe SEMS+ for Home Assistant

A Home Assistant integration for GoodWe systems on the **SEMS+** cloud (the
SEMS+ app and semsplus.goodwe.com). It talks only to SEMS+; accounts still on
classic SEMS are served by
[TimSoethout/goodwe-sems-home-assistant](https://github.com/TimSoethout/goodwe-sems-home-assistant).

## Requirements

- Home Assistant 2026.8 or later.
- A SEMS+ account: the email and password you sign in to the SEMS+ app or
  [semsplus.goodwe.com](https://semsplus.goodwe.com) with. Owner and
  installer accounts both work.

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) and choose **Custom repositories**.
2. Add `https://github.com/haids3/ha-sems-plus` with the type
   **Integration**.
3. Search HACS for **GoodWe SEMS+**, open it and select **Download**.
4. Restart Home Assistant.

### Manual

1. Download the latest
   [release](https://github.com/haids3/ha-sems-plus/releases).
2. Copy `custom_components/sems_plus` into the `custom_components` folder
   of your Home Assistant configuration.
3. Restart Home Assistant.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for
   **GoodWe SEMS+**.
2. Sign in with your SEMS+ email and password.
3. Choose the stations to add. The first 8 are ticked; untick the ones you
   don't want, or tick more.

Each station appears as its own entry under the account, with its devices
(inverter or All-in-One, battery racks, smart meter and so on) under it.

- **Add a station later:** open the integration and select **Add station**.
- **Change a station's settings:** open the station's menu (⋮) and select
  **Reconfigure**. You can set the update interval (60 seconds by default,
  30 at least) and turn **Allow controls** on or off.
- **Password changed:** Home Assistant asks you to sign in again.

## Using it

- **Energy dashboard:**
  - *Grid consumption:* smart meter **Import total**
  - *Return to grid:* smart meter **Export total**
  - *Solar production:* inverter **PV energy total**
  - *Home battery storage:* inverter **Battery charge total** and
    **Battery discharge total**

  A station with several inverters or meters also has station-wide
  **Production total**, **Import total**, **Export total** and battery
  totals; use those instead.
- **Controls** (with *Allow controls* on, where SEMS+ grants your account
  remote control) go through SEMS+, so the app, the portal and Home
  Assistant stay in sync.
- **Firmware updates:** each device's **Firmware update** sensor turns on
  when SEMS+ has firmware waiting for it. Install it in SEMS+ or ask your
  installer.

## Removing

Delete a single station from its menu (⋮), or the whole account from the
integration's entry. If you installed through HACS, also remove the
integration in HACS and restart Home Assistant.

## How it works

- **One entry per SEMS+ account.** You sign in once and then pick which of the
  account's stations to add. An installer account that can see many customers'
  stations only brings in the ones you choose.
- **Each station is a subentry** under the account. Add more later with
  **Add station**, or delete one to drop it.
- **Per-station settings:** the update interval, and whether controls are
  allowed. Every station of an account shares one request queue, so give
  stations you only keep an eye on a longer interval.
- **Controls are off by default.** Turning on *Allow controls* for a station
  creates entities that change real hardware: inverter run/stop, export
  limiting, restart, work modes, the TOU schedule and battery immediate
  charging. They only appear when SEMS+ itself grants your account remote
  control of that station.
- **Live power flow.** Station power and SOC are pushed every few seconds over
  SEMS+'s own live feed, on top of the regular polling.

## What you get

**Station:** PV, battery, grid and load power (and third-party PV, EV
charger, heat pump or generator power where the station has them), battery SOC, status, active
alarms (with the active alarm list as an attribute), online, alarm and grid
connection (battery stations only), and today and lifetime energy for
production, import, export, consumption, self-use and battery charge and
discharge, plus self-sufficiency and self-use rates.

**Inverter / All-in-One:** power, temperature, operating hours, AC voltage,
current and frequency, per-string PV power, voltage and current, PV energy
(today, week, month, year, total), battery charge and discharge energy, and
**Status** (running, standby, shutdown, ...), **Grid** (on-grid or off-grid),
model, the **Export limit** state and power (read-only, shown even
with controls off; installer logins only, as SEMS+ hides it from owners), a
**Firmware** sensor and a **Firmware update** sensor (on when
SEMS+ has firmware waiting, with the components and versions listed, and
whether your account may install them in SEMS+). With controls allowed:
**Start** and **Stop** where SEMS+ offers them (**Start** and **Shut down** on
grid-tie inverters), **Restart**, **Export limit** and its power, and on battery inverters a **Work mode** device (the running mode;
switches for TOU, backup, off-grid, peak shaving and smart charge, which the
API calls delayed charge, as far as the inverter offers them; and their
settings: backup grid charging and power, peak-shaving SOC, import limit and
window, smart-charge export limit, PV priority and time) and the TOU slots (on/off, charge or discharge, start,
end, power, cutoff SOC, and for discharge slots whether the power limits
battery discharge or export; unused slots start disabled). Inverters on
SEMS+ work-mode version 1 run one mode at a time, so they get a **Configured
mode** select (self-use, backup, TOU or off-grid) instead of the mode switches,
and four TOU slots without months or cutoff SOC. The work mode and each TOU
slot are also shown read-only when controls are off.

**Battery rack:** SOC, state of health, power, voltage, current, cell
temperatures and voltages, charge and discharge limits, energy counters, model
and a **Firmware** (BMS) sensor.

**Smart meter:** power, per-phase power, voltage and current, power factor,
frequency, and import and export energy.

**Battery system** (with controls allowed): **Immediate charging** switch, end
SOC and charge power.

Sensors are only created for values a device actually reports, and devices
that go offline keep their entities, shown as unavailable.

Battery power is positive while discharging, and grid power positive while
importing. SEMS+ reports grid power the other way round; the integration
flips it.

## Credits

Built on [`sems-plus-client`](https://github.com/haids3/sems-plus-client). The
SEMS+ login scheme was worked out in
[TimSoethout/goodwe-sems-home-assistant](https://github.com/TimSoethout/goodwe-sems-home-assistant),
where this integration's API research started. Not affiliated with GoodWe.
