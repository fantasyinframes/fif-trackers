"""Build the Fantasy In Frames tracker data files from nflverse.

Sources (all openly licensed, CC BY 4.0, via nflverse):
  - weekly player stats      -> offense box scores
  - play-by-play             -> IDP box scores (defensive plays only, no special-teams tackles)
  - snap counts              -> snap % for every player-game
Positions (Interior DL / Edge / LB / CB / S, and WR Outside/Slot) come from
data/positions.json, which scripts/ourlads.py refreshes from Ourlads depth charts,
with data/position_overrides.json applied on top for manual fixes.

Writes:
  data/combined.json  {"updated","through","players","weeks"}   (Offense + IDP Value Tracker, premium)
  data/offense.json   same shape, offense players only           (Offense tracker, free)
  data/idp.json       {"updated","through","weeks":{wk:[{name,team,position,solo,...}]}}  (IDP trackers)

Usage: python scripts/build_data.py [--season 2026] [--through N] [--cache DIR]
"""
import argparse, collections, csv, datetime, gzip, io, json, os, sys, urllib.request

REL = "https://github.com/nflverse/nflverse-data/releases/download/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

OFF_POS = {"QB", "RB", "WR", "TE"}
IDP_POS = {"IDL", "EDGE", "LB", "CB", "S"}
# nflverse position -> tracker position when Ourlads has no entry for the player
NV_IDP_FALLBACK = {"DT": "IDL", "NT": "IDL", "DE": "EDGE", "OLB": "EDGE", "LB": "LB", "ILB": "LB", "MLB": "LB",
                   "CB": "CB", "DB": "CB", "S": "S", "SAF": "S", "FS": "S", "SS": "S"}
NV_OFF_FALLBACK = {"QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "WR": "WR", "TE": "TE"}


def fetch(path, cache):
    """Download an nflverse release file (cached locally when --cache is given)."""
    name = os.path.basename(path)
    if cache:
        p = os.path.join(cache, name)
        if os.path.exists(p):
            return open(p, "rb").read()
    req = urllib.request.Request(REL + path, headers={"User-Agent": "fif-trackers"})
    with urllib.request.urlopen(req, timeout=300) as r:
        b = r.read()
    if cache:
        os.makedirs(cache, exist_ok=True)
        open(os.path.join(cache, name), "wb").write(b)
    return b


def rows(b):
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    return list(csv.DictReader(io.StringIO(b.decode("utf-8"))))


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if f == int(f) else f


def load_json(name, default):
    p = os.path.join(DATA, name)
    return json.load(open(p)) if os.path.exists(p) else default


def pbp_defense(pbp):
    """Per (gsis_id, week) defensive counts from play-by-play. Special-teams plays are
    skipped for tackles and other box-score stats; blocked kicks are credited from them."""
    C = collections.defaultdict(collections.Counter)
    team = {}
    for p in pbp:
        if p["season_type"] != "REG":
            continue
        w = int(p["week"])
        st = p["special_teams_play"] == "1" or p["play_type"] in ("kickoff", "punt", "field_goal", "extra_point")
        dt = p["defteam"]

        def add(pid, k, v=1, any_play=False):
            if pid and pid != "NA" and (any_play or not st):
                C[(pid, w)][k] += v
                team.setdefault((pid, w), dt)

        for i in (1, 2):
            add(p[f"solo_tackle_{i}_player_id"], "solo")
            add(p[f"tackle_with_assist_{i}_player_id"], "solo")  # the tackler on an assisted play: counts as a solo
            add(p[f"tackle_for_loss_{i}_player_id"], "tfl")
            add(p[f"qb_hit_{i}_player_id"], "qbh")
            add(p[f"pass_defense_{i}_player_id"], "pd")
            add(p[f"forced_fumble_player_{i}_player_id"], "ff", any_play=True)  # takeaways count on special teams
            add(p[f"half_sack_{i}_player_id"], "sack", 0.5)
            fr = p[f"fumble_recovery_{i}_player_id"]
            # a takeaway: on punts nflverse lists the receiving team as defteam, so the
            # punting (cover) team is posteam there; on every other play it's defteam
            take = p["posteam"] if p["play_type"] == "punt" else dt
            if fr and fr != "NA" and p[f"fumble_recovery_{i}_team"] == take:
                add(fr, "fr", any_play=True)  # recoveries count on special teams too
        for i in (1, 2, 3, 4):
            add(p[f"assist_tackle_{i}_player_id"], "ast")
        add(p["sack_player_id"], "sack")
        add(p["interception_player_id"], "int")
        if p["safety"] == "1":
            add(p["safety_player_id"], "sfty")
        tdp = p["td_player_id"]
        if tdp not in ("", "NA") and p["td_team"] == dt and (p["return_touchdown"] == "1" or not st):
            add(tdp, "dtd")
        bp = p.get("blocked_player_id")
        if bp not in (None, "", "NA"):
            C[(bp, w)]["blk"] += 1
            team.setdefault((bp, w), dt)
    return C, team


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=int(os.environ.get("SEASON", datetime.date.today().year)))
    ap.add_argument("--through", type=int, default=0, help="last week to include (0 = last complete week)")
    ap.add_argument("--cache", default="")
    a = ap.parse_args()
    y = a.season

    stats = [r for r in rows(fetch(f"stats_player/stats_player_week_{y}.csv", a.cache)) if r["season_type"] == "REG"]
    snaps = [r for r in rows(fetch(f"snap_counts/snap_counts_{y}.csv", a.cache)) if r["game_type"] == "REG"]
    pbp = rows(fetch(f"pbp/play_by_play_{y}.csv.gz", a.cache))
    sched = [r for r in rows(fetch("schedules/games.csv", a.cache)) if r["season"] == str(y) and r["game_type"] == "REG"]
    players = rows(fetch("players/players.csv", a.cache))

    # ---- which weeks are complete: every scheduled game has a final score AND snap counts posted
    games_by_week = collections.defaultdict(set)
    final_by_week = collections.defaultdict(set)
    for g in sched:
        w = int(g["week"])
        games_by_week[w].add(g["game_id"])
        if g["home_score"] not in ("", "NA"):
            final_by_week[w].add(g["game_id"])
    snap_games = collections.defaultdict(set)
    for s in snaps:
        snap_games[int(s["week"])].add(s["game_id"])
    stat_games = collections.defaultdict(set)
    for s in stats:
        stat_games[int(s["week"])].add(s["game_id"])
    complete = [w for w in sorted(games_by_week)
                if games_by_week[w] and games_by_week[w] <= final_by_week[w]
                and games_by_week[w] <= snap_games[w] and games_by_week[w] <= stat_games[w]]
    last = 0
    for w in complete:  # contiguous from week 1
        if w == last + 1:
            last = w
    through = a.through or last
    if through > last:
        sys.exit(f"Week {through} is not complete yet (last complete week: {last}).")
    if through == 0:
        sys.exit("No complete weeks yet.")
    weeks = range(1, through + 1)

    # ---- player lookups
    pfr2gsis = {p["pfr_id"]: p["gsis_id"] for p in players if p.get("pfr_id") and p.get("gsis_id")}
    pinfo = {p["gsis_id"]: p for p in players if p.get("gsis_id")}
    snap = {}  # (gsis, week) -> row
    for s in snaps:
        g = pfr2gsis.get(s["pfr_player_id"])
        if g:
            snap[(g, int(s["week"]))] = s
    opp_of = {}  # (team, week) -> opponent
    for g in sched:
        w = int(g["week"])
        opp_of[(g["home_team"], w)] = g["away_team"]
        opp_of[(g["away_team"], w)] = g["home_team"]

    positions = load_json("positions.json", {})        # gsis -> {"pos":..,"wr":..,"team":..}  (Ourlads)
    overrides = load_json("position_overrides.json", {})  # gsis -> {"pos":..,"wr":..}

    def pos_for(g, nv_pos, side):
        o = {**positions.get(g, {}), **overrides.get(g, {})}
        p = o.get("pos")
        if side == "off":
            p = p if p in OFF_POS else NV_OFF_FALLBACK.get(nv_pos)
            return p, (o.get("wr") or "Outside") if p == "WR" else ""
        p = o.get("idp_pos") or p  # two-way players keep a separate defensive position
        p = p if p in IDP_POS else NV_IDP_FALLBACK.get(nv_pos)
        return p, ""

    def name_of(g, fallback=""):
        p = pinfo.get(g, {})
        return p.get("display_name") or fallback

    def latest_team(g):
        for w in reversed(weeks):
            if (g, w) in team_wk:
                return team_wk[(g, w)]
        return ""

    team_wk = {}
    out_players, out_weeks = {}, {str(w): [] for w in weeks}

    # ---- offense: one row per player-game with offensive snaps or any stat line
    st_by = {(r["player_id"], int(r["week"])): r for r in stats}
    off_keys = set()
    for (g, w), r in st_by.items():
        if w in weeks and NV_OFF_FALLBACK.get(r["position"]) or (positions.get(g, {}).get("pos") in OFF_POS and w in weeks):
            off_keys.add((g, w))
    for (g, w), s in snap.items():
        if w in weeks and num(s["offense_snaps"]) > 0 and NV_OFF_FALLBACK.get(s["position"]):
            off_keys.add((g, w))
    for g, w in sorted(off_keys, key=lambda k: (k[1], k[0])):
        r = st_by.get((g, w), {})
        s = snap.get((g, w), {})
        nv_pos = r.get("position") or s.get("position") or pinfo.get(g, {}).get("position", "")
        pos, wr = pos_for(g, nv_pos, "off")
        if pos not in OFF_POS:
            continue
        tm = r.get("team") or s.get("team") or ""
        team_wk[(g, w)] = tm
        opp = r.get("opponent_team") or s.get("opponent") or opp_of.get((tm, w), "")
        pct = round(num(s.get("offense_pct")) * 100) if s else 0
        f = lambda k: num(r.get(k))
        two = f("passing_2pt_conversions") + f("rushing_2pt_conversions") + f("receiving_2pt_conversions")
        fl = f("fumbles_lost_total")
        fum = f("fumbles_total")
        out_weeks[str(w)].append(["o" + g, opp, pct,
            f("completions"), f("attempts"), f("passing_yards"), f("passing_tds"), f("passing_interceptions"),
            f("sacks_suffered"), f("passing_first_downs"), two,
            f("carries"), f("rushing_yards"), f("rushing_tds"), f("rushing_first_downs"),
            f("targets"), f("receptions"), f("receiving_yards"), f("receiving_tds"), f("receiving_first_downs"),
            fl, fum, f("special_teams_tds")])
        out_players["o" + g] = [name_of(g, r.get("player_display_name", "")), "", pos, wr]

    # ---- IDP: one row per player-game with defensive snaps or any defensive stat
    C, dteam = pbp_defense(pbp)
    idp_keys = {(g, w) for (g, w) in C if w in weeks}
    for (g, w), s in snap.items():
        if w in weeks and num(s["defense_snaps"]) > 0:
            idp_keys.add((g, w))
    for g, w in sorted(idp_keys, key=lambda k: (k[1], k[0])):
        s = snap.get((g, w), {})
        nv_pos = s.get("position") or (st_by.get((g, w)) or {}).get("position") or pinfo.get(g, {}).get("position", "")
        pos, _ = pos_for(g, nv_pos, "idp")
        if pos not in IDP_POS:
            continue
        c = C.get((g, w), collections.Counter())
        tm = s.get("team") or dteam.get((g, w)) or (st_by.get((g, w)) or {}).get("team", "")
        team_wk[(g, w)] = tm
        opp = s.get("opponent") or opp_of.get((tm, w), "")
        pct = round(num(s.get("defense_pct")) * 100) if s else 0
        out_weeks[str(w)].append(["d" + g, opp, pct, c["solo"], c["ast"], c["tfl"], c["sack"], c["qbh"], c["pd"],
                                  c["int"], c["ff"], c["fr"], c["dtd"], c["sfty"], c["blk"]])
        out_players["d" + g] = [name_of(g, s.get("player", "")), "", pos, ""]

    for k, p in out_players.items():
        p[1] = latest_team(k[1:])

    updated = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-4))).strftime("%b %-d, %Y")
    combined = {"updated": updated, "through": through, "players": out_players, "weeks": out_weeks}
    offense = {"updated": updated, "through": through,
               "players": {k: v for k, v in out_players.items() if k[0] == "o"},
               "weeks": {w: [r for r in rs if r[0][0] == "o"] for w, rs in out_weeks.items()}}

    st_only = collections.defaultdict(list)
    for (g, w), s in snap.items():
        if w in weeks and num(s["defense_snaps"]) == 0 and num(s["offense_snaps"]) == 0 and num(s["st_snaps"]) > 0:
            st_only[w].append({**s, "_gsis": g})

    # IDP tracker format: Ourlads-style detailed position (DE/DT/OLB/ILB/CB/SCB/FS/SS)
    idp_weeks = {}
    for w, rs in out_weeks.items():
        lst = []
        for r in rs:
            if r[0][0] != "d":
                continue
            g = r[0][1:]
            o = {**positions.get(g, {}), **overrides.get(g, {})}
            detail = o.get("detail") or {"IDL": "DT", "EDGE": "DE", "LB": "ILB", "CB": "CB", "S": "SS"}[out_players[r[0]][2]]
            tm = team_wk[(g, int(w))]
            lst.append({"name": o.get("idp_name") or out_players[r[0]][0], "team": "LAR" if tm == "LA" else tm, "position": detail,
                        "solo": r[3], "ast": r[4], "sack": r[6], "tfl": r[5], "qh": r[7], "int": r[9], "pd": r[8],
                        "ff": r[10], "fr": r[11], "td": r[12], "sfty": r[13], "blk": r[14]})
        # the IDP trackers also list players who only played special teams that week (a 0-point game)
        have = {r[0] for r in rs if r[0][0] == "d"}
        for s in st_only.get(int(w), []):
            g = s["_gsis"]
            if "d" + g in have:
                continue
            o = {**positions.get(g, {}), **overrides.get(g, {})}
            grp, _ = pos_for(g, s["position"], "idp")
            if grp not in IDP_POS:
                continue
            detail = o.get("detail") or {"IDL": "DT", "EDGE": "DE", "LB": "ILB", "CB": "CB", "S": "SS"}[grp]
            lst.append({"name": o.get("idp_name") or name_of(g, s["player"]), "team": "LAR" if s["team"] == "LA" else s["team"],
                        "position": detail, **{k: 0 for k in ("solo", "ast", "sack", "tfl", "qh", "int", "pd", "ff", "fr", "td", "sfty", "blk")}})
        idp_weeks[w] = lst
    idp = {"updated": updated, "through": through, "weeks": idp_weeks}

    os.makedirs(DATA, exist_ok=True)
    for name, var, obj in (("combined", "FIF_COMBINED", combined), ("offense", "FIF_OFFENSE", offense), ("idp", "FIF_IDP", idp)):
        s = json.dumps(obj, separators=(",", ":"))
        open(os.path.join(DATA, name + ".json"), "w").write(s)
        # .js version: the trackers load this with a plain <script> tag
        open(os.path.join(DATA, name + ".js"), "w").write(f"window.{var}={s};\n")
    print(f"Built weeks 1-{through}: {sum(k[0]=='o' for k in out_players)} offense, "
          f"{sum(k[0]=='d' for k in out_players)} IDP players")


if __name__ == "__main__":
    main()
