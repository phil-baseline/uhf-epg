# uhf-epg

Nightly XMLTV guide for UHF + Mega IPTV. Downloads epgshare01's CA2/US2 guides, rewrites channel IDs into Mega's tvg-id style (`CTV.Winnipeg.HD.ca2` → `ctvwinnipeg.ca`), keeps the next 3 days, publishes to GitHub Pages.

- Guide: `https://<user>.github.io/uhf-epg/guide.xml.gz` (plain `guide.xml` also published)
- Add it in the UHF web console under EPG sources.
- Run by hand: Actions → build-guide → Run workflow. Self-check: `python3 translate.py demo`.
