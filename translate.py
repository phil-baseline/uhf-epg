"""Build an XMLTV guide whose channel IDs are Mega's tvg-ids (mega_ids.txt).

Sources: epgshare01 CA/US/US-locals/US-sports/UK. Each Mega ID is matched, in order, by:
  1. alias      aliases.txt line "mega_id = source_id"
  2. exact      CTV.Winnipeg.HD.ca2 -> ctvwinnipeg.ca
  3. callsign   cbs2kcbs.us -> KCBS-DT.us_locals1
  4. prefix     cbcnews.ca -> CBC.News.Network.ca2 (shortest source name starting with the Mega name)
Usage: python3 translate.py out_dir [days]    |    python3 translate.py demo
"""
import copy
import gzip
import os
import re
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

BASE = "https://epgshare01.online/epgshare01/"
FEEDS = ["epg_ripper_CA2.xml.gz", "epg_ripper_US2.xml.gz", "epg_ripper_US_LOCALS1.xml.gz",
         "epg_ripper_US_SPORTS1.xml.gz", "epg_ripper_UK1.xml.gz"]
HERE = os.path.dirname(os.path.abspath(__file__))
NETWORK = re.compile(r"^(abc|cbs|nbc|nb|fox|cw|my|pbs|pb|metv)\d*")
CALLSIGN = re.compile(r"([kw][a-z]{2,3})(?:dt\d*|ld\d*|cd\d*|d\d|tv\d*|plus|\d+)?$")


def key(src):
    """Source id -> (name, cc). CTV.Winnipeg.HD.ca2 -> ('ctvwinnipeg', 'ca'); KCBS-DT.us_locals1 -> ('kcbsdt', 'us')"""
    m = re.match(r"^(.*)\.([a-z]{2})(?:_[a-z]+)?\d*$", src, re.I)
    if not m:
        return None, None
    name = re.sub(r"\b(HD|FHD|UHD|4K)\b", "", m[1], flags=re.I)
    return re.sub(r"[^a-z0-9+&]", "", name.lower()), m[2].lower()


VARIANT = {"en", "espanol", "espaol", "mundo", "plus", "west", "family", "kal", "latino", "deportes"}


def word_prefixes(src):
    """CBC.News.Network.HD.ca2 -> {'cbc': 'newsnetwork', 'cbcnews': 'network', ...} (prefix -> remainder tokens)"""
    m = re.match(r"^(.*)\.([a-z]{2})(?:_[a-z]+)?\d*$", src, re.I)
    toks = [t for t in re.split(r"[^a-z0-9+&]+", (m[1] if m else "").lower()) if t and t not in ("hd", "fhd", "uhd", "4k")]
    return {"".join(toks[:i]): toks[i:] for i in range(1, len(toks))}


def callsign_of_source(src):
    m = re.match(r"^([KW][A-Z]{2,3})(?:-[A-Z0-9]+)?\.us_locals", src)
    return m[1].lower() if m else None


def callsign_of_mega(name):
    if NETWORK.match(name) or re.fullmatch(r"[kw][a-z]{2,3}", name):
        m = CALLSIGN.search(name)
        return m[1] if m else None
    return None


def match(mega_ids, sources, counts, aliases):
    """sources: list of source ids. Returns {mega_id: (source_id, method)}."""
    rank = lambda s: counts.get(s, 0)
    exact, calls, by_cc = {}, {}, {}
    for s in sources:
        if not counts.get(s):
            continue
        n, cc = key(s)
        if not n:
            continue
        k = f"{n}.{cc}"
        if k not in exact or rank(s) > rank(exact[k]):
            exact[k] = s
        c = callsign_of_source(s)
        if c and (c not in calls or rank(s) > rank(calls[c])):
            calls[c] = s
        by_cc.setdefault(cc, []).append((word_prefixes(s), n, s))
    out = {}
    for mid in mega_ids:
        name, _, cc = mid.rpartition(".")
        if mid in aliases and aliases[mid] in counts:
            out[mid] = (aliases[mid], "alias")
        elif mid in exact:
            out[mid] = (exact[mid], "exact")
        elif cc == "us" and callsign_of_mega(name) in calls:
            out[mid] = (calls[callsign_of_mega(name)], "callsign")
        elif len(name) >= 4:
            # whole-word prefix only, and not a language/feed variant (History En Espanol, MeTV Plus, Showtime 2)
            cands = [(len(n), -rank(s), s) for wp, n, s in by_cc.get(cc, [])
                     if name in wp and not (set(wp[name]) & VARIANT or any(t.isdigit() for t in wp[name]))]
            if cands:
                out[mid] = (min(cands)[2], "prefix")
    return out


def parse_start(s):
    t = datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    m = re.search(r"([+-])(\d{2})(\d{2})$", s.strip())
    if m:
        d = timedelta(hours=int(m[2]), minutes=int(m[3]))
        t -= d if m[1] == "+" else -d
    return t


def scan_channels(paths, since, until):
    """Pass 1: channel elements + in-window listing counts per source id."""
    chans, counts = {}, {}
    for p in paths:
        for _, el in ET.iterparse(gzip.open(p), events=("end",)):
            if el.tag == "channel":
                chans[el.get("id")] = el
            elif el.tag == "programme":
                try:
                    ok = since <= parse_start(el.get("start", "")) <= until
                except ValueError:
                    ok = False
                if ok:
                    counts[el.get("channel")] = counts.get(el.get("channel"), 0) + 1
                el.clear()
    return chans, counts


def collect(paths, wanted, since, until):
    """Pass 2: keep listings only for wanted source ids."""
    progs = {}
    for p in paths:
        for _, el in ET.iterparse(gzip.open(p), events=("end",)):
            if el.tag != "programme":
                continue
            src = el.get("channel")
            try:
                ok = src in wanted and since <= parse_start(el.get("start", "")) <= until
            except ValueError:
                ok = False
            if ok:
                progs.setdefault(src, []).append(copy.deepcopy(el))
            el.clear()
    return progs


def build(paths, mega_ids, aliases, days):
    now = datetime.now(timezone.utc)
    since, until = now - timedelta(hours=6), now + timedelta(days=days)
    chans, counts = scan_channels(paths, since, until)
    m = match(mega_ids, list(chans), counts, aliases)
    progs = collect(paths, {s for s, _ in m.values()}, since, until)
    root = ET.Element("tv", {"generator-info-name": "uhf-epg (epgshare01 translated for Mega)"})
    plist = []
    for mid, (src, _) in sorted(m.items()):
        ch = copy.deepcopy(chans[src])
        ch.set("id", mid)
        root.append(ch)
        for p in progs.get(src, []):
            p = copy.copy(p)
            p.set("channel", mid)
            plist.append(p)
    root.extend(plist)
    return root, m


def load_list(name):
    path = os.path.join(HERE, name)
    return open(path).read().split() if os.path.exists(path) else []


def load_aliases():
    out = {}
    path = os.path.join(HERE, "aliases.txt")
    if os.path.exists(path):
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                a, b = (x.strip() for x in line.split("=", 1))
                out[a.lower()] = b
    return out


def demo():
    assert key("CTV.Winnipeg.HD.ca2") == ("ctvwinnipeg", "ca")
    assert key("TSN.2.HD.ca2") == ("tsn2", "ca")
    assert key("Sportsnet.(Pacific).ca2") == ("sportsnetpacific", "ca")
    assert key("KCBS-DT.us_locals1")[1] == "us"
    assert callsign_of_source("KCBS-DT.us_locals1") == "kcbs"
    for mega, cs in [("cbs2kcbs", "kcbs"), ("abcwabc", "wabc"), ("fox2ktvuplus", "ktvu"), ("cbs13wloxdt2", "wlox"),
                     ("nbc13kyma2", "kyma"), ("cbs4wbz", "wbz"), ("ktla", "ktla"), ("pbswliw21", "wliw")]:
        assert callsign_of_mega(mega) == cs, (mega, callsign_of_mega(mega))
    # ponytail: bare 4-letter names (wetv) also read as callsigns; exact match runs first so real networks win
    assert callsign_of_mega("tsn1") is None and callsign_of_mega("hgtv") is None
    assert match(["historychannel.us", "metv.us", "showtime.us", "starzencorewest.us"],
                 ["History.Channel.En.Español.us2", "MeTV.Plus.us2", "Showtime.2.HD.us2", "Starz.Encore.Westerns.SD.us2"],
                 {"History.Channel.En.Español.us2": 5, "MeTV.Plus.us2": 5, "Showtime.2.HD.us2": 5,
                  "Starz.Encore.Westerns.SD.us2": 5}, {}) == {}
    srcs = ["CTV.Winnipeg.HD.ca2", "KCBS-DT.us_locals1", "CBC.News.Network.ca2", "CBC.News.Network.HD.ca2",
            "Cable.Pulse.24.(CP24).HD.ca2", "Empty.ca2"]
    counts = {s: 10 for s in srcs[:-1]}
    counts["CBC.News.Network.HD.ca2"] = 20
    got = match(["ctvwinnipeg.ca", "cbs2kcbs.us", "cbcnews.ca", "cp24.ca", "empty.ca", "nothing.us"], srcs, counts,
                {"cp24.ca": "Cable.Pulse.24.(CP24).HD.ca2"})
    assert got == {"ctvwinnipeg.ca": ("CTV.Winnipeg.HD.ca2", "exact"), "cbs2kcbs.us": ("KCBS-DT.us_locals1", "callsign"),
                   "cbcnews.ca": ("CBC.News.Network.HD.ca2", "prefix"),
                   "cp24.ca": ("Cable.Pulse.24.(CP24).HD.ca2", "alias")}, got
    print("demo ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["demo"]:
        demo()
        sys.exit()
    out_dir, days = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3
    mega_ids = sorted(set(i.lower() for i in load_list("mega_ids.txt")))
    tmp = tempfile.mkdtemp()
    paths = []
    for f in FEEDS:
        p = os.path.join(tmp, f)
        req = urllib.request.Request(BASE + f, headers={"User-Agent": "uhf-epg"})
        with urllib.request.urlopen(req, timeout=300) as r, open(p, "wb") as w:
            w.write(r.read())
        paths.append(p)
    root, m = build(paths, mega_ids, load_aliases(), days)
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with gzip.open(os.path.join(out_dir, "guide.xml.gz"), "wb") as f:
        f.write(data)
    with open(os.path.join(out_dir, "guide.xml"), "wb") as f:
        f.write(data)
    by = {}
    for _, how in m.values():
        by[how] = by.get(how, 0) + 1
    miss = [i for i in mega_ids if i not in m]
    report = [f"matched {len(m)}/{len(mega_ids)} {by}", f"bytes {len(data)}", "unmatched:"] + miss
    report += ["", "matches:"] + [f"{k} <- {s} ({h})" for k, (s, h) in sorted(m.items())]
    open(os.path.join(out_dir, "report.txt"), "w").write("\n".join(report))
    print("\n".join(report[:2]))
