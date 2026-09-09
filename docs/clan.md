# Clan assist — donations and reinforcement requests

Between raids, BasePilot can open the clan menu, fill the requests your clanmates
have posted, and ask for reinforcements of its own. Both are off by default and both
are armed per run on the **Run page** (*Auto donate*, *Auto request troops*);
everything else lives in *Settings → Clan assist*.

The code is [`app/core/clan.py`](../app/core/clan.py). It is template-driven — the same
capture → match → click loop the attack and upgrade flows use — and uses **no OCR**, so
it works whether or not Tesseract is installed.

## What it clicks, in order

1. **Clan menu button** (`clanmenu.png`) — the orange speech bubble.
2. **Donate** (`donate.png`) — the green button on a clanmate's request, topmost first
   (whoever has waited longest).
3. **The "!" badge** (`clannotify.png`) — pressed only when no Donate button is left on
   screen. It is the game's own way of bringing in the requests this screen isn't
   showing, which is why nothing here scrolls. Followed at most six times per visit.
4. **A troop** (`donatetroop*.png`) — tapped until the game **greys it out**, which is
   the game saying that request (or your camp) is full. Then the next troop crop you
   captured takes over. When everything on screen has greyed, the troop row is **swiped
   left** (up to four times) to reach the troops sitting off the edge of it. A greyed
   icon is never tapped, and each tap hands one troop over — there is nothing to
   confirm.
5. **A tap outside the panel** — how a player leaves the donate panel, since it has no
   close button. (If your client *does* draw a Send there, it is pressed instead.)
6. **Request troops** (`requesttroops.png`) — the castle-with-a-plus button, then
   **Send** (`send.png`) to confirm.
7. **Close** (`clanexit.png`) — the "«" button, back to the village. It only gets here
   once the menu shows **neither a Donate button nor the badge** — that empty screen is
   the proof every request was served. (A visit fills at most twenty requests; a clan
   busier than that gets the rest on the next visit.)

That outside tap sometimes takes the whole clan menu down with the panel, so the menu is
re-opened — verified, the same way it was opened the first time — before the visit
carries on.

`clanexit.png` doubles as the state signal: it exists only while the clan menu is open,
so it is what proves the menu opened and what proves it closed again. Nothing is assumed
about where a click landed.

## Setup

### 1. Capture the templates

BasePilot clicks the game's own art, so it needs a crop of each button **from your
client**. *Settings → Clan assist → **Capture templates*** does this properly: it
screenshots the game, you drag a box around a button, pick what it is, and it saves the
crop **scaled to the reference resolution** (2560×1440 for 16:9, 2560×1600 for 16:10) —
which is what makes matching work at any window size. Cropping by hand from a 1920×1080
screenshot produces a template that matches nothing.

Templates land in `%LOCALAPPDATA%\BasePilot\templates\<aspect>\` and override the
bundled art of the same name, so this works with the released exe as well as from
source. The Settings card lists what is still missing, and clan assist refuses to click
anything until the set is complete.

Tips for good crops:

- Tight to the button's edges, no surrounding background.
- Capture each button on the screen where it actually appears (open the clan menu for
  Donate / Request / the troop / Send).
- `nextbase.png` is the **Next** button on the battle-prep screen — it belongs to the
  loot filter rather than clan assist, but the same dialog captures it.
- Troop crops come **from the donate panel**, not the deploy bar. Capture one per troop
  you are willing to donate: pick *Another troop to donate* and it files them as
  `donatetroop2.png`, `donatetroop3.png`, …
- Re-capture if you switch the game between 16:9 and 16:10.

### 2. Run it dry

*Settings → Clan assist → **Dry run*** is **on by default**. In dry run BasePilot opens
the clan menu, logs every button it recognises, closes the menu again — and clicks
nothing else:

```
Clan assist: opening the clan menu at (128, 960)
Clan assist (dry run): would donate at (402, 615) — 2 Donate button(s) on screen
Clan assist (dry run): would request reinforcements at (356, 1284)
Clan assist: dry run — clan menu walked, nothing clicked
```

That is the check that your templates match. Once those lines look right, turn dry run
off and save.

### 3. Arm it

Run page → **Auto donate** / **Auto request troops** → **Start**.

## The elixir floor

Donated troops have to be retrained, so clan assist checks the HUD **before** it opens
the menu and skips donating while elixir is below *Settings → Clan assist → Elixir
floor* (500k by default; set it to **off** to always donate). Requesting reinforcements
is unaffected — that costs nothing.

Reading the HUD needs OCR, so on an install without Tesseract the check is skipped
rather than blocking donations, and says so in the log:

```
Clan assist: could not read the HUD (OCR unavailable?) — donating without the elixir check
Clan assist: elixir 120000 is below the 500000 floor — skipping donations this pass
```

## When it runs

From the home screen, between raids, on two independent clocks (*Settings → Clan assist
→ How often*): donations **after every raid** by default (the interval shows as "every
raid" at 0), requests every 30 minutes. A visit due on
either clock opens the menu once and does both errands that are due. It also runs while
the bot idles under *Run until maxed* — those stretches last hours, and clanmates keep
asking for troops through them. Multi-run gives each account its own visit. The Builder
Base flow skips it.

## Which troops it donates

Every `donatetroop*.png` on disk — thirteen ship with BasePilot, and your own captures
sit alongside them. A clan asks for whatever it asks for, so capture a crop for any troop
you are happy to give away that is not already covered: pick **Another troop to donate**
in the capture dialog and it files them as the next free `donatetroopN.png`. In the donate panel they are tried in name order and the
first one on screen is handed over. Adding a troop later needs no settings change: drop
in another crop and it is used on the next visit.

The *Donate troop* dropdown is only a fallback for when no crop exists at all: it reuses
the deploy-bar icons the attack flows ship (Valkyrie, Sneaky Goblin, Super Minion,
Edrag), which are the same troops drawn on a different background and match less
reliably.

The Settings card tells you how many troop crops it can see (`All templates captured —
3 troop(s) captured`).

**How much it gives** is decided by the game, not by a number you pick: it keeps tapping
until the troop greys out, moves to the next troop, and swipes the row left for the ones
that are off screen. *Settings → Clan assist → troops per request* is a cap for
when you want to be less generous — leave it at **until grey** to fill every request as
far as it will go.

## Safety rails

- **Send is the only click that could ever cost anything** — donating never presses it,
  and a request made during castle cooldown is where the game offers a gem boost. It is
  vetoed when the art around it shows a red cost or a gem icon.
- **No stale coordinates.** Every target is re-found on a fresh frame immediately before
  the click that uses it — including the troop between taps, so when the request fills
  and the panel closes itself the batch just ends.
- **No blind confirms.** A panel is escaped by its own close button, or — for the donate
  panel, which has none — a tap on empty ground. Never by pressing whatever button
  happens to be there.
- **The menu is always closed again** before the loop taps Attack; an open panel would
  swallow that tap.
- **It gives up instead of flailing.** Five passes that could not verify what they were
  looking at disable clan assist for the rest of the session, with the reason on the
  status bar. Frames it could not make sense of are saved to
  `%LOCALAPPDATA%\BasePilot\debug\clan_*.jpg`.

## Command line

```
BasePilot.exe --autostart --minutes 0 --upgrades maxer --donate --request
```

`--donate` and `--request` set the two Run page toggles; templates, dry run, and the
knobs come from your saved settings.

## When it doesn't work

| Symptom | Cause | Fix |
| --- | --- | --- |
| `missing template(s) …` | Those crops were never captured | *Settings → Clan assist → Capture templates* |
| `clanmenu.png not found on the home screen` | Crop is off, or taken at the wrong scale | Re-capture it with the dialog (never by hand) |
| `clan menu did not open` | The click landed but the panel didn't render, or `clanexit.png` doesn't match | Re-capture `clanexit.png`; check the debug frame |
| Dry run logs the menu but no buttons | No open requests in your clan, or `donate.png` / `requesttroops.png` don't match | Confirm a request is actually on screen, then re-capture |
| `donate panel showed no known troop icon` | The panel is showing a troop you have no crop for | Capture that troop too (*Another troop to donate*) |
| `whole troop row is grey` but you had troops | The row did not swipe (drag landed off the row), or you have no crop for those troops | Capture the missing troops; check the debug frame for where the row sits |
| `… is greyed out` on the first tap | The crop carries too much dark background, so a coloured icon reads as grey | Re-capture it tight to the portrait; the Logs page (debug) prints the % coloured it measured |
| Donations don't register | A troop crop matches something else in the panel | Re-capture that troop tightly, then watch a dry run |
| `elixir … is below the … floor` | Working as intended — you are below the elixir floor | Lower it or set it to **off** in Settings |
| `Clan assist disabled for this session` | Five unverified passes | Re-capture, then restart the run |

The Logs page carries every line above, and each unverified state leaves a JPEG in
`%LOCALAPPDATA%\BasePilot\debug\` showing exactly what the bot was looking at.

## Fallback: the chat button point

If `clanmenu.png` cannot be matched at all (an unusual client, a themed skin), *Settings
→ Clan assist → Pick chat button* lets you hand-pick the spot to click instead, stored
as a fraction of the capture. It replaces only that first click — the rest of the flow
still needs its templates, `clanexit.png` included.

## Limits

- Home Village only.
- Request *messages* are not read, so BasePilot cannot honour "no spells please" — it
  donates the troop you configured.
- It does not track donation ratios, clan games, or war reinforcement rules.
