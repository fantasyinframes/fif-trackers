# Fantasy In Frames trackers

Weekly data for the Fantasy In Frames trackers. Updates itself every week; nothing to upload.

## How it works

Every **Tuesday and Wednesday morning** (about 8 AM Eastern), GitHub runs `.github/workflows/weekly-update.yml`:

1. **Positions** – `scripts/ourlads.py` reads all 32 Ourlads depth charts and updates `data/positions.json`
   (QB/RB/WR/TE with WR Outside/Slot, and Interior DL / Edge / LB / CB / S using each team's 3-4 or 4-3 scheme).
2. **Stats** – `scripts/build_data.py` pulls nflverse weekly stats, play-by-play and snap counts and builds the data
   files. It only includes weeks where **every game is final and snap counts are posted**, so a half-finished week
   never goes live.
3. **Publish** – if any stat or position changed, it commits the new files. GitHub Pages serves them, and the
   trackers on the site read them when a reader opens the page.

| File | Used by |
|---|---|
| `data/combined.js` | Offense + IDP Value Tracker (premium) |
| `data/offense.js` | Offense Fantasy Points Tracker (free) |
| `data/idp.js` | IDP trackers (free and premium) |

The tracker pages themselves are **not** stored here (this repo is public); they live only in WordPress.
They don't need re-uploading when stats change.

## Data rules

- **Offense:** nflverse weekly player stats. Fumbles lost and 2-pt conversions include all play types.
- **IDP:** counted from nflverse play-by-play. Special-teams plays are skipped for tackles, sacks, TFLs, QB hits
  and passes defended. Forced fumbles, fumble recoveries (takeaways only) and blocked kicks count on special teams.
- **Snap %:** nflverse snap counts (offense % for offense players, defense % for IDP).
- Players who only played special teams in a week show as a 0-point game in the IDP trackers.

Credit line on the trackers: *Stats and snap counts: nflverse open NFL data (CC BY 4.0). Positions from Ourlads depth charts.*

## Fixing a player's position

Add the player to `data/position_overrides.json` by nflverse player ID (it wins over Ourlads). Example:

```json
{
  "00-0040718": {"pos": "WR", "wr": "Slot", "idp_pos": "CB"}
}
```

Fields: `pos` (QB, RB, WR, TE, IDL, EDGE, LB, CB, S), `wr` (Outside or Slot), `detail` (the IDP trackers' label:
DE, DT, OLB, ILB, CB, SCB, FS, SS), `idp_pos` (defensive position for two-way players).

## Running it by hand

GitHub → **Actions** → **Weekly tracker update** → **Run workflow**. Leave the box blank for the latest complete
week, or enter a week number.
