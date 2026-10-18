"""Rewrite epgshare01 CA/US XMLTV channel IDs into Mega's tvg-id style.

CTV.Winnipeg.HD.ca2 -> ctvwinnipeg.ca, so UHF matches Mega channels by tvg-id.
Usage: python3 translate.py out_dir [days]
"""
import gzip
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

FEEDS = [
    "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
]


def mega_id(src):
    """CTV.Winnipeg.HD.ca2 -> ctvwinnipeg.ca ; Sportsnet.(Pacific).ca2 -> sportsnetpacific.ca"""
    m = re.match(r"^(.*)\.([a-z]{2})\d*$", src, re.I)
    if not m:
        return None
    name, cc = m.groups()
    name = re.sub(r"\b(HD|FHD|UHD|4K)\b", "", name, flags=re.I)
    name = re.sub(r"[^a-z0-9+&]", "", name.lower())
    return f"{name}.{cc.lower()}" if name else None


def parse_start(s):
    return datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) - _offset(s)


def _offset(s):
    m = re.search(r"([+-])(\d{2})(\d{2})$", s.strip())
    if not m:
        return timedelta(0)
    d = timedelta(hours=int(m[2]), minutes=int(m[3]))
    return d if m[1] == "+" else -d


def translate(streams, days):
    """streams: iterable of binary XMLTV file objects. Returns (channels, programmes) elements."""
    now = datetime.now(timezone.utc)
    since, until = now - timedelta(hours=6), now + timedelta(days=days)
    chans, progs = {}, {}  # src id -> channel el ; src id -> [programme els]
    for f in streams:
        for _, el in ET.iterparse(f, events=("end",)):
            if el.tag == "channel" and mega_id(el.get("id", "")):
                chans[el.get("id")] = el
            elif el.tag == "programme" and el.get("channel") in chans:
                try:
                    start = parse_start(el.get("start", ""))
                except ValueError:
                    start = None
                if start and since <= start <= until:
                    progs.setdefault(el.get("channel"), []).append(el)
                else:
                    el.clear()  # free memory: dropped listings stay as empty nodes in the parse tree
    # ponytail: when several source channels collapse to one Mega ID, keep the one with most listings
    best = {}
    for src, items in progs.items():
        new = mega_id(src)
        if new not in best or len(items) > len(progs[best[new]]):
            best[new] = src
    out_c, out_p = [], []
    for new, src in best.items():
        chans[src].set("id", new)
        out_c.append(chans[src])
        for p in progs[src]:
            p.set("channel", new)
            out_p.append(p)
    return out_c, out_p


def write(out_dir, chans, progs):
    root = ET.Element("tv", {"generator-info-name": "uhf-epg (epgshare01 translated)"})
    root.extend(chans)
    root.extend(progs)
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with open(f"{out_dir}/guide.xml", "wb") as f:
        f.write(data)
    with gzip.open(f"{out_dir}/guide.xml.gz", "wb") as f:
        f.write(data)
    return len(chans), len(progs), len(data)


def demo():
    assert mega_id("CTV.Winnipeg.HD.ca2") == "ctvwinnipeg.ca"
    assert mega_id("Sportsnet.(Pacific).ca2") == "sportsnetpacific.ca"
    assert mega_id("TSN.2.HD.ca2") == "tsn2.ca"
    assert mega_id("ESPN.us2") == "espn.us"
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S +0000")
    xml = (f'<tv><channel id="TSN.2.ca2"><display-name>TSN2</display-name></channel>'
           f'<channel id="TSN.2.HD.ca2"><display-name>TSN2 HD</display-name></channel>'
           f'<programme channel="TSN.2.ca2" start="{now}" stop="{now}"><title>A</title></programme>'
           f'<programme channel="TSN.2.HD.ca2" start="{now}" stop="{now}"><title>B</title></programme>'
           f'<programme channel="TSN.2.HD.ca2" start="{now}" stop="{now}"><title>C</title></programme>'
           f'</tv>').encode()
    import io
    c, p = translate([io.BytesIO(xml)], 3)
    assert [e.get("id") for e in c] == ["tsn2.ca"], [e.get("id") for e in c]
    assert sorted(e.findtext("title") for e in p) == ["B", "C"]
    assert all(e.get("channel") == "tsn2.ca" for e in p)
    print("demo ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["demo"]:
        demo()
        sys.exit()
    out_dir, days = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3
    streams = []
    for url in FEEDS:
        req = urllib.request.Request(url, headers={"User-Agent": "uhf-epg"})
        streams.append(gzip.GzipFile(fileobj=urllib.request.urlopen(req, timeout=300)))
    print("channels %d, programmes %d, bytes %d" % write(out_dir, *translate(streams, days)))
