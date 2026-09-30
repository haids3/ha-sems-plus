# GoodWe SEMS+ for Home Assistant

A Home Assistant integration for GoodWe systems on the **SEMS+** cloud (the
SEMS+ app and semsplus.goodwe.com). It talks only to SEMS+; accounts still on
classic SEMS are served by
[TimSoethout/goodwe-sems-home-assistant](https://github.com/TimSoethout/goodwe-sems-home-assistant).

## How it is set up

- **One entry per SEMS+ account.** You sign in once and then pick which of the
  account's stations to add. An installer account that can see many customers'
  stations only brings in the ones you choose.
- **Each station is a subentry** under the account. Add more later with
  **Add station**, or delete one to drop it.
- **Per-station settings:** the update interval, and whether controls are
  allowed. Every station of an account shares one request queue, so give
  stations you only keep an eye on a longer interval.
- **Controls are off by default.** Turning on *Allow controls* for a station
  creates entities that change real hardware: inverter run/stop and battery
  immediate charging.

## What you get

**Station:** PV, battery, grid and load power, battery SOC, status, active
alarms (with the active alarm list as an attribute), online, alarm and grid
connection (battery stations only), and today and lifetime energy for
production, import, export, consumption, self-use and battery charge and
discharge, plus self-sufficiency and self-use rates.

**Inverter / All-in-One:** power, temperature, operating hours, AC voltage,
current and frequency, per-string PV power, voltage and current, PV energy
(today, week, month, year, total), battery charge and discharge energy, and
status. With controls allowed: a **Run** switch.

**Battery rack:** SOC, state of health, power, voltage, current, cell
temperature, charge limits and energy counters.

**Smart meter:** power, per-phase power, voltage and current, power factor,
frequency, and import and export energy.

**Battery system** (with controls allowed): **Immediate charging** switch, end
SOC and charge power.

Sensors are only created for values a device actually reports, and devices
that go offline keep their entities, shown as unavailable.

Sign conventions follow SEMS+: battery power is positive while discharging,
and grid power is positive while importing.

## Installation

Add this repository to HACS as a custom repository (category *Integration*),
install **GoodWe SEMS+**, restart, then add the integration.

## Credits

Built on [`sems-plus-client`](https://github.com/haids3/sems-plus-client). The
SEMS+ login scheme was worked out in
[TimSoethout/goodwe-sems-home-assistant](https://github.com/TimSoethout/goodwe-sems-home-assistant),
where this integration's API research started. Not affiliated with GoodWe.
