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
status, model, the **Export limit** state and power (read-only, shown even
with controls off; installer logins only, as SEMS+ hides it from owners), a
**Firmware** sensor and a **Firmware update** sensor (on when
SEMS+ has firmware waiting, with the components and versions listed, and
whether your account may install them in SEMS+). With controls allowed: **Run** (or **Start** and
**Shut down** on grid-tie inverters), **Restart**, **Export limit** and its
power, and on battery inverters a **Work mode** device (the running mode;
switches for TOU, backup, off-grid, peak shaving and delayed charge, as far as
the inverter offers them; and their settings: backup grid charging and power,
peak-shaving SOC, import limit and window, delayed-charge export limit, PV
priority and time) and the TOU slots (on/off, charge or discharge, start,
end, power, cutoff SOC, and for discharge slots whether the power limits
battery discharge or export; unused slots start disabled). The work mode and
each TOU slot are also shown read-only when controls are off.

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

## Installation

Add this repository to HACS as a custom repository (category *Integration*),
install **GoodWe SEMS+**, restart, then add the integration.

## Credits

Built on [`sems-plus-client`](https://github.com/haids3/sems-plus-client). The
SEMS+ login scheme was worked out in
[TimSoethout/goodwe-sems-home-assistant](https://github.com/TimSoethout/goodwe-sems-home-assistant),
where this integration's API research started. Not affiliated with GoodWe.
