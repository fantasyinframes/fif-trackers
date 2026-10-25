"""Refresh data/positions.json from Ourlads depth charts (all 32 teams).

For each player on a team's offensive or defensive depth chart it records:
  pos     tracker position: QB / RB / WR / TE  or  IDL / EDGE / LB / CB / S
  wr      "Outside" (LWR/RWR) or "Slot" (SWR), WRs only
  detail  the IDP trackers' label: DE / DT / OLB / ILB / CB / SCB / FS / SS
  team    team on the depth chart

Scheme matters for the combined tracker: in a 3-4, DEs play inside (IDL) and OLBs
rush the passer (EDGE); in a 4-3, DEs are EDGE and every LB is LB.

Players not on any depth chart keep their last known entry. Manual fixes go in
data/position_overrides.json, which wins over this file.

Usage: python scripts/ourlads.py [--html-dir DIR]   (DIR: saved pages named TEAM.html, for testing)
"""
import argparse, csv, html, io, json, os, re, sys, time, unicodedata, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POS_FILE = os.path.join(ROOT, "data", "positions.json")
URL = "https://www.ourlads.com/nfldepthcharts/depthchart/{}"
ROSTER = "https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_{}.csv"
TEAMS = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
         "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"]
OURLADS_CODE = {"ARI": ["ARZ", "ARI"], "LA": ["LAR", "LA"]}  # tried in order

# IDP trackers' detailed labels (same table the trackers use)
DETAIL = {"DE": "DE", "LDE": "DE", "RDE": "DE", "DT": "DT", "LDT": "DT", "RDT": "DT", "NT": "DT",
          "OLB": "OLB", "LOLB": "OLB", "ROLB": "OLB", "RUSH": "OLB", "EDGE": "OLB", "JACK": "OLB", "ED": "OLB",
          "MLB": "ILB", "ILB": "ILB", "LB": "ILB", "WLB": "ILB", "SLB": "ILB", "LILB": "ILB", "RILB": "ILB",
          "CB": "CB", "LCB": "CB", "RCB": "CB", "SCB": "SCB", "NB": "SCB", "NCB": "SCB", "SLOT": "SCB",
          "FS": "FS", "SS": "SS", "S": "SS"}


def group(label, three_four):
    """Combined-tracker position for a defensive depth-chart label."""
    d = DETAIL.get(label)
    if label == "SLB" and three_four:  # 3-4 teams list a stand-up rusher at SLB (Thibodeaux, Nwosu)
        return "EDGE"
    if d is None:
        return None
    if d == "DT":
        return "IDL"
    if d == "DE":
        return "IDL" if three_four else "EDGE"
    if d == "OLB":
        return "EDGE" if three_four or label in ("RUSH", "EDGE", "JACK", "ED") else "LB"
    if d == "ILB":
        return "LB"
    if d in ("CB", "SCB"):
        return "CB"
    return "S"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s).replace("-", " ")
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return " ".join(s.split())


def cells(row):
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>.*?</t[dh]>", row, re.S)]


def parse_name(raw):
    """'Cook III, James 22/2' -> ('cook', 'james');  'ALLEN, JOSH 18/1' -> ('allen', 'josh')."""
    if "," not in raw:
        return None
    last, rest = raw.split(",", 1)
    first = rest.strip().split(" ")[0] if rest.strip() else ""
    return norm(last), norm(first)


def parse_team(page):
    """Return (three_four, [(label, jersey, raw_name, side)])."""
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())
    m = re.search(r"Defense\s+Base\s+([0-9]-[0-9](?:-[0-9])?)", text)
    scheme = m.group(1) if m else ""
    tables = re.findall(r"<table.*?</table>", page, re.S)
    if len(tables) < 2:
        raise ValueError("depth chart tables not found")
    out = []
    for side, tbl in (("off", tables[0]), ("def", tables[1])):
        for row in re.findall(r"<tr.*?</tr>", tbl, re.S):
            c = cells(row)
            if len(c) < 3 or c[0] in ("Pos", ""):
                continue
            for i in range(1, len(c) - 1, 2):
                if c[i + 1]:
                    out.append((c[0], c[i], c[i + 1], side))
    return scheme, out


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (fif-trackers weekly update)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=int(os.environ.get("SEASON", time.strftime("%Y"))))
    ap.add_argument("--html-dir", default="")
    ap.add_argument("--teams", default=",".join(TEAMS))
    a = ap.parse_args()

    raw = urllib.request.urlopen(urllib.request.Request(ROSTER.format(a.season), headers={"User-Agent": "fif-trackers"}), timeout=120).read()
    roster = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    by_team = {}
    for r in roster:
        if r.get("gsis_id"):
            by_team.setdefault(r["team"], []).append(r)

    def match(team, jersey, nm):
        last, first = nm
        cands = by_team.get(team, [])
        hits = [r for r in cands if norm(r["last_name"]) == last]
        if len(hits) > 1:
            hits = [r for r in hits if r.get("jersey_number") == jersey] or \
                   [r for r in hits if norm(r["first_name"]).startswith(first[:3]) or norm(r.get("football_name", "")).startswith(first[:3])]
        if not hits:  # last-name spelling differs: fall back to jersey + first name
            hits = [r for r in cands if r.get("jersey_number") == jersey and
                    (norm(r["first_name"])[:3] == first[:3] or norm(r.get("football_name", ""))[:3] == first[:3])]
        if not hits:  # recently moved / not on the nflverse team yet
            hits = [r for t, rs in by_team.items() for r in rs if norm(r["last_name"]) == last and
                    (norm(r["first_name"]) == first or norm(r.get("football_name", "")) == first)]
        return hits[0]["gsis_id"] if len(hits) == 1 else None

    positions = json.load(open(POS_FILE)) if os.path.exists(POS_FILE) else {}
    seen, unmatched, failed, changes = set(), [], [], []
    for team in a.teams.split(","):
        page = None
        for code in OURLADS_CODE.get(team, [team]):
            try:
                if a.html_dir:
                    p = os.path.join(a.html_dir, code + ".html")
                    if not os.path.exists(p):
                        continue
                    page = open(p, encoding="utf-8", errors="replace").read()
                else:
                    page = fetch(URL.format(code))
                    time.sleep(1)
                scheme, entries = parse_team(page)
                if not scheme:  # not a team depth chart page (e.g. the wrong team code)
                    raise ValueError("no defensive formation found")
                break
            except Exception as e:  # try the next code
                page = None
                err = str(e)
        if page is None:
            failed.append(team)
            continue
        three_four = scheme.startswith("3-4")
        for label, jersey, name, side in entries:
            nm = parse_name(name)
            if not nm:
                continue
            if side == "off":
                pos = {"QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "TE": "TE", "LWR": "WR", "RWR": "WR",
                       "SWR": "WR", "WR": "WR"}.get(label)
                if not pos:
                    continue
            else:
                pos = group(label, three_four)
                if not pos:
                    continue
            g = match(team, jersey, nm)
            if not g:
                unmatched.append(f"{team} {label} {name}")
                continue
            key = (g, side)
            if key in seen:  # first (highest) listing wins
                continue
            seen.add(key)
            e = positions.setdefault(g, {})
            before = dict(e)
            if side == "off":
                if e.get("pos") in ("IDL", "EDGE", "LB", "CB", "S"):  # two-way player: keep the defense separate
                    e["idp_pos"] = e["pos"]
                e["pos"] = pos
                if pos == "WR":
                    e["wr"] = "Slot" if label == "SWR" else "Outside"
                else:
                    e.pop("wr", None)
            else:
                if e.get("pos") in ("QB", "RB", "WR", "TE") and (g, "off") in seen:
                    e["idp_pos"] = pos
                else:
                    e["pos"] = pos
                e["detail"] = "OLB" if (label == "SLB" and three_four) else DETAIL[label]
            e["team"] = team
            if before and {k: before.get(k) for k in ("pos", "wr", "detail", "idp_pos")} != \
                    {k: e.get(k) for k in ("pos", "wr", "detail", "idp_pos")}:
                changes.append(f"{team} {name}: {before.get('pos')}/{before.get('detail') or before.get('wr') or ''}"
                               f" -> {e.get('pos')}/{e.get('detail') or e.get('wr') or ''}")

    if len(failed) > 4:
        sys.exit(f"Ourlads failed for {len(failed)} teams ({', '.join(failed)}); positions.json left unchanged.")
    json.dump(positions, open(POS_FILE, "w"), indent=0, sort_keys=True)
    print(f"Ourlads: {len(seen)} depth-chart spots matched, {len(changes)} position changes, "
          f"{len(unmatched)} unmatched, failed teams: {failed or 'none'}")
    for c in changes:
        print("  changed:", c)
    for u in unmatched:
        print("  unmatched:", u)


if __name__ == "__main__":
    main()
