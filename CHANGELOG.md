# Changelog

## v1.0.0

First BasePilot release. Previous builds were published as "Clash AutoLoot"; this
version renames the app, redesigns the interface, and adds the base-progression
autopilot.

### Added

- **Auto upgrade (beta)** — reads the builder menu with OCR and starts upgrades:
  Maxer (most expensive first, Town Hall excluded, dark elixir prioritised), Rusher
  (takes the Town Hall), and Dry run (logs decisions without clicking).
- **Run until maxed** — unlimited sessions that farm, spend, and idle when the village
  has nothing to start, resuming automatically when a builder frees up.
- **Live status panel** — state, free builders, laboratory, storages, session loot and
  loot per hour.
- **Reserve builders** and **upgrade order** settings.
- Purchase safety: screen-name verification before every buy, red-cost veto,
  builder-counter confirmation, and debug screenshots for anything unverified.
- Handling for the daily reward popup, which previously could stall a session.

### Changed

- Renamed to **BasePilot** with a new mission-control interface and logo. Settings from
  older installs migrate automatically on first launch.
- Loot tracking now requires agreement across frames before counting a gain, so bad OCR
  reads no longer inflate session totals.
- Resource readings use the game's own affordability signals where possible, making
  detection work at any Town Hall level.

### Fixed

- Loot tracking never ran when wall upgrades were disabled.
- Sessions could end early after temporary troop-training failures.
- Storage "full" detection idled the bot while storages still had room to fill.
