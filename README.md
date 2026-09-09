# BasePilot

**Autopilot for your Clash of Clans base.** BasePilot farms, upgrades, and knows when
to do nothing — it runs unattended until your village genuinely has nothing left to
start, then waits for a builder to free up and gets back to work.

Windows desktop app for **Clash of Clans on Google Play Games (PC)**. It plays the
game the way a person does: screen capture, computer vision, and clicks. No memory
reading, no packet manipulation, no modified client.

**[Download the latest release](../../releases/latest)** — a single `BasePilot.exe`
with the OCR engine bundled in, so there is nothing to install. This repository holds
the full source; see [Running from source](#running-from-source) to build it yourself.

![BasePilot running beside Clash of Clans: the Run page shows Maxer mode with "Run until
maxed" enabled, and the Live Status panel reports IDLING with 0/6 builders free and both
storages full](docs/basepilot-screenshot.png)

*BasePilot idling on purpose: storages are capped and every builder is busy, so it holds
position and rechecks instead of raiding for loot that would overflow.*

---

## What it does

**Farming.** Finds matches, deploys your army (Valkyries, Sneaky Goblins, Super
Minions, or Edrags), collects loot, returns home, and recovers on its own from popups,
disconnects, and stray screens. Builder Base farming included. See
[Army setup](#army-setup) for what to bring.

**Auto upgrade (beta).** Reads the builder menu with OCR and spends your loot:

- **Maxer** — starts an affordable upgrade and never touches your Town Hall. Priciest
  first by default, since a farmed account is builder-limited rather than loot-limited;
  switch to cheapest first in *Settings → Upgrade order* to spread builders across more
  jobs. Dark elixir upgrades (heroes) always get first claim either way.
- **Rusher** — takes the Town Hall as soon as it's affordable.
- **Dry run** — logs what it *would* start and clicks nothing. Good first setting.

**Run until maxed.** No time limit. Farm → spend → and when storages are full with
every builder busy, BasePilot **idles** instead of raiding for loot that would
overflow, rechecking every few minutes and resuming the moment something frees up.
It only stops when you tell it to.

**Wall upgrades.** Batch-buys walls when loot passes a threshold you set, elixir first,
keeping enough gold for match entry fees.

**Loot filter.** Reads the loot a matched base is holding and presses **Next** until one
clears your minimums (500k gold and 500k elixir by default), so an army is never spent on
a 40k base. Bounded by a skip cap, because every Next costs another search fee. Set it in
*Settings -> Minimum loot to attack*; the Run page counts the bases it skipped.

**Clan assist (beta).** Between raids it opens the clan menu and serves every open
request — longest-waiting first, following the game's own "!" badge for the ones the
screen isn't showing, and giving each troop until the game greys it out — then asks for
reinforcements of its own. It only leaves once no Donate button and no badge remain, and
it holds off donating while your elixir is under a floor you set. It keeps doing that
through the long idle stretches of *Run until maxed*, when requests pile up unanswered.
Every click is the game's own button, matched on screen rather than remembered as a
coordinate, and the close button doubles as proof the menu opened and closed. The button and troop templates it
matches ship with BasePilot for 16:9 clients; on 16:10, or to add a troop, *Settings →
Clan assist → **Capture templates*** crops them from your own client at the right scale.
Leave it in **dry run** for a session first to see what it recognises. Full setup and
mechanics: [docs/clan.md](docs/clan.md).

**Loot tracking.** Every raid's gold, elixir, and dark elixir gains are read straight
off the HUD and accumulated into a session total plus a **loot-per-hour rate**, so you
can see what an army or strategy is actually earning you instead of guessing. Readings
require agreement across consecutive frames and are sanity-checked against what a
single raid can plausibly yield, so a bad OCR frame can't inflate your numbers. (Note
that a capped storage banks nothing — the rate reflects real gains, not raid count.)

**Live status.** Current state, free builders, laboratory, and storage levels at a
glance, alongside the loot readout.

Works at **any Town Hall level** — detection reads the game's own UI signals (builder
chip, lab chip, storage indicators) rather than hardcoded per-TH values.

Not automated yet: starting laboratory research and Pet House upgrades. BasePilot
tracks the lab and tells you when it's idle, but you start those two yourself.

## Safety rails

Automation that spends resources has to be careful, so BasePilot:

- Never confirms a purchase whose cost shows red (unaffordable → gem-spend risk).
- Requires the screen to **name the building it picked** before any purchase click, so
  a mis-aimed click can't buy the wrong thing.
- Verifies every upgrade actually started by checking the builder counter afterward.
- Escapes unknown dialogs via their close button or empty ground — never a blind "OK".
- Keeps a gold buffer so matchmaking entry fees are never spent away.
- Screenshots anything it couldn't verify to `%LOCALAPPDATA%\BasePilot\debug\` and
  benches that upgrade instead of retrying blindly.
- Refuses to confirm a donation or request through a button that shows a price (red cost
  or a gem icon beside it) — both are free, so a price tag means it found the wrong one.
- Gives up rather than flails: five clan-menu passes it couldn't make sense of disable
  clan assist for the session instead of clicking a bad template all night.

## Requirements

- Windows 10/11
- Clash of Clans running in **Google Play Games on PC**
- The game rendering at **16:9** or 16:10

**Ultrawide / 21:9 monitors:** Google Play Games locks the game's aspect ratio to your
display resolution at launch. Use *Settings → Switch display to 16:9*, fully close and
reopen Clash, then *Restore my display* — the running game keeps 16:9.

## Getting started

1. Download `BasePilot.exe` from [Releases](../../releases/latest) and run it (no
   installer; settings live in `%LOCALAPPDATA%\BasePilot`). The exe is unsigned, so
   Windows SmartScreen warns on first launch — *More info → Run anyway*.
2. Open the game, then press **Test** on the Settings page to confirm BasePilot can see
   it. Use **Auto-detect** or pick the window manually if needed.
3. On the Run page, choose your army ([what to bring](#army-setup)), set
   **Auto upgrade → Dry run** for the first session, and press **Start**. Watch the Logs page
   to see what it would do.
4. Happy with its choices? Switch to **Maxer**, enable **Run until maxed**, and set
   *Settings → Reserve builders* (use 0 if your walls are maxed).

Command line, for scheduled or overnight runs:

```
BasePilot.exe --autostart --minutes 0 --walls --upgrades maxer --donate --request
```

`--minutes 0` means run until maxed. `--upgrades off|dry|maxer|rusher`. `--donate`
and `--request` arm clan assist (capture its templates first — see
[docs/clan.md](docs/clan.md)).

Farming more than one account — the Players page runs them one after another, and
`--profile NAME` gives a second BasePilot window its own settings, window pin and log:
[docs/two-accounts.md](docs/two-accounts.md).

## Army setup

![Saved Recipes showing Army 1: 42 Valkyries, 11 Earthquake spells, and 1 Log Launcher, with
Queen, King, Warden, and Royal Champion and their pets](docs/army-valkyrie.png)

**42 Valkyries · 11 Earthquake spells · 1 Log Launcher**, plus your heroes and pets — 336/352
housing and a full 11/11 spell bar.

The 11 Earthquakes aren't arbitrary. BasePilot places exactly 11 earthquake points per raid, so
a full spell bar means every one of them lands a spell.

Three things to get right before you press Start:

- **Be on the Home Village.** Switch there yourself, or pick *Home Village* in the app and let
  the bot switch for you.
- **Use the default deployment bar layout.** Two rows is fine. BasePilot finds troops by
  matching their icons in that bar, so a customised layout can hide them.
- **Keep this army at the top of your Saved Recipes.** If Valkyries aren't already in your
  deploy bar, BasePilot opens Saved Recipes and clicks **Use** next to the Valkyrie row — but it
  only looks for that button close to the row it matched, so a recipe further down the list
  scrolls out of range and army loading fails.

If the troop you picked on the Run page isn't in your army, the raid aborts immediately and the
log reads `Troop <name> not found!`.

Sneaky Goblins and Super Minions deploy the same way. Edrags need at least 12. Full deploy
mechanics — drag patterns, earthquake placement modes, hero order — are in
[docs/armies.md](docs/armies.md).

## Running from source

Python 3.11+ on Windows:

```
pip install -r requirements.txt
python main.py
```

OCR needs a Tesseract 5 install. For development, BasePilot falls back to
`C:\Program Files\Tesseract-OCR\tesseract.exe`, so a
[UB-Mannheim build](https://github.com/UB-Mannheim/tesseract/wiki) is enough — or point
`TESSERACT_CMD` at any `tesseract.exe` you prefer.

To build the one-file exe, copy that Tesseract install (`tesseract.exe`, its DLLs, and
`tessdata/`) into `tesseract_bundle/` and run:

```
pyinstaller BasePilot.spec
```

`tesseract_bundle/` is gitignored to keep the repo light — it is ~40 MB of Apache-2.0
binaries. The release workflow in `.github/workflows/release.yml` recreates it on a
Windows runner and publishes the exe automatically on every `v*` tag.

## License

MIT — see [LICENSE](LICENSE). Tesseract, bundled into the released exe, ships under the
Apache 2.0 license.

## Disclaimer

Automating Clash of Clans **violates Supercell's Terms of Service and can get your
account banned.** BasePilot is published for educational purposes — it's a real-world
exercise in computer vision, OCR, and UI automation against an animated, adversarial
target. Use it on an account you're willing to lose, or don't use it at all. No
warranty; you accept all risk.

Not affiliated with, endorsed by, or associated with Supercell. Clash of Clans is a
trademark of Supercell Oy.
