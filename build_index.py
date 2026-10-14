#!/usr/bin/env python3
"""Build index.html: verified claims grouped by question, held claims listed separately.

Plain Python, no AI. Reads questions.md, claims.json, sources.json and REPORT.md.
Runs verify.py first so REPORT.md is current. Never edits any data file.

    python3 build_index.py
"""
import html
import json
import re
from decimal import Decimal
from pathlib import Path

import verify

HERE = Path(__file__).resolve().parent

# Which question a claim answers, decided from words in its subject.
# First match wins, so the more specific rules come first.
RULES = [
    (1, ("vacancy", "occupancy")),
    (4, ("rent change", "rent growth", "year-over-year", "quarter-over-quarter")),
    (3, ("effective rent", "concession")),
    (2, ("asking rent", "rent per unit", "average rent")),
    (5, ("absorption",)),
    (6, ("deliver", "completion", "under construction")),
]


# For the side-by-side comparison: firms label the same area differently.
# Labels not listed here are compared only with the identical label.
SAME_AREA = {
    "San Francisco / SF Peninsula": "San Francisco",
    "San Francisco": "San Francisco",
    "Silicon Valley": "San Jose metro",
    "San Jose": "San Jose metro",
    "Oakland/East Bay": "Oakland / East Bay",
    "Oakland": "Oakland / East Bay",
}


def question_for(subject):
    s = subject.lower()
    for number, words in RULES:
        if any(w in s for w in words):
            return number
    return None


def load_questions():
    """Return {number: (short title, full text)} from questions.md."""
    text = (HERE / "questions.md").read_text(encoding="utf-8")
    part = text.split("# Questions", 1)[-1]
    questions, current = {}, None
    for line in part.splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)", line)
        if m:
            current = int(m.group(1))
            questions[current] = m.group(2).strip()
        elif current and line.strip():
            questions[current] += " " + line.strip()
    out = {}
    for n, full in questions.items():
        title, _, rest = full.partition(":")
        detail = rest.strip() or full
        out[n] = (title.strip(), detail[:1].upper() + detail[1:])
    return out


def load_report():
    """Return {claim_id: (status, reason)} from REPORT.md."""
    statuses = {}
    for line in (HERE / "REPORT.md").read_text(encoding="utf-8").splitlines():
        if not line.startswith("| c"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        status = "verified" if cells[6] == "verified" else "held"
        statuses[cells[0]] = (status, cells[7].replace("\\|", "|"))
    return statuses


def mark_value(quote, value):
    """Escape the quote and highlight each standalone appearance of the value."""
    target = verify.to_number(value)
    out, last = [], 0
    for m in re.finditer(verify.NUMBER, quote):
        if verify.to_number(m.group()) == target and target is not None:
            out.append(html.escape(quote[last:m.start()]))
            out.append(f"<mark>{html.escape(m.group())}</mark>")
            last = m.end()
    out.append(html.escape(quote[last:]))
    return "".join(out)


def review_notes(claim):
    """Plain, rule-based reasons a person should still look at a verified claim."""
    notes = []
    quote = claim.get("quote", "")
    count = len(verify.numbers_in(quote))
    if count > 1:
        notes.append(f"Quote has {count} numbers. Confirm the column or phrase this value comes from.")
    target = verify.to_number(claim.get("value", ""))
    hits = sum(1 for n in verify.numbers_in(quote) if n == target)
    if hits > 1:
        notes.append(f"The value appears {hits} times in the quote.")
    periods = set(re.findall(r"\b(?:Q[1-4]\s+)?20\d\d\b", quote))
    if len(periods) > 1:
        notes.append("Quote mentions more than one period.")
    subject = claim.get("subject", "").lower()
    if question_for(subject) == 2 and "asking" not in subject:
        notes.append("The source doesn't say whether this rent is asking or effective. "
                     "Listed under asking rent until confirmed.")
    return notes


def display_value(value, unit):
    """Show 2.4 + 'percent' as 2.4%, and 3,936 + 'dollars ...' as $3,936. Display only."""
    value, unit = str(value), str(unit)
    u = unit.lower()
    if u in ("percent", "%"):
        return f"{value}%", ""
    if u in ("basis points", "bps"):
        return f"{value} bps", ""
    if u.startswith("dollars"):
        sign = "-" if value.startswith("-") else ""
        return f"{sign}${value.lstrip('-$')}", unit[len("dollars"):].strip()
    return value, unit


def comparison_key(subject):
    """'Firm, Market, metric' -> (firm, area, metric, from_vacancy), or None."""
    parts = [p.strip() for p in subject.split(",", 2)]
    if len(parts) != 3:
        return None
    firm, market, metric = parts
    m = metric.lower()
    if "change" in m and ("vacancy" in m or "occupancy" in m):
        return None
    if "vacancy" in m:
        return firm, SAME_AREA.get(market, market), "Occupancy", True
    if "occupancy" in m:
        return firm, SAME_AREA.get(market, market), "Occupancy", False
    if "rent" in m and ("year-over-year" in m or "growth" in m):
        return firm, SAME_AREA.get(market, market), "Year-over-year rent change", False
    return None


def comparison_html(verified_claims):
    """Side-by-side table of percentages where two or more firms cover the same area."""
    table = {}
    for c in verified_claims:
        if str(c.get("unit", "")).lower() != "percent":
            continue
        key = comparison_key(c.get("subject", ""))
        value = verify.to_number(c.get("value", ""))
        if key is None or value is None:
            continue
        if str(c.get("value", "")).strip().startswith("-"):
            value = -value
        firm, area, metric, from_vacancy = key
        shown = Decimal(100) - value if from_vacancy else value
        table.setdefault(metric, {}).setdefault(area, {}).setdefault(firm, (shown, c, from_vacancy))

    rows_all, blocks = [], []
    for metric in ("Occupancy", "Year-over-year rent change"):
        areas = {a: f for a, f in table.get(metric, {}).items() if len(f) >= 2}
        if not areas:
            continue
        firms = sorted({firm for f in areas.values() for firm in f})
        rows = []
        for area, by_firm in areas.items():
            values = [v for v, _, _ in by_firm.values()]
            rows.append((max(values) - min(values), area, by_firm))
        rows.sort(key=lambda r: (r[0], r[1]))
        low, high = rows[0][0], rows[-1][0]
        body = []
        for spread, area, by_firm in rows:
            rows_all.append((spread, metric, area, by_firm))
            cells = []
            for firm in firms:
                if firm not in by_firm:
                    cells.append('<td class="na">—</td>')
                    continue
                v, c, conv = by_firm[firm]
                note = (f'<span class="sub">from {esc(c["value"])}% vacancy</span>' if conv else "")
                cells.append(f'<td><b>{v}%</b><span class="sub">{esc(c["claim_id"])}</span>{note}</td>')
            tag = ""
            if len(rows) > 1 and spread == low:
                tag = '<span class="tag close">closest</span>'
            elif len(rows) > 1 and spread == high:
                tag = '<span class="tag far">most different</span>'
            body.append(f"<tr><th>{esc(area)}</th>{''.join(cells)}"
                        f'<td class="spread">{spread} pts {tag}</td></tr>')
        head = "".join(f"<th>{esc(f)}</th>" for f in firms)
        blocks.append(f"""
    <h3 class="ctitle">{esc(metric)}</h3>
    <div class="tablewrap"><table class="cmp">
      <thead><tr><th>Area</th>{head}<th>Spread</th></tr></thead>
      <tbody>{''.join(body)}</tbody>
    </table></div>""")

    if not blocks:
        return '<p class="empty">No area has the same measure from two or more firms yet.</p>'
    rows_all.sort(key=lambda r: r[0])
    def pick(rows):
        return "".join(
            f"<li><b>{esc(area)}</b>, {esc(metric.lower())}: {r} pts apart "
            f"({', '.join(f'{esc(f)} {v}%' for f, (v, _, _) in sorted(by_firm.items()))})</li>"
            for r, metric, area, by_firm in rows)
    closest = [r for r in rows_all if r[0] == rows_all[0][0]] or rows_all[:1]
    if len(closest) < 3:
        closest = rows_all[:3]
    farthest = list(reversed(rows_all[-3:]))
    return f"""
    <div class="summary">
      <div><h4>Closest</h4><ul>{pick(closest)}</ul></div>
      <div><h4>Most different</h4><ul>{pick(farthest)}</ul></div>
    </div>
    {''.join(blocks)}
    <ul class="notes">
      <li>Spread is the highest value minus the lowest, in percentage points. Rows are sorted from closest to most different.</li>
      <li>CBRE reports stabilized vacancy. It's shown here as occupancy (100 minus vacancy) so it lines up with the others.</li>
      <li>Firms draw areas differently: CBRE's "Silicon Valley" sits next to Colliers' "San Jose", and "SF / Peninsula" next to "San Francisco".</li>
      <li>CBRE's rent change is for "rent per unit" and doesn't say asking or effective. The other firms report effective rent.</li>
      <li>Firms that buy data from the same provider can match closely without confirming each other.</li>
    </ul>"""


def esc(x):
    return html.escape(str(x))


def claim_card(c, source, status_reason=None):
    shown, unit_note = display_value(c.get('value', ''), c.get('unit', ''))
    src_title = esc(source.get("title", c.get("source_id", ""))) if source else esc(c.get("source_id", ""))
    url = source.get("url", "") if source else ""
    src_link = f'<a href="{esc(url)}">{src_title}</a>' if url else src_title
    notes = "" if status_reason else "".join(
        f"<li>{esc(n)}</li>" for n in review_notes(c))
    reason = (f'<p class="reason"><strong>Held:</strong> {esc(status_reason)}</p>'
              if status_reason else "")
    return f"""
    <article class="claim{' held' if status_reason else ''}">
      <div class="top">
        <div class="value">{esc(shown)}<span class="unit">{esc(unit_note)}</span></div>
        <div class="meta"><span class="id">{esc(c.get('claim_id', ''))}</span> · {esc(c.get('period', ''))}</div>
      </div>
      <h3>{esc(c.get('subject', ''))}</h3>
      {reason}
      <p class="quote">{mark_value(c.get('quote', ''), c.get('value', ''))}</p>
      <p class="source">Source: {src_link}
        <span class="file">{esc(source.get('original', '')) if source else ''}</span></p>
      {f'<ul class="notes">{notes}</ul>' if notes else ''}
    </article>"""


def build():
    verify.run_checks()
    questions = load_questions()
    claims = verify.load(verify.CLAIMS)
    sources = {s["id"]: s for s in verify.load(verify.SOURCES)}
    report = load_report()

    verified, held = [], []
    for c in claims:
        status, reason = report.get(c.get("claim_id"), ("held", "not in REPORT.md"))
        (verified if status == "verified" else held).append((c, reason))

    groups = {n: [] for n in questions}
    other = []
    for c, _ in verified:
        n = question_for(c.get("subject", ""))
        (groups[n] if n in groups else other).append(c)

    sections = []
    for n, (title, detail) in questions.items():
        cards = "".join(claim_card(c, sources.get(c["source_id"])) for c in groups[n])
        body = cards or '<p class="empty">No verified claims answer this yet.</p>'
        if n == 7:
            body = comparison_html([c for c, _ in verified])
        sections.append(f"""
  <section>
    <h2><span class="qn">Q{n}</span> {esc(title)}</h2>
    <p class="qtext">{esc(detail)}</p>
    <div class="{'compare' if n == 7 else 'grid'}">{body}</div>
  </section>""")
    if other:
        cards = "".join(claim_card(c, sources.get(c["source_id"])) for c in other)
        sections.append(f"""
  <section>
    <h2>Other verified claims</h2>
    <p class="qtext">These didn't match a question by their subject.</p>
    <div class="grid">{cards}</div>
  </section>""")

    held_html = ("".join(claim_card(c, sources.get(c.get("source_id")), r) for c, r in held)
                 or '<p class="empty">No held claims. Every claim passed the checker.</p>')

    source_rows = "".join(
        f"<li><strong>{esc(s['title'])}</strong>: <code>{esc(s['original'])}</code>, "
        f"text by {esc(s['extractor'])}, added {esc(s['added_on'])}, "
        f"fingerprint <code>{esc(s['sha256'][:16])}…</code></li>"
        for s in sources.values())

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multifamily Market Claims</title>
<style>
:root {{
  --bg: #f7f7f5; --card: #ffffff; --ink: #1d1f21; --muted: #5f6368; --line: #dfe1e5;
  --accent: #0b6e4f; --mark: #fff1a8; --held: #b3261e; --held-bg: #fdf0ef; --code: #f1f3f4;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #15171a; --card: #1e2125; --ink: #e8eaed; --muted: #9aa0a6; --line: #33373c;
    --accent: #5fd3a6; --mark: #6b5a00; --held: #f28b82; --held-bg: #2b1c1b; --code: #26292e;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #15171a; --card: #1e2125; --ink: #e8eaed; --muted: #9aa0a6; --line: #33373c;
  --accent: #5fd3a6; --mark: #6b5a00; --held: #f28b82; --held-bg: #2b1c1b; --code: #26292e;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 32px 16px 64px; }}
header h1 {{ margin: 0 0 4px; font-size: 26px; }}
header p {{ margin: 4px 0; color: var(--muted); }}
.counts {{ display: flex; gap: 12px; margin: 16px 0 8px; flex-wrap: wrap; }}
.count {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px 14px; }}
.count b {{ font-size: 20px; margin-right: 6px; }}
.count.held b {{ color: var(--held); }}
.count.ok b {{ color: var(--accent); }}
section {{ margin-top: 36px; }}
h2 {{ font-size: 19px; margin: 0 0 4px; }}
.qn {{ color: var(--accent); margin-right: 4px; }}
.qtext {{ color: var(--muted); margin: 0 0 14px; max-width: 75ch; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
.claim {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; min-width: 0; }}
.claim.held {{ border-color: var(--held); background: var(--held-bg); }}
.top {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }}
.value {{ font-size: 26px; font-weight: 650; font-variant-numeric: tabular-nums; }}
.unit {{ display: block; font-size: 13px; font-weight: 400; color: var(--muted); }}
.meta {{ color: var(--muted); font-size: 13px; white-space: nowrap; }}
.id {{ font-family: ui-monospace, Menlo, monospace; }}
h3 {{ font-size: 14px; font-weight: 600; margin: 6px 0 10px; }}
.quote {{ background: var(--code); border-radius: 6px; padding: 8px 10px; margin: 0 0 8px;
  font: 12px/1.5 ui-monospace, Menlo, monospace; white-space: normal; overflow-wrap: anywhere; word-spacing: 0.4em; }}
mark {{ background: var(--mark); color: inherit; border-radius: 2px; padding: 0 1px; }}
.source {{ font-size: 13px; color: var(--muted); margin: 0; }}
.source a {{ color: var(--accent); }}
.file {{ display: block; font-family: ui-monospace, Menlo, monospace; font-size: 12px; }}
.notes {{ font-size: 13px; color: var(--muted); margin: 8px 0 0; padding-left: 18px; }}
.reason {{ color: var(--held); font-size: 14px; margin: 0 0 8px; }}
.empty {{ color: var(--muted); font-style: italic; }}
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; margin-bottom: 8px; }}
.summary > div {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; }}
.summary h4 {{ margin: 0 0 6px; font-size: 14px; }}
.summary ul {{ margin: 0; padding-left: 18px; font-size: 14px; }}
.ctitle {{ font-size: 15px; margin: 22px 0 8px; }}
.tablewrap {{ overflow-x: auto; background: var(--card); border: 1px solid var(--line); border-radius: 10px; }}
table.cmp {{ border-collapse: collapse; width: 100%; font-size: 14px; font-variant-numeric: tabular-nums; }}
.cmp th, .cmp td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: top; }}
.cmp thead th {{ font-size: 12px; color: var(--muted); font-weight: 600; }}
.cmp tbody tr:last-child th, .cmp tbody tr:last-child td {{ border-bottom: 0; }}
.cmp tbody th {{ font-weight: 600; min-width: 8em; }}
@media (max-width: 600px) {{ .cmp th, .cmp td {{ padding: 6px 8px; font-size: 13px; }} }}
.cmp .sub {{ display: block; font-size: 11px; color: var(--muted); }}
.cmp .na {{ color: var(--muted); }}
.cmp .spread {{ white-space: nowrap; font-weight: 600; }}
.tag {{ display: inline-block; font-size: 11px; font-weight: 600; border-radius: 999px; padding: 1px 8px; margin-left: 6px; }}
.tag.close {{ background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--accent); }}
.tag.far {{ background: color-mix(in srgb, var(--held) 15%, transparent); color: var(--held); }}
footer {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }}
footer code {{ background: var(--code); padding: 0 4px; border-radius: 4px; }}
</style>
</head>
<body>
<main>
<header>
  <h1>Northern California multifamily market claims</h1>
  <p>Each number below is quoted from a saved source and checked by <code>verify.py</code>.
     Verified means the source is unchanged, the quote is in it word for word, and the number is in the quote.
     It doesn't mean the number is the right fact, so check the notes under each claim.
     Quotes are shown with extra spaces condensed; the saved text keeps them.</p>
  <div class="counts">
    <div class="count ok"><b>{len(verified)}</b>verified</div>
    <div class="count held"><b>{len(held)}</b>held</div>
    <div class="count"><b>{len(sources)}</b>source{'s' if len(sources) != 1 else ''}</div>
  </div>
</header>
{''.join(sections)}
  <section>
    <h2>Held claims</h2>
    <p class="qtext">These failed the checker and aren't counted above.</p>
    <div class="grid">{held_html}</div>
  </section>
<footer>
  <p>Sources</p>
  <ul>{source_rows}</ul>
  <p>Built by <code>python3 build_index.py</code> from claims.json and REPORT.md. Don't edit by hand.</p>
</footer>
</main>
</body>
</html>
"""
    (HERE / "index.html").write_text(page, encoding="utf-8")
    print(f"Wrote index.html: {len(verified)} verified, {len(held)} held.")


if __name__ == "__main__":
    build()
