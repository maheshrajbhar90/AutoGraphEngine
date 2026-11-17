#!/usr/bin/env python3
"""
Update the Market Intelligence JSON from an exported "Early Warning System" PDF.

Usage:
    python update_market_json.py report.pdf market.json                # writes market_updated.json
    python update_market_json.py report.pdf market.json -o out.json
    python update_market_json.py report.pdf market.json --inplace      # overwrites, keeps market.json.bak
    python update_market_json.py report.pdf market.json --debug        # prints the text read from each region

Install:
    pip install pdfplumber
    # Needed when the PDF is made of screenshots (no selectable text), as the dashboard export is:
    pip install pytesseract pillow     # plus the Tesseract OCR binary (apt install tesseract-ocr)

How it works:
    The dashboard has a fixed layout, so each card is read from a fixed region of the page
    (fractions of a 1308x924 page). If the PDF has a text layer it is used directly; otherwise
    each region is OCR'd (inverted + upscaled, because the UI is dark). Text is parsed with
    tolerant regexes and cross-checked (buy - sell = net, probabilities sum to 100, 1M RS in the
    stage board = % change in the leaders list, ...). Anything not found is left unchanged in
    the JSON and listed at the end; suspicious values are listed under "Check these".

    OCR can still misread digits or drop a minus sign, so skim the "Check these" list and the
    diff before using the output.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

W, H = 1308.0, 924.0


def box(x0, x1, y0=0.0, y1=H):
    return (x0 / W, x1 / W, y0 / H, y1 / H)


# (x0, x1, y0, y1) as fractions of page width/height
REGIONS = {
    "full": (0, 1, 0, 1),
    "left": box(35, 274),
    "right": box(1033, 1272),
    "stage1": box(303, 533),
    "stage2": box(539, 768),
    "stage3": box(774, 1004),
    "lead1": box(303, 533, 70, 165),
    "lead2": box(541, 767, 70, 165),
    "lead3": box(779, 1005, 70, 165),
    "rad1": box(51, 651),
    "rad2": box(657, 1256),
    "kpi1": box(35, 233, 578, 690),
    "kpi2": box(242, 441, 578, 690),
    "kpi3": box(450, 649, 578, 690),
    "kpi4": box(658, 857, 578, 690),
    "kpi5": box(866, 1065, 578, 690),
    "kpi6": box(1074, 1272, 578, 690),
    "pcr_val": box(46, 135, 286, 314),  # the big "0.77" number on the Options Sentiment tile
}

_TRANS = str.maketrans({"−": "-", "–": "-", "—": "-"})
NUM = r"[+-]?\d+(?:\s?\.\s?\d+)?"  # tolerant of OCR putting a space around the decimal point
SCORE_RE = re.compile(r"(\d(?:\s?\d)?)\s*[/,|]?\s*1[0O6]0\b")  # "67/100", "34, 160", "6 7 /160"


def clean(t):
    t = (t or "").translate(_TRANS)
    return re.sub(r"(?<=[\d.+\-\s])@(?=[.\d%])", "0", t)  # OCR reads a zero as "@" ("-@.4%", "@% distributing")


def variants(digits):
    """Plausible readings of an amount: as read, or without a leading digit that is really a misread '₹'."""
    out = [int(digits.replace(",", ""))]
    if len(digits) >= 4 and digits[0] in "72":
        out.append(int(digits[1:].replace(",", "")))
    return out


GRADE_BANDS = [(75, "A"), (65, "B+"), (55, "B")]  # inferred from the dashboard; 72-75 is ambiguous


def grade_for(score, ocr_grade):
    if 72 <= score < 75:
        return ocr_grade
    for lo, g in GRADE_BANDS:
        if score >= lo:
            return g
    return ocr_grade


def pct(s):
    """'+0. 8' -> 0.8, '+01' -> 0.1 (OCR lost the point), '-6.2' -> -6.2"""
    s = re.sub(r"\s+", "", s)
    m = re.fullmatch(r"([+-]?)0(\d)", s)
    if m:
        s = f"{m.group(1)}0.{m.group(2)}"
    return float(s)


def money(s):
    return float(s.replace(",", ""))


def ago(s):
    s = re.sub(r"\s+", "", s)
    if s.lower() == "justnow":
        return "just now"
    s = re.sub(r"^[Il|]", "1", s)  # OCR reads "1m" as "Im"
    m = re.fullmatch(r"(\d+)([mhd])ago", s)
    return f"{m.group(1)}{m.group(2)} ago" if m else None


class Doc:
    def __init__(self, path, dpi=300):
        try:
            import pdfplumber
        except ImportError:
            sys.exit("Missing dependency: pip install pdfplumber")
        self.pdf = pdfplumber.open(path)
        self.cache = {}
        total = sum(len(p.extract_text() or "") for p in self.pdf.pages)
        self.ocr = total < 200
        self.images = []
        if self.ocr:
            try:
                import pytesseract  # noqa: F401
            except ImportError:
                sys.exit("PDF has no text layer. For OCR: pip install pytesseract pillow (and install Tesseract).")
            print("No text layer found - using OCR (this takes a minute)...")
            self.images = [p.to_image(resolution=dpi).original for p in self.pdf.pages]

    @staticmethod
    def _prep(img, scale=3):
        """Dark dashboards OCR badly: grayscale, invert to dark-on-light, boost contrast, upscale."""
        from PIL import ImageOps, ImageStat
        g = img.convert("L")
        if ImageStat.Stat(g).mean[0] < 128:
            g = ImageOps.invert(g)
        g = ImageOps.autocontrast(g, cutoff=1)
        return g.resize((g.width * scale, g.height * scale))

    def _crop_img(self, i, region):
        x0, x1, y0, y1 = REGIONS[region]
        img = self.images[i]
        w, h = img.size
        return img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))

    def _page_text(self, i, region):
        if self.ocr:
            import pytesseract
            return clean(pytesseract.image_to_string(self._prep(self._crop_img(i, region)), config="--psm 6"))
        x0, x1, y0, y1 = REGIONS[region]
        p = self.pdf.pages[i]
        return clean(p.crop((x0 * p.width, y0 * p.height, x1 * p.width, y1 * p.height)).extract_text())

    def text(self, region, pages=None):
        pages = list(range(len(self.pdf.pages))) if pages is None else list(pages)
        out = []
        for i in pages:
            key = (region, i)
            if key not in self.cache:
                self.cache[key] = self._page_text(i, region)
            out.append(self.cache[key])
        return "\n".join(out)

    def find_pages(self, needle, region="full"):
        return [i for i in range(len(self.pdf.pages)) if needle in self.text(region, [i])]

    def number_ocr(self, region, pages):
        """Digits-only OCR of one small value (used for tiles the normal OCR garbles)."""
        if not self.ocr:
            return clean(self.text(region, pages)).strip()
        import pytesseract
        for i in pages:
            t = pytesseract.image_to_string(
                self._prep(self._crop_img(i, region), 4),
                config="--psm 7 -c tessedit_char_whitelist=0123456789.",
            ).strip()
            if re.fullmatch(r"\d+\.\d+", t):
                return t
        return ""


def first(pattern, text, flags=0):
    return re.search(pattern, text, flags)


class Report:
    def __init__(self, known):
        self.changed, self.missing, self.warn = [], [], []
        self.known = set(known)

    def set(self, container, key, value, name):
        if value is None:
            self.missing.append(name)
            return
        old = container.get(key)
        if old != value:
            self.changed.append(f"{name}: {old!r} -> {value!r}")
        container[key] = value


def update_card1(d, data, rep):
    c = data["card1"]
    p0 = d.text("full", [0])
    m = first(r"\b(DEFENSIVE|CAUTIOUS|NEUTRAL|BULLISH|BEARISH|AGGRESSIVE|OFFENSIVE|BALANCED)\b", p0)
    rep.set(c, "bias", m.group(1) if m else None, "card1.bias")
    m = first(r"^\W*([A-Za-z][A-Za-z ]*?)\s*[·•.\-]\s*(\d+)\s*%\s*confidence", p0, re.M)
    rep.set(c, "regime", m.group(1).strip().upper() if m else None, "card1.regime")
    rep.set(c, "confidence", int(m.group(2)) if m else None, "card1.confidence")
    m = first(r"OVERALL MARKET HEALTH.*?" + SCORE_RE.pattern, p0, re.S)
    rep.set(c, "score", int(re.sub(r"\s", "", m.group(1))) if m else None, "card1.score")
    probs = {}
    for key, pat in [("bullish", r"(\d+(?:\.\d+)?)\s*%\s*bullish"),
                     ("sideways", r"(\d+(?:\.\d+)?)\s*%\s*sideways"),
                     ("bearish", r"(\d+(?:\.\d+)?)\s*%\s*bearish")]:
        m = first(pat, p0)
        probs[key] = float(m.group(1)) if m else None
        rep.set(c, key, probs[key], f"card1.{key}")
    if all(v is not None for v in probs.values()) and abs(sum(probs.values()) - 100) > 1:
        rep.warn.append(f"card1 bullish+sideways+bearish = {sum(probs.values())} (should be 100)")


def update_card2(d, data, rep):
    # each description is read from its own card's crop (full-page OCR interleaves the six cards)
    k = {i: d.text(f"kpi{i}", [0]) for i in range(1, 7)}
    descs = {}
    m = first(r"NIFTY50\s*\(([\d,.]+)\)\s*trading\s+(above|below)\s+its\s+200-?SMA\s*\(([\d,.]+)\)", k[1])
    if m:
        descs["TREND"] = f"NIFTY50 {round(money(m.group(1)))} {m.group(2)} 200-SMA {round(money(m.group(3)))}"
    m = first(r"(\d+)\s*%\s*of\s+(?:the\s+)?scanned\s+universe\s+in\s+Stage", k[2])
    if m:
        descs["BREADTH"] = f"{m.group(1)}% of scanned universe in Stage 1/2"
    m = first(r"(\d+)\s*%\s*of\s+universe\s+in\s+Stage\s+2\s+\(advancing\),\s*(\d+)\s*%\s*in\s+Stage\s+1", k[3])
    if m:
        descs["MOMENTUM"] = f"{m.group(1)}% in Stage 2 (advancing), {m.group(2)}% in Stage 1 (basing)"
    m = first(r"(\d+)\s*%\s*of\s+scanned\s+universe\s+shows", k[4])
    if m:
        descs["LIQUIDITY"] = f"{m.group(1)}% show accumulation signals (rising OBV + positive CMF)"
    m = first(r"Market\s+breadth\s+weak\s+at\s+(\d+)\s*%", k[5])
    if m:
        descs["RISK"] = f"Market breadth weak at {m.group(1)}% — narrow participation"
    m = first(r"Composite\s+PMI\s*\(([\d.]+)\)", k[6])
    if m:
        descs["MACRO"] = f"{'Strong' if float(m.group(1)) >= 50 else 'Weak'} India Composite PMI {m.group(1)}"

    for i, item in enumerate(data["card2"]["scores"], start=1):
        name = item["name"]
        m = SCORE_RE.search(d.text(f"kpi{i}", [0]))
        if m:
            score = float(re.sub(r"\s", "", m.group(1)))
            rep.set(item, "score", score, f"card2.{name}.score")
            item["color"] = "green" if score >= 50 else "red"
        else:
            rep.missing.append(f"card2.{name}.score")
        rep.set(item, "desc", descs.get(name), f"card2.{name}.desc")
    # the breadth percentage appears in both the score card and the BREADTH description
    br = next(s for s in data["card2"]["scores"] if s["name"] == "BREADTH")
    m = first(r"(\d+)%", br["desc"])
    if m and abs(float(m.group(1)) - br["score"]) > 3:
        rep.warn.append(f"BREADTH score {br['score']} vs description {m.group(1)}% differ - verify")


def update_card3(d, data, rep):
    t = d.text("left", d.find_pages("SECTOR PERFORMANCE", "left") or None)
    i = t.find("SECTOR PERFORMANCE")
    j = t.find("MARKET BREADTH", i)
    seg = t[i:j] if i >= 0 else t
    perf = {}
    for m in re.finditer(rf"\b([A-Z][A-Z_]+)\s+\S{{0,2}}?{NUM}\s*%\s+\S{{0,2}}?({NUM})\s*%", seg):
        if m.group(1) in rep.known:
            perf[m.group(1)] = pct(m.group(2))  # second column = 7-day
    if not perf:
        rep.missing.append("card3.sectors")
        return
    sectors = data["card3"]["sectors"]
    for row in sectors:
        if row[0] in perf:
            if row[1] != perf[row[0]]:
                rep.changed.append(f"card3.{row[0]}: {row[1]} -> {perf[row[0]]}")
            row[1] = perf[row[0]]
        else:
            rep.missing.append(f"card3.{row[0]}")


def update_card4(d, data, rep):
    c = data["card4"]
    t = d.text("right")
    m = re.search(r"F[Il1]{2}\s*/\s*D[Il1]{2}\s*FLOWS", t, re.I)
    j = t.find("BLOCK DEALS", m.end() if m else 0)
    seg = t[m.start():(j if j > 0 else None)] if m else ""
    labels = re.findall(r"\b([DF])[Il1]{2}\s+(BUY|SELL|NET)\b", seg, re.I)
    # amounts like "₹17524 Cr"; OCR often turns the rupee sign into a leading digit ("717524", "7-8435")
    toks = re.findall(r"[^\s\d\-]?\s*(?:\d(?=-))?(-?)(\d[\d,]*)\s*Cr", seg)
    if len(labels) == 6 and len(toks) == 6:
        got = {}
        for (who, kind), (neg, digits) in zip(labels, toks):
            got[("dii" if who.upper() == "D" else "fii", kind.lower())] = (-1 if neg else 1, digits)
        for who in ("fii", "dii"):
            (_, b), (_, s), (ns, n) = got[(who, "buy")], got[(who, "sell")], got[(who, "net")]
            best = None  # buy - sell must equal net; prefer the readings that need the fewest corrections
            for bi, bv in enumerate(variants(b)):
                for si, sv in enumerate(variants(s)):
                    for ni, nv in enumerate(variants(n)):
                        if abs(bv - sv - ns * nv) <= 1 and (best is None or bi + si + ni < best[0]):
                            best = (bi + si + ni, bv, sv, ns * nv)
            if best is None:
                rep.warn.append(f"card4.{who}: buy/sell/net from OCR are inconsistent ({b}, {s}, {n}) - not updated")
                continue
            if best[0]:
                rep.warn.append(f"card4.{who}: dropped a misread rupee-sign digit (buy {best[1]}, sell {best[2]}, net {best[3]})")
            for kind, v in (("buy", best[1]), ("sell", best[2]), ("net", best[3])):
                rep.set(c[who], kind, v, f"card4.{who}.{kind}")
    else:
        rep.missing.append("card4.fii/dii")
    m = first(r"As\s+of\s+(\d{2}-[A-Za-z]{3}-\d{4})", t)
    rep.set(c, "as_of", m.group(1) if m else None, "card4.as_of")
    left = d.text("left")
    m = first(r"(\d+)\s*%\s*accumu\w+", left) or first(r"accumulating\s+across\s+(\d+)\s*%", left)
    rep.set(c, "accumulation", int(m.group(1)) if m else None, "card4.accumulation")
    m = first(r"([0-9@OoQ]+)\s*%\s*distribut", left)
    rep.set(c, "distribution", int(re.sub(r"[@OoQ]", "0", m.group(1))) if m else None, "card4.distribution")


STAGE_RE = re.compile(
    r"(?:^|\n)(?:([A-Z][A-Z_]+)[ \t]*\n)?[ \t]*(NEUTRAL|WEAKNESS|STRENGTH|STRONG|LEADER|LAGGARD)[ \t]+"
    r"(HOLD|AVOID|BUY|ACCUMULATE|WATCH)[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*?"
    rf"3M\s+RS\s*({NUM})\s*%?\s*,?\s*[1IlI|]M\s+RS\s*({NUM})"
)


def update_card5(d, data, rep):
    found, anon = {}, []
    for r in ("stage1", "stage2", "stage3"):
        for m in STAGE_RE.finditer(d.text(r)):
            rec = {"m3": pct(m.group(4)), "m1": pct(m.group(5)), "class": m.group(2), "avoid": m.group(3) == "AVOID"}
            if m.group(1) in rep.known:
                found[m.group(1)] = rec
            else:
                anon.append(rec)  # name line was lost by OCR
    left = sorted(rep.known - set(found))
    for rec in anon:
        dup = any(f["m3"] == rec["m3"] and f["m1"] == rec["m1"] for f in found.values())
        if dup:
            continue
        if len(left) == 1:
            found[left[0]] = rec
            rep.warn.append(f"card5: sector name missing in OCR, assigned {left[0]} by elimination")
            left = []
        else:
            rep.warn.append(f"card5: a sector card (3M {rec['m3']}, 1M {rec['m1']}) has no readable name")
    if not found:
        rep.missing.append("card5.sectors")
        return
    order = [s["name"] for s in data["card5"]["sectors"]]
    names = [n for n in order if n in found] + [n for n in found if n not in order]
    new = [{"name": n, **found[n]} for n in names]
    if new != data["card5"]["sectors"]:
        rep.changed.append(f"card5.sectors: rebuilt with {len(new)} sectors")
    for n in order:
        if n not in found:
            rep.warn.append(f"card5: {n} was in the old JSON but not found in the PDF - dropped")
    data["card5"]["sectors"] = new


STOCK_RE = re.compile(
    r"\b([A-Z][A-Z0-9&\-]{2,})\s+Grade\s*(A\+|A|B\+|B|C)\b.{0,25}?([li]ow|medium|high)\s*risk\s+"
    r"(\d{2,3})\s*/?\s*1[0O6]0\s+Entry\s*\W?\s*(\d[\d,]*)\s*\W{0,3}\s*Stop\s*\W?\s*(\d[\d,]*)",
    re.S,
)


def pick_prices(entry_s, stop_s):
    """Choose (entry, stop) with a sane 0-15% gap, preferring readings with no digit dropped."""
    best = None
    for ei, e in enumerate(variants(entry_s)):
        for si, s in enumerate(variants(stop_s)):
            if 0 < (e - s) / e * 100 < 15 and (best is None or ei + si < best[0]):
                best = (ei + si, e, s)
    return best


def update_card6(d, data, rep):
    stocks = {}
    for r in ("rad1", "rad2"):
        for m in STOCK_RE.finditer(d.text(r)):
            name, score = m.group(1), float(m.group(4))
            prices = pick_prices(m.group(5), m.group(6))
            if not prices:
                rep.warn.append(f"card6.{name}: could not read entry/stop ({m.group(5)}, {m.group(6)}) - skipped")
                continue
            _, entry, stop = prices
            grade = grade_for(score, m.group(2))
            if grade != m.group(2):
                rep.warn.append(f"card6.{name}: OCR grade {m.group(2)} but score {score:.0f} implies {grade}; using {grade}")
            cat = {"iow": "low", "low": "low"}.get(m.group(3), m.group(3))
            stocks[name] = {
                "name": name,
                "score": score,
                "grade": grade,
                "risk_cat": f"{cat} risk",
                "entry": float(entry),
                "stop": float(stop),
                "risk": round((entry - stop) / entry * 100, 1),
            }
    if not stocks:
        rep.missing.append("card6.stocks")
        return
    new = sorted(stocks.values(), key=lambda s: -s["score"])
    rep.changed.append(f"card6.stocks: rebuilt with {len(new)} stocks")
    data["card6"]["stocks"] = new


def update_card7(d, data, rep, m1):
    pages = d.find_pages("CURRENT LEADERS")
    if not pages:
        rep.missing.append("card7")
        return
    skip = {"CURRENT", "EMERGING", "LEADERS", "WEAKENING"}
    for i, group in enumerate(data["card7"]["groups"], start=1):
        t = d.text(f"lead{i}", pages)
        pairs = []
        for m in re.finditer(rf"\b([A-Z][A-Z_]+)\s+({NUM})\s*%", t):
            n = m.group(1)
            if n in skip or n not in rep.known:
                continue
            v = pct(m.group(2))
            # the % change here equals the sector's 1M RS in the stage board; OCR often drops the minus sign
            if n in m1:
                if abs(abs(v) - abs(m1[n])) <= 0.15:
                    v = m1[n]
                else:
                    rep.warn.append(f"card7.{n}: leaders list says {v}, stage board 1M RS says {m1[n]}")
            pairs.append({"name": n, "change": v})
        if pairs:
            if pairs != group["sectors"]:
                rep.changed.append(f"card7.{group['label']}: {[p['name'] for p in pairs]}")
            group["sectors"] = pairs
        else:
            rep.missing.append(f"card7.{group['label']}")
    m = first(r"LAGGARDS\W*\S{0,3}\s*(just now|\d+\s*[mhd]\s*ago)", d.text("full", pages))
    rep.set(data["card7"], "as_of", ago(m.group(1)) if m else None, "card7.as_of")


def update_card8(d, data, rep):
    pages = d.find_pages("OPTIONS SENTIMENT", "left")
    left = d.text("left", pages or None)
    full = d.text("full")
    vix = data["card8"]["vix"]
    m = first(rf"VOLATILITY\s*([\d\s/.]{{1,12}}?)\s*({NUM})\s*%", left)
    value = None
    m2 = first(r"India\s+VIX\s+elevated\s+at\s+([\d.]+)", full)  # two decimals, better than the tile
    if m2:
        value = float(m2.group(1))
    elif m:
        value = float(re.sub(r"[\s/]+", ".", m.group(1).strip()).replace("..", "."))
    rep.set(vix, "value", value, "card8.vix.value")
    rep.set(vix, "change_pct", pct(m.group(2)) if m else None, "card8.vix.change_pct")
    m = first(r"Verified\s+(just now|\w{1,3}\s*ago)", left)
    a = ago(m.group(1)) if m else None
    rep.set(vix, "source", f"Verified {a} · yfinance (daily bar)" if a else None, "card8.vix.source")

    pcr = data["card8"]["pcr"]
    v = d.number_ocr("pcr_val", pages) if pages else ""
    rep.set(pcr, "value", float(v) if v else None, "card8.pcr.value")
    m = first(r"(bullish-leaning|bearish-leaning|bullish|bearish|neutral)", left[left.find("OPTIONS"):], re.I) \
        if "OPTIONS" in left else None
    rep.set(pcr, "label", m.group(1).lower() if m else None, "card8.pcr.label")
    m = first(r"spot\s*([\d.]+)\s*\W{1,3}\s*(\d{2}-[A-Za-z]{3}-\d{4})", left)
    rep.set(pcr, "basis", f"NIFTY PCR (OI) · spot {round(float(m.group(1)))} · {m.group(2)} expiry" if m else None,
            "card8.pcr.basis")


def update_header_and_hook(d, data, rep):
    m = first(r"Exported:\s*(\d{1,2})\s+([A-Za-z]{3})\w*\s+(\d{4})", d.text("full"))
    rep.set(data, "date", f"{int(m.group(1)):02d} {m.group(2).upper()} {m.group(3)}" if m else None, "date")
    fii = data["card4"]["fii"]["net"]
    breadth = next((s["score"] for s in data["card2"]["scores"] if s["name"] == "BREADTH"), None)
    if fii > 0 and breadth is not None:
        hook = (f"FIIs pumped ₹{int(fii):,} crore into the market, yet only {int(breadth)}% of NIFTY 50 stocks "
                f"show healthy participation. Institutional buying is rising, but the market remains "
                f"{data['card1']['bias'].lower()}. Here's where the opportunities are emerging.")
        rep.set(data["card1"], "hook", hook, "card1.hook")
    else:
        rep.missing.append("card1.hook (FII net not positive - write manually)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("json")
    ap.add_argument("-o", "--output")
    ap.add_argument("--inplace", action="store_true", help="overwrite the JSON (a .bak copy is kept)")
    ap.add_argument("--debug", action="store_true", help="print the text read from each region")
    ap.add_argument("--dpi", type=int, default=300, help="OCR render resolution")
    args = ap.parse_args()

    jpath = Path(args.json)
    data = json.loads(jpath.read_text(encoding="utf-8"))
    d = Doc(args.pdf, args.dpi)

    if args.debug:
        for name in REGIONS:
            print(f"\n===== {name} =====\n{d.text(name)}")

    known = {r[0] for r in data["card3"]["sectors"]} | {s["name"] for s in data["card5"]["sectors"]}
    known |= {s["name"] for g in data["card7"]["groups"] for s in g["sectors"]}
    rep = Report(known)

    update_card1(d, data, rep)
    update_card2(d, data, rep)
    update_card3(d, data, rep)
    update_card4(d, data, rep)
    update_card5(d, data, rep)
    update_card6(d, data, rep)
    update_card7(d, data, rep, {s["name"]: s["m1"] for s in data["card5"]["sectors"]})
    update_card8(d, data, rep)
    update_header_and_hook(d, data, rep)

    if args.inplace:
        shutil.copy(jpath, jpath.with_suffix(jpath.suffix + ".bak"))
        out = jpath
    else:
        out = Path(args.output) if args.output else jpath.with_name(jpath.stem + "_updated.json")
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\nWrote {out}")
    print(f"\nChanged ({len(rep.changed)}):")
    for c in rep.changed:
        print("  +", c)
    if rep.missing:
        print(f"\nNot found - left unchanged ({len(rep.missing)}):")
        for m in rep.missing:
            print("  !", m)
    if rep.warn:
        print(f"\nCheck these ({len(rep.warn)}):")
        for w in rep.warn:
            print("  ?", w)
    if rep.missing:
        print("\nTip: run with --debug to see the text read from each region and adjust REGIONS/regexes.")


if __name__ == "__main__":
    main()
