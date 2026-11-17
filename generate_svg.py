"""Market Intelligence - animated SVG card generator (documentary-style edition).

Usage:  python generate_svgs.py [input.json] [output_dir]

Outputs six individual SVG cards, combined_six_card_animation.svg and
scene_timeline.json (scene start times + narration cue points).

Animation engine: every animation is a pure function of one clock `t`.
  * Standalone SVG in a browser  -> plays in real time.
  * window.__setTime(t)          -> deterministic seek (used by render_video.py / qa_check.py)
  * ?t=12.5 in the URL           -> freeze on a single frame
"""
import json, sys, html, math, re
from pathlib import Path
from xml.etree import ElementTree as ET

# ----------------------------------------------------------------------------
# Design tokens
# ----------------------------------------------------------------------------
GREEN, RED, AMBER, BLUE = "#10bd8b", "#f0444a", "#f5a623", "#438cff"
TXT, MUTE, DIM, FAINT = "#eef2f8", "#9ba5b4", "#8993a3", "#48515e"
PANEL, EDGE, TRACK, SLATE = "#11151d", "#242b36", "#202a36", "#4c5b70"
FONT = "Inter,Arial,Helvetica,sans-serif"

# Screening criteria shown as chips on the Opportunity Radar.  Override per run with
# card6.signals (list) or per stock with stocks[i].signals (list).
DEFAULT_SIGNALS = ["OBV accumulation", "Positive CMF", "Stage 2 breakout"]

HOLD = 4.0                                   # default seconds a finished scene stays on screen
INTRO = 0.9                                  # title slides in, then the first beat starts
GAP = 0.35                                   # default pause between two consecutive beats
SCENE_FLOORS = [11, 14, 18, 18, 20, 20]      # minimum scene lengths (as in the original)
REVEAL = 0.6                                 # duration of every slide/fade reveal


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def esc(v):
    return html.escape(str(v), quote=True)


def n2(x):
    return round(float(x), 2)


def fnum(v, default=None):
    """Finite float or default. Booleans / strings / None / NaN / inf -> default."""
    if isinstance(v, bool) or v is None:
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def safe_num(value, default=0):
    x = fnum(value)
    return default if x is None else x


def fmt_in(v, dec=0):
    """Indian digit grouping, sign dropped (callers add it)."""
    s = f"{abs(v):.{dec}f}"
    ip, _, fp = s.partition(".")
    if len(ip) > 3:
        head, tail, parts = ip[:-3], ip[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        ip = ",".join(parts + [tail])
    return ip + ("." + fp if fp else "")


def plain(v):
    v = fnum(v)
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}".rstrip("0").rstrip(".")


def signed(v, dec=1, suffix="%"):
    return "—" if v is None else f"{v:+.{dec}f}{suffix}"


def pretty(name):
    return re.sub(r"\s+", " ", str(name).replace("_", " ")).strip()


def fit_size(text, max_w, base, k=0.6, floor=14):
    """Largest font size <= base so that len(text)*size*k <= max_w."""
    n = max(1, len(str(text)))
    return max(floor, min(base, max_w / (n * k)))


_NARROW, _WIDE, _MID = set("I1 ,.:;'|!"), set("MW@%"), set("LJTFEZ")


def est_width(text, size):
    """Rough rendered width of bold Inter-style text (per-glyph classes; within ~5% of measured)."""
    w = 0.0
    for ch in str(text):
        if ch in " ,.:;'|!":
            w += 0.30
        elif ch in "I1":
            w += 0.34
        elif ch in _WIDE:
            w += 0.92
        elif ch in _MID:
            w += 0.62
        elif ch.isdigit():
            w += 0.64
        elif ch.islower():
            w += 0.60
        else:
            w += 0.72
    return w * size


def fit_width(text, max_w, base, floor=14):
    """Largest size <= base whose estimated width fits max_w (glyph-aware, unlike fit_size)."""
    return max(floor, min(base, base * max_w / max(1.0, est_width(text, base))))


def wrap_lines(value, max_chars, max_lines=2):
    """Word-wrap text for SVG cards without allowing it to spill horizontally."""
    words = str(value).split()
    lines, line = [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if len(candidate) <= max_chars or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        lines[-1] = last[:max(1, max_chars - 1)].rstrip() + "…"
    return lines


def t(x, y, s, size=26, fill=TXT, weight=500, anchor="start", cls="", extra=""):
    return (f'<text x="{n2(x)}" y="{n2(y)}" font-family="{FONT}" font-size="{n2(size)}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}" class="{cls}"{(" " + extra) if extra else ""}>{esc(s)}</text>')


def runs(x, y, parts, size=26, anchor="start", weight=500):
    """One text element with several coloured runs: parts = [(text, fill, weight|None)]."""
    spans = "".join(f'<tspan fill="{c}" font-weight="{w or weight}">{esc(s)}</tspan>' for s, c, w in parts)
    return (f'<text x="{n2(x)}" y="{n2(y)}" font-family="{FONT}" font-size="{n2(size)}" '
            f'text-anchor="{anchor}">{spans}</text>')


def r(x, y, w, h, fill=PANEL, stroke=EDGE, sw=2, rx=18, cls=""):
    return (f'<rect x="{n2(x)}" y="{n2(y)}" width="{n2(w)}" height="{n2(h)}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw}" class="{cls}"/>')


def grp(inner, cls, enter, extra=""):
    return f'<g class="{cls}" data-enter="{n2(enter)}"{(" " + extra) if extra else ""}>{inner}</g>'


def pill(x, y, label, color=GREEN, w=None):
    w = w or max(92, len(label) * 13 + 26)
    return (f'<rect x="{x}" y="{y - 25}" width="{w}" height="32" rx="16" fill="#111922" stroke="{color}" stroke-width="2"/>'
            + t(x + w / 2, y - 3, label, 17, color, 650, "middle"))


def chip(x, y, w, h, label, color, size, weight=650, sw=1.5, fill="#111922"):
    """Rounded tag with its label optically centred: the text baseline is placed so the CAPITAL-letter
    band (cap height ~0.727 em) is centred in the pill, independent of pill height or font size."""
    return (f'<rect x="{n2(x)}" y="{n2(y)}" width="{n2(w)}" height="{n2(h)}" rx="{n2(h / 2)}" fill="{fill}" '
            f'stroke="{color}" stroke-width="{sw}"/>'
            + t(x + w / 2, y + h / 2 + 0.364 * size, label, size, color, weight, "middle"))


def counter(x, y, target, prefix="", suffix="", dec=0, size=30, color=TXT, weight=700, anchor="start",
            delay=0, duration=1.5, pulse=False):
    """Counting number. Static text holds the FINAL value, so non-JS renderers show real data;
    the engine replays 0 -> value. Missing data renders an em dash."""
    v = fnum(target)
    head = (f'<text x="{n2(x)}" y="{n2(y)}" font-family="{FONT}" font-size="{n2(size)}" font-weight="{weight}" '
            f'fill="{color}" text-anchor="{anchor}"')
    if v is None:
        return head + ">—</text>"
    final = ("-" if v < 0 else "") + prefix + fmt_in(v, dec) + suffix
    pulse_attr = ' data-pulse="1"' if pulse else ''

    return (
        head
        + f' data-counter="1" data-target="{v}" data-prefix="{esc(prefix)}" data-suffix="{esc(suffix)}" '
        + f'data-decimals="{dec}" data-delay="{n2(delay)}" data-duration="{n2(duration)}"'
        + f'{pulse_attr}>{esc(final)}</text>'
    )



def bar(x, y, w_final, h, fill, delay, duration, rx=None, direction=1, x0=None):
    """Animated bar. direction=-1 grows leftwards from x0."""
    rx = h / 2 if rx is None else rx
    w_final = max(0.0, w_final)
    if direction == -1:
        x0 = x if x0 is None else x0
        xx, extra = x0 - w_final, f' data-bar-dir="-1" data-bar-x0="{n2(x0)}"'
    else:
        xx, extra = x, ""
    return (f'<rect x="{n2(xx)}" y="{n2(y)}" width="{n2(w_final)}" height="{n2(h)}" rx="{n2(rx)}" fill="{fill}" '
            f'data-bar="1" data-bar-target="{n2(w_final)}" data-delay="{n2(delay)}" data-duration="{n2(duration)}"{extra}/>')


def draw_line(d_path, color, width, delay, duration, opacity=1):
    return (f'<path d="{d_path}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
            f'opacity="{opacity}" pathLength="1" stroke-dasharray="1" data-draw="1" '
            f'data-delay="{n2(delay)}" data-duration="{n2(duration)}"/>')


def unavailable(label):
    return (grp(t(960, 540, "DATA UNAVAILABLE", 54, DIM, 750, "middle"), "slide-item", 0.3) +
            grp(t(960, 596, str(label), 26, FAINT, 600, "middle"), "fade-item", 0.6))


# ----------------------------------------------------------------------------
# Frame, header, document
# ----------------------------------------------------------------------------
def header(eyebrow, title, date, disclaimer=True, brand="MARKET INTELLIGENCE"):
    out = [
        '<rect width="1920" height="1080" fill="#303030"/>',
        '<rect x="88" y="46" width="1744" height="988" rx="30" fill="#080a0d"/>',
        '<rect x="88" y="46" width="1744" height="988" rx="30" fill="url(#gridPat)" opacity=".55"/>',
    ]
    if eyebrow:
        out.append(grp(t(176, 130, eyebrow, 24, BLUE, 750), "heading-slide", 0.05))
    if title:
        size = fit_size(title, 1100, 48, 0.64, 30)
        out.append(grp(t(176, 188, title, size, TXT, 750), "heading-slide", 0.18))
        out.append(draw_line("M176 204 H300", BLUE, 4, 0.5, 0.7))
    label = (brand + " · " + date) if date else brand
    out.append(t(1744, 132, label, 22, DIM, 500, "end"))
    if disclaimer:
        out.append(t(176, 990, "Educational & research purposes only. Not investment advice.", 17, FAINT, 500))
    return '<g class="hdr">' + "".join(out) + '</g>'


DEFS = (
    '<defs>'
    '<pattern id="gridPat" width="64" height="64" patternUnits="userSpaceOnUse">'
    '<path d="M64 0H0V64" fill="none" stroke="#141a23" stroke-width="1"/></pattern>'
    '</defs>'
)

STYLE = (
    '<style>text{dominant-baseline:auto}'
    '.gauge-track{fill:none;stroke:#202a36;stroke-width:16;stroke-linecap:round}'
    '.gauge-progress{fill:none;stroke-width:16;stroke-linecap:round}'
    '.bar-track{fill:#202a36}</style>'
)

# ----------------------------------------------------------------------------
# Animation engine (single clock; deterministic; shared by standalone + combined)
# ----------------------------------------------------------------------------
ENGINE = r'''
(function(){
"use strict";
var SCHEDULE=/*SCHEDULE*/null;
var TOTAL=/*TOTAL*/0;
var STOPS=/*STOPS*/[];
var ease=function(t){return t<.5?4*t*t*t:1-Math.pow(-2*t+2,3)/2;};
var outc=function(t){return 1-Math.pow(1-t,3);};
var cl=function(v,a,b){return Math.max(a,Math.min(b,v));};
var nf=function(v,d){return Math.abs(v).toLocaleString("en-IN",{minimumFractionDigits:d,maximumFractionDigits:d});};
var N=function(el,k,d){var v=parseFloat(el.dataset[k]);return isFinite(v)?v:d;};
var root=document.documentElement;
var scenes=Array.prototype.slice.call(document.querySelectorAll(".scene"));
var pat=document.getElementById("gridPat");
var dots=Array.prototype.slice.call(document.querySelectorAll(".pdot"));
var cache=new Map();
function Q(s,sel){return Array.prototype.slice.call(s.querySelectorAll(sel));}
function prep(s){
  var P=cache.get(s);
  if(!P){P={
    reveal:Q(s,".heading-slide,.slide-item,.anim-card,.anim-row,.anim-panel,.fade-item"),
    counters:Q(s,'[data-counter="1"]'),bars:Q(s,'[data-bar="1"]'),gauges:Q(s,"[data-gauge]"),
    types:Q(s,"[data-typewriter]"),draws:Q(s,"[data-draw]"),glows:Q(s,"[data-glow]"),
    returns:Q(s,"[data-return]"),moves:Q(s,"[data-move]")};
    P.returns.forEach(function(el){var v=Number(el.dataset["return"]);el.setAttribute("fill",v>0?"#10bd8b":v<0?"#f0444a":"#8993a3");});
    cache.set(s,P);}
  return P;
}
function apply(s,t){
  var P=prep(s);
  P.reveal.forEach(function(el){
    var e=outc(cl((t-N(el,"enter",0))/0.6,0,1));
    el.style.opacity=e;
    if(!el.classList.contains("fade-item"))el.style.transform="translateX("+(-28*(1-e))+"px)";
  });
  P.types.forEach(function(el){
    var full=el.dataset.fullText||"";
    var p=cl((t-N(el,"typeDelay",0))/N(el,"typeDuration",1.5),0,1);
    el.textContent=full.slice(0,Math.floor(full.length*ease(p)));
  });
  P.counters.forEach(function(el){
    var target=Number(el.dataset.target),dec=N(el,"decimals",0),delay=N(el,"delay",0),dur=N(el,"duration",1.4);
    var p=cl((t-delay)/dur,0,1);
    el.textContent=(target<0?"-":"")+(el.dataset.prefix||"")+nf(Math.abs(target)*ease(p),dec)+(el.dataset.suffix||"");
    if(el.dataset.pulse){
      var q=(t-delay-dur)/0.55;el.style.transformBox="fill-box";el.style.transformOrigin="50% 50%";
      el.style.transform=(q>0&&q<1)?"scale("+(1+0.06*Math.sin(q*Math.PI))+")":"";
    }
  });
  P.bars.forEach(function(el){
    var w=N(el,"barTarget",0)*ease(cl((t-N(el,"delay",0))/N(el,"duration",1.5),0,1));
    el.setAttribute("width",w);
    if(el.dataset.barDir==="-1")el.setAttribute("x",N(el,"barX0",0)-w);
  });
  P.moves.forEach(function(el){
    var p=ease(cl((t-N(el,"delay",0))/N(el,"duration",1.5),0,1));
    el.setAttribute("transform","translate("+(N(el,"move",0)*p)+",0)");
  });
  P.gauges.forEach(function(el){
    var sc=N(el,"gauge",0),len=N(el,"length",0);
    var p=ease(cl((t-N(el,"delay",0))/N(el,"duration",1.5),0,1));
    el.style.strokeDashoffset=String(len*(1-p*sc/100));
  });
  P.draws.forEach(function(el){
    el.style.strokeDashoffset=String(1-ease(cl((t-N(el,"delay",0))/N(el,"duration",0.8),0,1)));
  });
  P.glows.forEach(function(el){
    var g=t-N(el,"enter",0)-0.8;
    el.style.filter=g>0?"drop-shadow(0 0 "+(2+5*(0.5+0.5*Math.sin(t*1.85)))+"px "+el.dataset.glow+")":"";
  });
}
function show(t){
  if(pat)pat.setAttribute("patternTransform","translate("+((t*5)%64)+","+((t*2.5)%64)+")");
  if(!SCHEDULE){apply(root,t);return;}
  var cur=0;
  scenes.forEach(function(s,i){
    var sc=SCHEDULE[i],lt=t-sc.start;
    if(t>=sc.start)cur=i;
    if(lt<0||lt>=sc.dur+(i===scenes.length-1?1e9:0)){s.style.display="none";return;}
    s.style.display="inline";
    var fin=cl(lt/0.45,0,1),fout=(i===scenes.length-1)?1:cl((sc.dur-lt)/0.5,0,1);
    s.style.opacity=ease(Math.min(fin,fout));
    apply(s,lt);
  });
  dots.forEach(function(d,i){d.setAttribute("fill",i===cur?"#438cff":i<cur?"#4c5b70":"#243040");});
}
window.__setTime=show;window.__duration=TOTAL;window.__stops=STOPS;
var m=/[?&]t=([0-9.]+)/.exec(location.search);
if(m){show(parseFloat(m[1]));}
else if(window.__manual){show(0);}
else{
 var clock=0,paused=false,waiting=false,pend=null,step=/[?&]step=1/.test(location.search),last=null;
 function resume(){if(waiting){if(pend!==null&&pend>clock)clock=pend;waiting=false;pend=null;show(clock);}paused=false;}
 function go(i,play){var s=STOPS[i];clock=s[0];show(clock);
  if(play&&!step){paused=false;waiting=false;pend=null;}else{paused=true;waiting=true;pend=s[1];}}
 function next(){for(var i=0;i<STOPS.length;i++)if(STOPS[i][0]>clock+1e-3){go(i,true);return;}}
 function prev(){for(var i=STOPS.length-1;i>=0;i--)if(STOPS[i][0]<clock-0.4){go(i,false);return;}
  clock=0;show(0);paused=true;waiting=false;pend=null;}
 document.addEventListener("keydown",function(e){var k=e.key;
  if(k===" "||k==="Enter"){if(waiting||paused)resume();else paused=true;e.preventDefault();}
  else if(k==="ArrowRight"){if(waiting)resume();else next();}
  else if(k==="ArrowLeft")prev();
  else if(k==="s"||k==="S")step=!step;
  else if(k==="r"||k==="R"){clock=0;paused=false;waiting=false;pend=null;show(0);}
 });
 window.__ctl={state:function(){return {clock:clock,paused:paused,waiting:waiting,step:step};}};
 show(0);
 (function tick(now){
  if(last===null)last=now;var dt=Math.min(.1,(now-last)/1000);last=now;
  if(!paused&&clock<TOTAL){
   var nt=clock+dt;
   if(step)for(var i=0;i<STOPS.length;i++){var s=STOPS[i][0];
    if(s>clock+1e-6&&s<=nt+1e-6){nt=s;paused=true;waiting=true;pend=STOPS[i][1];break;}}
   clock=Math.min(nt,TOTAL);show(clock);
  }
  requestAnimationFrame(tick);
 })(performance.now());
}
})();
'''
RUNTIME = ENGINE            # kept for backwards compatibility with the original module layout
COMBINED_RUNTIME = ENGINE


def engine(schedule=None, total=0.0, stops=None):
    return (ENGINE.replace("/*SCHEDULE*/null", json.dumps(schedule) if schedule else "null")
                  .replace("/*TOTAL*/0", str(n2(total)))
                  .replace("/*STOPS*/[]", json.dumps(stops or [])))


def svg_doc(content, runtime=True, title="Market dashboard", total=0.0, stops=None):
    script = f'<script><![CDATA[{engine(None, total, stops)}]]></script>' if runtime else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" overflow="hidden" width="1920" height="1080" '
            f'viewBox="0 0 1920 1080" role="img" aria-label="{esc(title)}">\n{DEFS}\n{STYLE}\n{content}\n{script}\n</svg>')


# ----------------------------------------------------------------------------
# Card 1 - THE BIG PICTURE (bias, score, probabilities, hook)
# ----------------------------------------------------------------------------
NUM_RE = re.compile(r"(₹\s?[\d,]+(?:\.\d+)?(?:\s?(?:crore|lakh|Cr))?|\d[\d,]*(?:\.\d+)?%)")


def rich_line(x, y, line, size, weight=700):
    """Text line with every number / % / rupee amount highlighted in accent blue."""
    parts = []
    for i, piece in enumerate(NUM_RE.split(line)):
        if piece:
            parts.append((piece, BLUE if i % 2 else TXT, 800 if i % 2 else weight))
    return runs(x, y, parts, size)


def card1(d):
    c = d.get("card1") or {}
    if not c:
        return unavailable("MARKET BIAS")
    p = []
    index = c.get("index", "")
    p.append(grp(t(176, 250, "FINAL MARKET BIAS" + (f" · {index}" if index else ""), 25, DIM, 600),
                 "slide-item", 0.2, 'data-cue="Market bias headline"'))
    bias = c.get("bias") or "—"
    p.append(f'<text x="176" y="430" font-family="{FONT}" font-size="{n2(fit_width(bias, 1060, 174, 60))}" '
             f'font-weight="800" fill="#aebbd0" data-typewriter="1" data-full-text="{esc(bias)}" '
             f'data-type-delay="0.35" data-type-duration="1.25" data-cue="Bias word typed">{esc(bias)}</text>')
    p.append(draw_line("M176 452 H316", BLUE, 4, 1.6, 0.6))
    sub = [s for s in (c.get("regime"), (plain(c.get("confidence")) + "% confidence") if c.get("confidence") is not None else "") if s]
    if sub:
        p.append(grp(t(180, 495, " · ".join(sub), 35, MUTE, 500), "slide-item", 1.8))

    score = c.get("score")
    if score is not None:
        p.append(f'<text x="1320" y="260" font-family="{FONT}" font-size="24" font-weight="600" fill="{DIM}" '
                 f'data-typewriter="1" data-full-text="OVERALL MARKET HEALTH" data-type-delay="1.45" '
                 f'data-type-duration="1.2">OVERALL MARKET HEALTH</text>')
        nd = len(str(int(abs(score))))
        ssize = 174 if nd <= 2 else 150
        p.append(counter(1300, 430, score, "", "", 0, ssize, TXT, 800, delay=2.6, duration=2.0, pulse=True))
        p.append(grp(t(1300 + nd * .66 * ssize + 22, 430, "/100", 60, MUTE, 700), "slide-item", 2.5,
                     'data-cue="Health score counts up"'))
        p.append(f'<rect x="1300" y="466" width="444" height="10" rx="5" fill="{TRACK}"/>')
        p.append(bar(1300, 466, 444 * min(100, max(0, score)) / 100, 10, BLUE, 2.6, 2.0))

    keys = [("bullish", GREEN, "bullish"), ("sideways", "#aab4c4", "sideways"), ("bearish", RED, "bearish")]
    vals = [c.get(k) for k, _, _ in keys]
    if all(v is not None for v in vals) and sum(vals) > 0:
        x, y, w = 176, 600, 1568
        dec = 0 if all(float(v).is_integer() for v in vals) else 1
        p.append(f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="17" fill="#171c24"/>')
        cols = [GREEN, SLATE, RED]
        cur, seg = x, []
        for i, (v, col) in enumerate(zip(vals, cols)):
            bw = w * v / sum(vals)
            p.append(bar(cur, y, bw, 34, col, 4.8 + i * 0.8, 0.9, rx=14))
            seg.append((cur, bw))
            cur += bw
        p.append(counter(176, 682, vals[0], "", "% bullish", dec, 30, GREEN, 700, delay=4.8, duration=0.9))
        p.append(counter(960, 682, vals[1], "", "% sideways", dec, 30, "#aab4c4", 700, "middle", 5.6, 0.9))
        p.append(counter(1744, 682, vals[2], "", "% bearish", dec, 30, RED, 700, "end", 6.4, 0.9))
        top = max(range(3), key=lambda i: vals[i])
        sx, sw = seg[top]
        p.append(grp(r(sx, y - 7, sw, 48, "none", cols[top] if top != 1 else MUTE, 2.5, 22) +
                     t(sx + sw / 2, 580, "MOST LIKELY", 18, cols[top] if top != 1 else MUTE, 750, "middle"),
                     "fade-item", 7.0, 'data-cue="Dominant scenario highlighted"'))

    hook = c.get("hook", "")
    if hook:
        lines = wrap_lines(hook, 78, 3)
        p.append(grp("".join(rich_line(176, 800 + j * 46, ln, 32) for j, ln in enumerate(lines)),
                     "slide-item", 7.6, 'data-cue="Hook line"'))
    return "".join(p)


# ----------------------------------------------------------------------------
# Card 2 - HOW STRONG IS THE FOUNDATION? (six health gauges)
# ----------------------------------------------------------------------------
def card2(d):
    items = (d.get("card2") or {}).get("scores") or []
    if not items:
        return unavailable("MARKET HEALTH SCORES")
    n = len(items)
    cols = min(3, n)
    rows = math.ceil(n / cols)
    x0, y0, total_w, total_h = 120, 270, 1680, 685
    gap_x, gap_y = 32, 22
    cell_w = (total_w - (cols - 1) * gap_x) / cols
    cell_h = (total_h - (rows - 1) * gap_y) / rows
    color_map = {"red": RED, "amber": AMBER, "green": GREEN}
    p, rects = [], []

    for i, item in enumerate(items):
        col_idx, row_idx = i % cols, i // cols
        x = x0 + col_idx * (cell_w + gap_x)
        y = y0 + row_idx * (cell_h + gap_y)
        delay = 0.8 + i * 0.9
        col = color_map.get(str(item.get("color", "")).lower(), DIM)
        rects.append((x, y, cell_w, cell_h, col))
        compact = cell_h < 245
        radius = min(72, max(42, cell_h * 0.25))
        cx, cy = x + radius + 48, y + cell_h / 2
        glen = 2 * math.pi * radius * 0.78
        sc = item.get("score")

        g = f'<g class="anim-card" data-enter="{n2(delay)}" data-cue="{esc(item["name"])} gauge">'
        g += r(x, y, cell_w, cell_h, PANEL, EDGE, 2, 20)
        g += (f'<circle cx="{n2(cx)}" cy="{n2(cy)}" r="{n2(radius)}" class="gauge-track" '
              f'stroke-dasharray="{n2(glen)} 490" transform="rotate(135 {n2(cx)} {n2(cy)})"/>')
        if sc is not None:
            g += (f'<circle cx="{n2(cx)}" cy="{n2(cy)}" r="{n2(radius)}" class="gauge-progress" stroke="{col}" '
                  f'stroke-dasharray="{n2(glen)} 490" stroke-dashoffset="{n2(glen * (1 - sc / 100))}" '
                  f'transform="rotate(135 {n2(cx)} {n2(cy)})" data-gauge="{sc}" data-delay="{n2(delay)}" '
                  f'data-duration="1.5" data-length="{n2(glen)}"/>')
        ssize = 43 if compact else 54
        g += counter(cx, cy + ssize * .32, sc, "", "", 0, ssize, col, 800, "middle", delay, 1.5)

        text_x = x + radius * 2 + 78
        text_w = max(100, x + cell_w - text_x - 22)
        nsize = 27 if compact else 33
        nlines = wrap_lines(item["name"], max(12, int(text_w / (nsize * .56))), 2)
        ny = y + cell_h * .38
        for j, line in enumerate(nlines):
            g += t(text_x, ny + j * (nsize + 3), line, nsize, TXT, 750)
        dsize = 17 if compact else 20
        dlines = wrap_lines(item.get("desc", ""), max(14, int(text_w / (dsize * .53))), 3)
        dy = ny + len(nlines) * (nsize + 3) + 16
        for j, line in enumerate(dlines):
            if dy + j * (dsize + 7) < y + cell_h - 12:
                g += t(text_x, dy + j * (dsize + 7), line, dsize, MUTE, 500)
        p.append(g + "</g>")

    # Insight strip: revealed after every gauge has filled
    t_sum = 0.8 + (n - 1) * 0.9 + 1.7
    scored = [(it["score"], it["name"], idx) for idx, it in enumerate(items) if it["score"] is not None]
    counts = {k: sum(1 for it in items if str(it.get("color", "")).lower() == k) for k in ("red", "amber", "green")}
    segs = [(f"{counts[k]} {k}", color_map[k]) for k in ("red", "amber", "green") if counts[k]]
    if segs:
        parts = []
        for j, (s, c_) in enumerate(segs):
            parts.append((("  ·  " if j else "") + s, c_, 750))
        tail = ""
        if len(scored) >= 2:
            hi, lo = max(scored), min(scored)
            tail = f"   —   strongest {pretty(hi[1])} {plain(hi[0])}  ·  weakest {pretty(lo[1])} {plain(lo[0])}"
        parts.append((tail, MUTE, 500))
        size = fit_size("".join(s for s, _, _ in parts), 1568, 26, 0.56, 18)
        p.append(grp(runs(176, 236, parts, size), "slide-item", t_sum, 'data-cue="Health summary"'))
    if len(scored) >= 3:
        hi, lo = max(scored), min(scored)
        if hi[2] != lo[2]:
            hl = "".join(r(rects[k][0] - 3, rects[k][1] - 3, rects[k][2] + 6, rects[k][3] + 6, "none", rects[k][4], 3, 23)
                         for k in (hi[2], lo[2]))
            p.append(grp(hl, "fade-item", t_sum))
    return "".join(p)


# ----------------------------------------------------------------------------
# Card 3 - SECTOR PERFORMANCE (two separate ranked charts: TODAY and LAST 7 DAYS)
# ----------------------------------------------------------------------------
GREEN_SOFT, RED_SOFT = "#0d7a5b", "#9b3037"      # muted tints (kept for optional secondary series)


def _tint(v, bright=True):
    if v is None or v == 0:
        return DIM
    return (GREEN if v > 0 else RED) if bright else (GREEN_SOFT if v > 0 else RED_SOFT)


def _pfx(v):
    return "+" if (v or 0) > 0 else ""


def _perf_panel(px, py, pw, ph, title, sub, rows, delay0):
    """One self-contained ranked chart. rows = [(name, value)], sorted here by its own value."""
    rows = sorted(((n, v) for n, v in rows if v is not None), key=lambda x: -x[1])
    n = len(rows)
    up = sum(1 for _, v in rows if v > 0)
    head = r(px, py, pw, ph, PANEL, EDGE, 2, 20)
    head += t(px + 32, py + 56, title, 34, TXT, 800) + t(px + 32, py + 88, sub, 18, DIM, 600)
    if rows:
        head += chip(px + pw - 32 - 150, py + 28, 150, 40, f"{up}/{n} UP", GREEN if up * 2 >= n else RED, 20, 750)
    p = [grp(head, "anim-panel", delay0, f'data-cue="{esc(title)} chart"')]
    if not rows:
        p.append(grp(t(px + pw / 2, py + ph / 2, "DATA UNAVAILABLE", 34, DIM, 750, "middle"), "fade-item", delay0 + .6))
        return p
    top, avail = py + 118, ph - 138
    row_h = avail / n
    label_x, bar_x, bar_w, val_x = px + 212, px + 232, 450, px + pw - 30
    vals = [v for _, v in rows]
    maxpos, maxneg = max([v for v in vals if v > 0], default=0), max([-v for v in vals if v < 0], default=0)
    scale = bar_w / ((maxpos + maxneg) or 1)
    zero_x = bar_x + maxneg * scale
    if maxneg > 0:
        p.append(grp(f'<path d="M{n2(zero_x)} {n2(top)} V{n2(top + avail)}" stroke="#3a4553" stroke-width="2"/>',
                     "fade-item", delay0 + .3))
    for i, (name, v) in enumerate(rows):
        yc = top + i * row_h + row_h / 2
        d_ = delay0 + .6 + i * .14
        ls = max(14, min(22, row_h * .52))
        bh = max(10, min(22, row_h * .52))
        nm = pretty(name)
        col = _tint(v)
        gg = f'<g class="anim-row" data-enter="{n2(d_)}">'
        gg += t(px + 30, yc + .364 * 15, str(i + 1), 15, FAINT, 700)
        gg += t(label_x, yc + ls * .36, nm, fit_width(nm, label_x - px - 62, ls, 12), TXT, 650, "end")
        gg += f'<rect x="{bar_x}" y="{n2(yc - bh / 2 - 3)}" width="{bar_w}" height="{n2(bh + 6)}" rx="8" fill="{TRACK}" opacity=".55"/>'
        gg += bar(zero_x, yc - bh / 2, abs(v) * scale, bh, col, d_, 1.0, rx=bh / 2,
                  direction=1 if v >= 0 else -1, x0=zero_x)
        gg += counter(val_x, yc + ls * .36, v, _pfx(v), "%", 1, ls + 1, col, 800, "end", d_, 1.0)
        p.append(gg + "</g>")
    return p


def card3(d):
    c = d.get("card3") or {}
    secs = c.get("sectors") or []
    if not secs:
        return unavailable("SECTOR PERFORMANCE")
    py, ph, pw = 222, 700, 820
    n = len(secs)
    p = _perf_panel(120, py, pw, ph, "TODAY", "1-day move by sector", [(s["name"], s["today"]) for s in secs], 0.6)
    t2 = 0.6 + 0.6 + n * .14 + 1.6
    p += _perf_panel(980, py, pw, ph, "LAST 7 DAYS", "1-week move by sector", [(s["name"], s["d7"]) for s in secs], t2)

    both = [(pretty(s["name"]), s["today"], s["d7"]) for s in secs if s["today"] is not None and s["d7"] is not None]
    fade = [nm for nm, a, b in both if a < 0 < b][:3]
    bounce = [nm for nm, a, b in both if b < 0 < a][:3]
    parts = []
    if fade:
        parts += [("Cooling today (up over 7D): ", MUTE, 500), (", ".join(fade), RED, 750)]
    if bounce:
        parts += [("      Bouncing today (down over 7D): ", MUTE, 500), (", ".join(bounce), GREEN, 750)]
    if parts:
        size = fit_size("".join(s for s, _, _ in parts), 1680, 24, .56, 15)
        p.append(grp(runs(120, 962, parts, size), "slide-item", t2 + .6 + n * .14 + 1.4, 'data-cue="Divergence takeaway"'))
    return "".join(p)


# ----------------------------------------------------------------------------
# Card 4 - WHERE IS INSTITUTIONAL MONEY GOING? (FII vs DII)
# ----------------------------------------------------------------------------
def flow_panel(name, vals, x, y, delay):
    g = f'<g class="anim-panel" data-enter="{n2(delay)}" data-cue="{name} flow">'
    g += r(x, y, 810, 440, PANEL, EDGE, 2, 20)
    g += t(x + 30, y + 62, name, 38, TXT, 750)
    if not vals or vals.get("net") is None:
        g += t(x + 405, y + 240, "DATA UNAVAILABLE", 34, DIM, 750, "middle")
        return g + "</g>"
    net = vals["net"]
    color = GREEN if net >= 0 else RED
    g += t(x + 30, y + 100, "NET FLOW · " + ("NET BUYER" if net >= 0 else "NET SELLER"), 22, DIM, 500)
    g += counter(x + 30, y + 195, net, "₹", " Cr", 0, 90, color, 800, delay=delay + .15, duration=2.0, pulse=True)
    ax, ay = x + 760, y + 150
    tri = (f"M{ax - 28} {ay + 25} L{ax + 28} {ay + 25} L{ax} {ay - 25} Z" if net >= 0
           else f"M{ax - 28} {ay - 25} L{ax + 28} {ay - 25} L{ax} {ay + 25} Z")
    g += f'<path d="{tri}" fill="{color}" opacity=".9"/>'
    pair = [(k, v) for k, v in (("buy", vals.get("buy")), ("sell", vals.get("sell"))) if v is not None]
    maxv = max([v for _, v in pair], default=0) or 1
    for j, (key, v) in enumerate(pair):
        label, col = ("Buy", GREEN) if key == "buy" else ("Sell", RED)
        yy = y + 278 + j * 93
        bd = delay + 2.2 + j * 1.1
        g += t(x + 30, yy, label, 26, TXT, 650)
        g += counter(x + 770, yy, v, "₹", " Cr", 0, 25, col, 700, "end", bd, 1.5)
        g += f'<rect x="{x + 30}" y="{yy + 15}" width="740" height="24" rx="12" fill="{TRACK}"/>'
        g += bar(x + 30, yy + 15, 740 * v / maxv, 24, col, bd, 1.5)
    return g + "</g>"


def flow_story(c):
    fii, dii = c.get("fii") or {}, c.get("dii") or {}
    fn, dn = fii.get("net"), dii.get("net")
    if fn is None or dn is None:
        return ""
    verb = lambda v: "bought" if v >= 0 else "sold"
    fa, da = fmt_in(abs(fn)), fmt_in(abs(dn))
    if (fn >= 0) != (dn >= 0):
        return f"FIIs {verb(fn)} ₹{fa} Cr while DIIs {verb(dn)} ₹{da} Cr — institutions are on opposite sides"
    return f"FIIs and DIIs both {verb(fn)}: ₹{fa} Cr and ₹{da} Cr net"


def card4(d):
    c = d.get("card4") or {}
    if not c:
        return unavailable("FII / DII FLOWS")
    p = [flow_panel("FII", c.get("fii"), 120, 222, 1.2), flow_panel("DII", c.get("dii"), 990, 222, 5.2)]
    story = flow_story(c)
    t_story = 9.4
    if story:
        p.append(grp(t(960, 722, story, fit_size(story, 1640, 30, .56, 20), TXT, 700, "middle"),
                     "slide-item", t_story, 'data-cue="FII vs DII contrast"'))
    acc, dist = c.get("accumulation"), c.get("distribution")
    t_bar = t_story + 1.0
    if acc is not None or dist is not None:
        p.append(grp(f'<rect x="120" y="790" width="1680" height="28" rx="14" fill="{TRACK}"/>', "fade-item", t_bar))
        parts = ""
        if acc is not None:
            parts += counter(120, 770, acc, "", "% accumulating", 0, 26, GREEN, 700, delay=t_bar, duration=1.8)
            p.append(bar(120, 790, 1680 * min(100, max(0, acc)) / 100, 28, GREEN, t_bar, 1.8))
        if dist is not None:
            parts += counter(1800, 770, dist, "", "% distributing", 0, 26, RED, 700, "end", t_bar, 1.8)
            if dist > 0:
                p.append(bar(1800, 790, 1680 * min(100, dist) / 100, 28, RED, t_bar, 1.8, direction=-1, x0=1800))
        p.append(grp(parts, "fade-item", t_bar, 'data-cue="Accumulation vs distribution"'))
    if c.get("as_of"):
        p.append(grp(t(120, 855, c["as_of"], 18, "#657080", 500), "fade-item", t_bar))
    return "".join(p)


# ----------------------------------------------------------------------------
# Card 5 - WHERE IS STRENGTH BUILDING? (sector stage board)
# ----------------------------------------------------------------------------
def card5(d):
    secs = (d.get("card5") or {}).get("sectors") or []
    if not secs:
        return unavailable("SECTOR STAGE BOARD")
    n = len(secs)
    cols = min(4, n)
    rows = math.ceil(n / cols)
    x0, y0, total_w, total_h = 120, 224, 1680, 701
    gap_x, gap_y = 22, 14
    cell_w = (total_w - (cols - 1) * gap_x) / cols
    cell_h = (total_h - (rows - 1) * gap_y) / rows
    maxabs = max([abs(s[k]) for s in secs for k in ("m3", "m1") if s[k] is not None], default=1) or 1
    p = []

    for i, item in enumerate(secs):
        col_idx, row_idx = i % cols, i // cols
        x = x0 + col_idx * (cell_w + gap_x)
        y = y0 + row_idx * (cell_h + gap_y)
        delay = .65 + i * .55
        avoid = bool(item.get("avoid"))
        border = "#d94a4e" if avoid else "#3478c8"
        compact, vcompact = cell_h < 170, cell_h < 132
        cls = "anim-card" + (" weak-card" if avoid else "")
        glow = f' data-glow="{RED}"' if avoid else ""
        g = f'<g class="{cls}" data-enter="{n2(delay)}"{glow}>'
        g += r(x, y, cell_w, cell_h, PANEL, border, 2, 18)
        pad = 14 if compact else 18
        tsize = 18 if vcompact else 21 if compact else 27
        tl = wrap_lines(pretty(item["name"]), max(10, int((cell_w - 2 * pad) / (tsize * .56))), 2)
        ty = y + (27 if compact else 36)
        for j, line in enumerate(tl):
            g += t(x + pad, ty + j * (tsize + 2), line, tsize, TXT, 750)

        tag_y = y + (57 if compact else 82)
        th = 25 if compact else 32
        tf = 13 if compact else 16
        tc = RED if avoid else BLUE
        cls_txt = item.get("class", "")
        tw = min(cell_w * .52, max(70, est_width(cls_txt, tf) + 2 * (th * .55)))
        if cls_txt:
            g += chip(x + pad, tag_y - 25, tw, th, cls_txt, tc, tf, 650)
        if avoid:
            ax_ = x + pad + (tw + 10 if cls_txt else 0)
            aw = min(cell_w - (ax_ - x) - pad, max(58, est_width("AVOID", tf) + 2 * (th * .55)))
            g += chip(ax_, tag_y - 25, aw, th, "AVOID", RED, tf, 700)

        def mcol(v):
            return DIM if v is None or v == 0 else GREEN if v > 0 else RED

        if vcompact:
            for j, (lab, key) in enumerate((("3M", "m3"), ("1M", "m1"))):
                g += t(x + pad + j * cell_w * .48, y + cell_h - 13, f"{lab} {signed(item[key])}", 14, mcol(item[key]), 700)
        else:
            ms = tag_y + (37 if compact else 55)
            mg = 27 if compact else 34
            msz = 16 if compact else 19
            bx0 = x + 235
            bw_ = cell_w - 235 - pad
            use_bars = (not compact) and bw_ >= 80
            for j, (lab, key) in enumerate((("3M RS", "m3"), ("1M RS", "m1"))):
                yy = min(y + cell_h - 10, ms + j * mg)
                val = item[key]
                g += t(x + pad, yy, lab, msz, MUTE, 500)
                g += counter(x + min(155, cell_w * .48), yy, val, "", "%", 1, msz, mcol(val), 700,
                             delay=delay + .2 + j * .35, duration=.75)
                if use_bars:
                    cx_ = bx0 + bw_ / 2
                    g += f'<rect x="{n2(bx0)}" y="{n2(yy - 14)}" width="{n2(bw_)}" height="10" rx="5" fill="{TRACK}"/>'
                    if val:
                        g += bar(cx_, yy - 14, abs(val) / maxabs * bw_ / 2, 10, mcol(val), delay + .2 + j * .35, .75,
                                 rx=5, direction=1 if val > 0 else -1, x0=cx_)
                    g += f'<path d="M{n2(cx_)} {n2(yy - 17)} V{n2(yy - 1)}" stroke="#3a4553" stroke-width="2"/>'
        p.append(g + "</g>")

    t_sum = .65 + (n - 1) * .55 + 1.2
    avoids = [pretty(s["name"]) for s in secs if s.get("avoid")]
    if avoids:
        txt = " · " + " · ".join(avoids)
        parts = [(f"{len(avoids)} SECTOR{'S' if len(avoids) != 1 else ''} FLAGGED AVOID", RED, 750), (txt, TXT, 600)]
    else:
        parts = [("NO SECTORS FLAGGED AVOID", GREEN, 750)]
    size = fit_size("".join(s for s, _, _ in parts), 1680, 24, .58, 16)
    p.append(grp(runs(120, 958, parts, size), "slide-item", t_sum, 'data-cue="Avoid list"'))
    return "".join(p)


# ----------------------------------------------------------------------------
# Card 6 - WHAT TO WATCH NEXT (opportunity radar)
# ----------------------------------------------------------------------------
def card6(d):
    c6 = d.get("card6") or {}
    stocks = c6.get("stocks") or []
    if not stocks:
        return unavailable("OPPORTUNITY RADAR")
    n = len(stocks)
    cols = min(2, n)
    rows = math.ceil(n / cols)
    x0, y0, total_w, total_h = 120, 218, 1680, 732
    gap_x, gap_y = 28, 12
    cell_w = (total_w - (cols - 1) * gap_x) / cols
    cell_h = (total_h - (rows - 1) * gap_y) / rows
    global_signals = c6.get("signals") if isinstance(c6.get("signals"), list) else DEFAULT_SIGNALS
    p = []

    for i, item in enumerate(stocks):
        col_idx, row_idx = i % cols, i // cols
        x = x0 + col_idx * (cell_w + gap_x)
        y = y0 + row_idx * (cell_h + gap_y)
        delay = .7 + i * .65
        compact, vcompact = cell_h < 175, cell_h < 135
        grade = item.get("grade", "")
        top = grade.upper() == "A"
        glow = f' data-glow="{GREEN}"' if top else ""
        g = f'<g class="anim-card" data-enter="{n2(delay)}" data-cue="{esc(item["name"])}"{glow}>'
        g += r(x, y, cell_w, cell_h, PANEL, GREEN if top else EDGE, 2, 18)
        pad = 16 if compact else 22
        nsize = 20 if vcompact else 24 if compact else 32
        nl = wrap_lines(item["name"], max(10, int((cell_w * .42) / (nsize * .56))), 2)
        for j, line in enumerate(nl):
            g += t(x + pad, y + (30 if compact else 43) + j * (nsize + 2), line, nsize, TXT, 800)

        gcol = GREEN if top else BLUE
        risk = item.get("risk_cat", "")
        rcol = GREEN if "low" in risk.lower() else AMBER if "medium" in risk.lower() else RED
        by_ = y + (31 if compact else 45)
        bx = x + cell_w * .48
        bh = 25 if compact else 32
        gw = min(112, cell_w * .22)
        rw = min(150, cell_w * .28)
        if grade:
            g += chip(bx, by_ - 21, gw, bh, "Grade " + grade, gcol, 14 if compact else 16, 650)
        rx_ = bx + (gw + 8 if grade else 0)
        if risk and rx_ + rw < x + cell_w - 85:
            g += chip(rx_, by_ - 21, rw, bh, risk[:16], rcol, 13 if compact else 15, 650)

        sc = item.get("score")
        ssize = 34 if compact else 43
        g += counter(x + cell_w - pad - 48, y + (35 if compact else 48), sc, "", "", 0, ssize, gcol, 800, "end",
                     delay + .2, 1.25)
        if sc is not None:
            g += t(x + cell_w - pad - 42, y + (35 if compact else 48), "/100", 16 if compact else 19, MUTE, 700)
            tw_ = cell_w - 2 * pad
            by2 = y + (50 if vcompact else 58 if compact else 66)
            g += f'<rect x="{n2(x + pad)}" y="{n2(by2)}" width="{n2(tw_)}" height="6" rx="3" fill="{TRACK}"/>'
            g += bar(x + pad, by2, tw_ * min(100, max(0, sc)) / 100, 6, gcol, delay + .25, 1.0, rx=3)

        sigs = item.get("signals") if isinstance(item.get("signals"), list) else global_signals
        sigs = [str(s) for s in sigs if str(s).strip()]
        if sigs:
            tag_y = y + cell_h - (12 if vcompact else 18 if compact else 27)
            tsz = 12 if vcompact else 13 if compact else 15
            th = 23 if vcompact else 27
            if not vcompact:
                g += t(x + pad, y + (83 if compact else 105), "Technical signals", 15 if compact else 18, MUTE, 500)
            xx = x + pad
            for s in sigs:
                w_ = len(s) * tsz * .6 + 26
                if xx + w_ > x + cell_w - pad:
                    break
                g += chip(xx, tag_y - 20, w_, th, s, GREEN, tsz, 600, 1.3)
                xx += w_ + 10
        p.append(g + "</g>")
    return "".join(p)



# ----------------------------------------------------------------------------
# Card 7 - SECTOR LEADERS & LAGGARDS (grouped by stage; 3M RS + 1M RS bars per sector)
# ----------------------------------------------------------------------------
TONES = {"green": GREEN, "blue": BLUE, "red": RED, "amber": AMBER}
TONE_ORDER = ["green", "blue", "red", "amber"]


def card7(d):
    c = d.get("card7") or {}
    groups = [x for x in (c.get("groups") or []) if x.get("sectors")]
    if not groups:
        return unavailable("SECTOR LEADERS & LAGGARDS")
    k = len(groups)
    x0, y0, total_w, gap_x = 120, 222, 1680, 28
    pw = (total_w - (k - 1) * gap_x) / k
    allv = [abs(s[m]) for x in groups for s in x["sectors"] for m in ("m1", "m3") if s[m] is not None]
    maxabs = max(allv, default=1) or 1
    max_rows = max(len(x["sectors"]) for x in groups)
    head_h = 118
    rh = min(112, (640 - head_h - 24) / max_rows)
    total_h = head_h + max_rows * rh + 20
    p, cursor = [], 0.6

    for gi, grpd in enumerate(groups):
        px = x0 + gi * (pw + gap_x)
        tone = TONES.get(grpd["tone"], BLUE)
        n_ = len(grpd["sectors"])
        panel = r(px, y0, pw, total_h, PANEL, tone, 2, 20)
        panel += t(px + 28, y0 + 56, grpd["label"], fit_width(grpd["label"], pw - 150, 28, 9), tone, 800)
        panel += chip(px + pw - 28 - 56, y0 + 28, 56, 36, str(n_), tone, 20, 750)
        panel += t(px + 28, y0 + 92, f"{n_} sector{'s' if n_ != 1 else ''}", 18, DIM, 600)
        panel += t(px + pw - 28, y0 + 92, "RS vs NIFTY50", 16, FAINT, 600, "end")
        p.append(grp(panel, "anim-panel", cursor, f'data-cue="{esc(grpd["label"])}"'))
        cursor += 1.0

        vw = 88
        right_end = px + pw - 34
        track_end = right_end - vw - 8
        track_w = max(90, min(200, pw * .36))
        track_x = track_end - track_w
        cx = track_x + track_w / 2
        half = track_w / 2

        for si, s in enumerate(grpd["sectors"]):
            ry = y0 + head_h + si * rh
            ih = rh - 10
            nm = pretty(s["name"])
            nsz = fit_width(nm, track_x - 44 - (px + 36) - 6, 26, 9)
            inner = r(px + 18, ry, pw - 36, ih, "#111922", "#1e2a38", 1.5, 14)
            inner += t(px + 36, ry + ih * .5 + .364 * nsz, nm, nsz, TXT, 750)
            bh = max(8, min(14, rh * .15))
            vs = max(14, min(21, rh * .22))
            for j, (lab, key) in enumerate((("3M", "m3"), ("1M", "m1"))):
                v = s[key]
                yy = ry + ih * (0.32 if j == 0 else 0.70)
                col = _tint(v)
                inner += t(track_x - 10, yy + .364 * 14, lab, 14, MUTE, 700, "end")
                inner += f'<rect x="{n2(track_x)}" y="{n2(yy - bh / 2)}" width="{n2(track_w)}" height="{n2(bh)}" rx="{n2(bh / 2)}" fill="{TRACK}"/>'
                if v:
                    inner += bar(cx, yy - bh / 2, abs(v) / maxabs * half, bh, col, cursor + .15 + j * .3, .9,
                                 rx=bh / 2, direction=1 if v > 0 else -1, x0=cx)
                inner += f'<path d="M{n2(cx)} {n2(yy - bh / 2 - 3)} V{n2(yy + bh / 2 + 3)}" stroke="#4a5666" stroke-width="2"/>'
                inner += counter(right_end, yy + .364 * vs, v, _pfx(v), "%", 1, vs, col, 800, "end",
                                 cursor + .15 + j * .3, .9)
            p.append(grp(inner, "anim-row", cursor, f'data-cue="{esc(nm)}"'))
            cursor += .85
        cursor += .4

    flat = [(s, pretty(s["name"])) for x in groups for s in x["sectors"]]

    def best(key, fn):
        cand = [(s[key], nm) for s, nm in flat if s[key] is not None]
        return fn(cand) if cand else None

    parts = []
    for lab, key in (("3M", "m3"), ("1M", "m1")):
        hi, lo = best(key, max), best(key, min)
        if hi:
            if parts:
                parts.append(("   ·   ", MUTE, 500))
            parts += [(f"Strongest {lab} ", MUTE, 500), (f"{hi[1]} {signed(hi[0])}", _tint(hi[0]), 750),
                      (f"   Weakest {lab} ", MUTE, 500), (f"{lo[1]} {signed(lo[0])}", _tint(lo[0]), 750)]
    size = fit_size("".join(s for s, _, _ in parts), 1680, 25, .56, 15)
    p.append(grp(runs(120, y0 + total_h + 56, parts, size), "slide-item", cursor, 'data-cue="Sector takeaway"'))
    if c.get("as_of"):
        p.append(grp(t(120, y0 + total_h + 96, c["as_of"], 18, "#657080", 500), "fade-item", cursor + .1))
    return "".join(p)


# ----------------------------------------------------------------------------
# Card 8 - VOLATILITY + OPTIONS SENTIMENT (two zone gauges)
# Zone tables are plain constants: edit to taste. (low, high, name, colour)
# ----------------------------------------------------------------------------
VIX_ZONES = [(0, 13, "CALM", GREEN), (13, 20, "NORMAL", BLUE), (20, 25, "ELEVATED", AMBER), (25, 40, "HIGH", RED)]
VIX_SCALE = (8, 35)
PCR_ZONES = [(0.4, 0.8, "BEARISH", RED), (0.8, 1.2, "NEUTRAL", AMBER), (1.2, 1.6, "BULLISH", GREEN)]
PCR_SCALE = (0.4, 1.6)


def zone_gauge(px, py, pw, ph, title, sub, value, dec, zones, scale, t0, side_kind, side, state, source):
    """One panel: title, big counter, side widget, zone bar with travelling marker, reading line, source line."""
    lo_s, hi_s = scale
    W = pw - 80
    bx, by_ = px + 40, py + 300
    p = [grp(r(px, py, pw, ph, PANEL, EDGE, 2, 20) + t(px + 40, py + 62, title, 26, DIM, 750) +
             t(px + pw - 40, py + 62, sub, 20, FAINT, 600, "end"), "anim-panel", t0, f'data-cue="{esc(title)}"')]
    if value is None:
        p.append(grp(t(px + pw / 2, py + ph / 2, "DATA UNAVAILABLE", 34, DIM, 750, "middle"), "fade-item", t0 + 1))
        return p
    zname, zcol = state
    head = counter(px + 40, py + 205, value, "", "", dec, 118, TXT, 800, delay=t0 + 1.15, duration=1.6, pulse=True)
    if side_kind == "change" and side is not None:
        c_ = RED if side > 0 else GREEN if side < 0 else DIM      # a rising VIX is fear
        ax, ay = px + pw - 40 - est_width("+" + plain(abs(side)) + "%", 50) - 40, py + 155
        tri = (f"M{ax - 18} {ay + 14} L{ax + 18} {ay + 14} L{ax} {ay - 18} Z" if side >= 0
               else f"M{ax - 18} {ay - 14} L{ax + 18} {ay - 14} L{ax} {ay + 18} Z")
        head += f'<path d="{tri}" fill="{c_}"/>'
        head += counter(px + pw - 40, py + 172, abs(side), "+" if side > 0 else "-", "%", 1, 50, c_, 800, "end",
                        t0 + 1.15, 1.6)
    elif side_kind == "label" and side:
        room = pw - 80 - est_width(f"{value:.{dec}f}", 118) - 30
        ls = fit_width(side.upper(), room - 44, 26, 10)
        lw = min(room, max(130, est_width(side.upper(), ls) + 44))
        head += chip(px + pw - 40 - lw, py + 124, lw, 50, side.upper(), zcol, ls, 750, 2)
    p.append(grp(head, "slide-item", t0 + 1.1, f'data-cue="{esc(title)} value"'))

    def X(v):
        return bx + W * min(1.0, max(0.0, (v - lo_s) / (hi_s - lo_s)))
    seg = ""
    for lo, hi, nm, col in zones:
        a, b = X(max(lo, lo_s)), X(min(hi, hi_s))
        if b - a > 1:
            seg += f'<rect x="{n2(a)}" y="{by_}" width="{n2(b - a)}" height="22" fill="{col}" opacity=".85"/>'
            seg += t((a + b) / 2, by_ + 78, nm, 17, col, 700, "middle")
    first, last = zones[0], zones[-1]
    ticks = sorted({z[0] for z in zones[1:]})
    for tv in ticks:
        seg += t(X(tv), by_ + 50, plain(tv), 16, FAINT, 600, "middle")
    seg = (f'<clipPath id="zc{int(px)}"><rect x="{n2(bx)}" y="{by_}" width="{n2(W)}" height="22" rx="11"/></clipPath>'
           f'<g clip-path="url(#zc{int(px)})">{seg.split("<text")[0]}</g>' + "<text" + "<text".join(seg.split("<text")[1:]))
    p.append(grp(seg, "fade-item", t0 + 3.4, f'data-cue="{esc(title)} zones"'))
    # marker travels from the left end of the bar to the value
    mx = X(value) - bx
    marker = (f'<path d="M{n2(bx)} {by_ - 6} V{by_ + 28}" stroke="{TXT}" stroke-width="4" stroke-linecap="round"/>'
              f'<path d="M{n2(bx - 11)} {by_ - 26} L{n2(bx + 11)} {by_ - 26} L{n2(bx)} {by_ - 6} Z" fill="{TXT}"/>')
    p.append(f'<g data-move="{n2(mx)}" data-delay="{n2(t0 + 3.45)}" data-duration="1.6">{marker}</g>')
    lo_z, hi_z = None, None
    for lo, hi, nm, col in zones:
        if lo <= value < hi or (value >= hi_s and hi == last[1]):
            lo_z, hi_z = lo, hi
    rng = f" ({plain(lo_z)}–{plain(hi_z)})" if lo_z is not None else ""
    parts = [(f"{plain(value) if dec == 0 else f'{value:.{dec}f}'} sits in the ", MUTE, 500), (zname + " zone", zcol, 750),
             (rng, MUTE, 500)]
    txt = "".join(s for s, _, _ in parts)
    p.append(grp(runs(px + 40, py + 470, parts, fit_size(txt, W, 28, .56, 16)), "slide-item", t0 + 5.6,
                 f'data-cue="{esc(title)} reading"'))
    if source:
        p.append(grp(t(px + 40, py + ph - 40, source, fit_width(source, W, 18, 12), "#657080", 500), "fade-item", t0 + 6.4))
    return p


def _zone_of(v, zones):
    for lo, hi, nm, col in zones:
        if lo <= v < hi:
            return nm, col
    return (zones[-1][2], zones[-1][3]) if v >= zones[-1][1] else (zones[0][2], zones[0][3])


def card8(d):
    c = d.get("card8") or {}
    vix, pcr = c.get("vix") or {}, c.get("pcr") or {}
    if not c or (vix.get("value") is None and pcr.get("value") is None):
        return unavailable("VOLATILITY & OPTIONS SENTIMENT")
    pw, ph, y0 = 820, 640, 222
    p = []
    vs = _zone_of(vix["value"], VIX_ZONES) if vix.get("value") is not None else ("", DIM)
    p += zone_gauge(120, y0, pw, ph, "VOLATILITY", vix.get("name") or "INDIA VIX", vix.get("value"), 1, VIX_ZONES, VIX_SCALE,
                    0.6, "change", vix.get("change_pct"), vs, vix.get("source", ""))
    if pcr.get("value") is not None:
        zn, zc = _zone_of(pcr["value"], PCR_ZONES)
        lab = (pcr.get("label") or zn).lower()
        zc = GREEN if "bull" in lab else RED if "bear" in lab else AMBER if "neut" in lab else zc
        zn = zn if not pcr.get("label") else zn
    else:
        zn, zc, lab = "", DIM, ""
    p += zone_gauge(980, y0, pw, ph, "OPTIONS SENTIMENT", pcr.get("name") or "PUT-CALL RATIO", pcr.get("value"), 2, PCR_ZONES,
                    PCR_SCALE, 7.6, "label", lab, (zn, zc), pcr.get("basis", ""))
    parts = []
    if vix.get("value") is not None:
        parts += [("India VIX ", MUTE, 500), (plain(vix["value"]), TXT, 750), (f"  {vs[0]}", vs[1], 750)]
        if vix.get("change_pct") is not None:
            parts.append((f"  {signed(vix['change_pct'])}", RED if vix["change_pct"] > 0 else GREEN, 750))
    if pcr.get("value") is not None:
        parts += [("      ·      Options PCR ", MUTE, 500), (f"{pcr['value']:.2f}", TXT, 750),
                  (f"  {lab}", zc, 750)]
    if parts:
        size = fit_size("".join(s for s, _, _ in parts), 1680, 26, .56, 16)
        p.append(grp(runs(120, 920, parts, size), "slide-item", 14.2, 'data-cue="Volatility and sentiment summary"'))
    return "".join(p)


# ----------------------------------------------------------------------------
# Validation / normalisation (lenient: bad or missing data degrades, never crashes)
# ----------------------------------------------------------------------------
def normalize(d):
    """Return (clean_data, warnings). Only a non-object root is fatal."""
    if not isinstance(d, dict):
        raise ValueError("Input JSON root must be an object")
    W = []
    n = {"date": str(d.get("date") or ""), "brand": str(d.get("brand") or "MARKET INTELLIGENCE")}

    def obj(k):
        v = d.get(k)
        if not isinstance(v, dict):
            W.append(f"{k}: missing or not an object - scene shows 'DATA UNAVAILABLE'")
            return {}
        return v

    def lst(c, k, path):
        v = c.get(k)
        if not isinstance(v, list) or not v:
            W.append(f"{path}: missing or empty - scene shows 'DATA UNAVAILABLE'")
            return []
        return v

    c = obj("card1")
    n["card1"] = {
        "index": str(c.get("index") or "").strip(), "bias": str(c.get("bias") or "").strip(),
        "regime": str(c.get("regime") or "").strip(), "hook": str(c.get("hook") or "").strip(),
        **{k: fnum(c.get(k)) for k in ("confidence", "score", "bullish", "sideways", "bearish")}} if c else {}

    c = obj("card2")
    scores = []
    for i, it in enumerate(lst(c, "scores", "card2.scores") if c else []):
        if not isinstance(it, dict):
            W.append(f"card2.scores[{i}] skipped (not an object)")
            continue
        sc = fnum(it.get("score"))
        if sc is not None and not 0 <= sc <= 100:
            W.append(f"card2.scores[{i}].score {sc} clamped to 0-100")
            sc = min(100.0, max(0.0, sc))
        col = str(it.get("color") or "").lower()
        if col not in ("red", "amber", "green"):
            W.append(f"card2.scores[{i}].color '{col}' unknown - neutral grey used")
        scores.append({"name": str(it.get("name") or f"Factor {i + 1}").strip(), "score": sc, "color": col,
                       "desc": str(it.get("desc") or "")})
    n["card2"] = {"scores": scores}

    c = obj("card3")
    rows3 = []
    for i, s in enumerate(lst(c, "sectors", "card3.sectors") if c else []):
        name = today = d7 = None
        if isinstance(s, dict):
            name, today, d7 = s.get("name"), fnum(s.get("today")), fnum(s.get("d7", s.get("week")))
        elif isinstance(s, (list, tuple)) and len(s) == 3:
            name, today, d7 = s[0], fnum(s[1]), fnum(s[2])
        elif isinstance(s, (list, tuple)) and len(s) == 2:
            name, d7 = s[0], fnum(s[1])          # legacy [name, 7-day]
        if not name or (today is None and d7 is None):
            W.append(f"card3.sectors[{i}] skipped (needs name + today and/or d7)")
            continue
        rows3.append({"name": str(name), "today": today, "d7": d7})
    if rows3 and all(r_["today"] is None for r_ in rows3):
        W.append("card3: no 'today' values - the TODAY chart shows 'DATA UNAVAILABLE'")
    n["card3"] = {"title": str(c.get("title") or "").strip(), "sectors": rows3}

    c = obj("card4")
    out4 = {}
    for key in ("fii", "dii"):
        f = c.get(key)
        if not isinstance(f, dict):
            if c:
                W.append(f"card4.{key} missing - panel shows 'DATA UNAVAILABLE'")
            out4[key] = None
            continue
        buy, sell, net = fnum(f.get("buy")), fnum(f.get("sell")), fnum(f.get("net"))
        if net is None and buy is not None and sell is not None:
            net = buy - sell
        elif None not in (buy, sell, net) and abs((buy - sell) - net) > 0.5:
            W.append(f"card4.{key}: buy - sell ({buy - sell:g}) does not match net ({net:g})")
        out4[key] = {"buy": buy, "sell": sell, "net": net}
    for key in ("accumulation", "distribution"):
        v = fnum(c.get(key))
        if c and v is None:
            W.append(f"card4.{key} missing - bar segment hidden")
        out4[key] = v
    out4["as_of"] = str(c.get("as_of") or "")
    n["card4"] = out4 if c else {}

    c = obj("card5")
    s5 = []
    for i, it in enumerate(lst(c, "sectors", "card5.sectors") if c else []):
        if not isinstance(it, dict) or not it.get("name"):
            W.append(f"card5.sectors[{i}] skipped (needs name)")
            continue
        s5.append({"name": str(it["name"]), "m3": fnum(it.get("m3")), "m1": fnum(it.get("m1")),
                   "class": str(it.get("class") or ""), "avoid": bool(it.get("avoid"))})
    n["card5"] = {"sectors": s5}

    c = obj("card6")
    s6 = []
    for i, it in enumerate(lst(c, "stocks", "card6.stocks") if c else []):
        if not isinstance(it, dict) or not it.get("name"):
            W.append(f"card6.stocks[{i}] skipped (needs name)")
            continue
        sc = fnum(it.get("score"))
        if sc is not None and not 0 <= sc <= 100:
            W.append(f"card6.stocks[{i}].score {sc} clamped to 0-100")
            sc = min(100.0, max(0.0, sc))
        s6.append({"name": str(it["name"]), "score": sc, "grade": str(it.get("grade") or ""),
                   "risk_cat": str(it.get("risk_cat") or ""),
                   "signals": it.get("signals") if isinstance(it.get("signals"), list) else None})
    n["card6"] = {"stocks": s6, "signals": c.get("signals") if isinstance(c.get("signals"), list) else None}
    # ---- optional cards 7 / 8 (only validated when present) ----
    if "card7" in d:
        c = obj("card7")
        groups = []
        for gi, g in enumerate(c.get("groups") if isinstance(c.get("groups"), list) else []):
            if not isinstance(g, dict):
                W.append(f"card7.groups[{gi}] skipped (not an object)")
                continue
            secs7 = []
            for si, it in enumerate(g.get("sectors") if isinstance(g.get("sectors"), list) else []):
                if isinstance(it, (list, tuple)) and len(it) == 3:
                    it = {"name": it[0], "m1": it[1], "m3": it[2]}
                elif isinstance(it, (list, tuple)) and len(it) == 2:
                    it = {"name": it[0], "m1": it[1]}
                if not isinstance(it, dict) or not it.get("name"):
                    W.append(f"card7.groups[{gi}].sectors[{si}] skipped (needs name)")
                    continue
                # legacy {"name","change"} meant the 1M figure
                m1 = fnum(it.get("m1", it.get("rs1m", it.get("change"))))
                m3 = fnum(it.get("m3", it.get("rs3m")))
                if m1 is None and m3 is None:
                    W.append(f"card7.groups[{gi}].sectors[{si}] has no m1/m3 values")
                secs7.append({"name": str(it["name"]), "m1": m1, "m3": m3})
            tone = str(g.get("tone") or "").lower()
            if tone not in TONES:
                tone = TONE_ORDER[min(gi, len(TONE_ORDER) - 1)]
            groups.append({"label": str(g.get("label") or f"GROUP {gi + 1}").upper(), "tone": tone, "sectors": secs7})
        n["card7"] = {"groups": groups, "as_of": str(c.get("as_of") or "")} if c else {}
    if "card8" in d:
        c = obj("card8")
        v, q = c.get("vix") if isinstance(c.get("vix"), dict) else {}, c.get("pcr") if isinstance(c.get("pcr"), dict) else {}
        n["card8"] = {
            "vix": {"value": fnum(v.get("value")), "change_pct": fnum(v.get("change_pct")),
                    "source": str(v.get("source") or ""), "name": str(v.get("name") or "")},
            "pcr": {"value": fnum(q.get("value")), "label": str(q.get("label") or ""),
                    "basis": str(q.get("basis") or ""), "name": str(q.get("name") or "")}} if c else {}
    return n, W


def validate(d):
    """Backwards-compatible entry point: returns the list of data warnings."""
    return normalize(d)[1]


# ----------------------------------------------------------------------------
# Sequencer: re-times every animation so each one starts only after the previous one has finished
# ----------------------------------------------------------------------------
SVGNS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVGNS)
REVEAL_CLS = {"heading-slide", "slide-item", "anim-card", "anim-row", "anim-panel", "fade-item"}
CLUSTER = 0.6      # animations of one card that start within this window belong to the same beat
MERGE = 0.1        # animations that were designed to start together stay together
PULSE = 0.55       # extra time for the score "pulse" after a counter lands


def _anim(el):
    """(attribute, start, duration) of an animated element, else None."""
    a = el.attrib
    if "data-type-delay" in a:
        return "data-type-delay", fnum(a["data-type-delay"], 0.0), fnum(a.get("data-type-duration"), 1.5)
    if "data-delay" in a:
        dur = fnum(a.get("data-duration"), 1.5) + (PULSE if "data-pulse" in a else 0.0)
        return "data-delay", fnum(a["data-delay"], 0.0), dur
    if "data-enter" in a:
        return "data-enter", fnum(a["data-enter"], 0.0), REVEAL
    return None


def sequence(content, gap=GAP, first=INTRO, pauses=None):
    """Return (new_content, beats, end). A beat is a set of animations that play together; beats play strictly
    one after another. Within one card (gauge, panel, row...) later pieces wait for earlier ones. The header
    (class 'hdr') keeps its own intro timing. pauses = {beat_number(1-based): extra_seconds_after_it}."""
    pauses = pauses or {}
    xml_content = f'<g xmlns="{SVGNS}">{content}</g>'

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        print("\n========== SVG XML PARSE ERROR ==========")
        print(f"Error: {e}")

        match = re.search(r"column (\d+)", str(e))

        if match:
            pos = int(match.group(1))

            start = max(0, pos - 150)
            end = min(len(xml_content), pos + 150)

            print("\nProblematic XML around error:")
            print("-----------------------------------")
            print(xml_content[start:end])
            print("-----------------------------------")

            print("             " + " " * (pos - start) + "^")

        raise
    parent = {c: p for p in root.iter() for c in p}
    skip = set()
    for el in root.iter():
        if "hdr" in el.get("class", "").split():
            skip.update(el.iter())

    def unit_of(el):
        top, cur = None, el
        while cur is not None and cur is not root:
            if REVEAL_CLS & set(cur.get("class", "").split()):
                top = cur
            cur = parent.get(cur)
        return top if top is not None else el

    units = {}
    for el in root.iter():
        if el in skip:
            continue
        info = _anim(el)
        if info:
            units.setdefault(unit_of(el), []).append((el,) + info)

    pieces = []
    for order, (u, ms) in enumerate(units.items()):
        ms = sorted(ms, key=lambda m: m[2])
        cur = [ms[0]]
        for m in ms[1:]:
            if m[2] - cur[0][2] > CLUSTER:
                pieces.append((cur[0][2], order, u, cur))
                cur = [m]
            else:
                cur.append(m)
        pieces.append((cur[0][2], order, u, cur))
    pieces.sort(key=lambda p: (p[0], p[1]))

    groups = []
    for pc in pieces:
        if groups and pc[0] - groups[-1][0][0] <= MERGE:
            groups[-1].append(pc)
        else:
            groups.append([pc])

    beats, cursor, seen_labels = [], first, {}
    for n, grp_ in enumerate(groups, 1):
        b0 = grp_[0][0]
        delta = cursor - b0
        length, label = 0.0, ""
        for _, _, u, ms in grp_:
            if not label:
                label = u.get("data-cue", "")
            for el, attr, s, dur in ms:
                el.set(attr, str(n2(s + delta)))
                length = max(length, s - b0 + dur)
        label = label or f"Step {n}"
        seen_labels[label] = seen_labels.get(label, 0) + 1
        if seen_labels[label] > 1:
            label += f" (part {seen_labels[label]})"
        beats.append({"start": n2(cursor), "len": n2(length), "label": label})
        cursor += length + gap + float(pauses.get(n, 0) or 0)
    end = (beats[-1]["start"] + beats[-1]["len"]) if beats else first
    out = ET.tostring(root, encoding="unicode")
    out = out[out.index(">") + 1: out.rindex("</g>")]
    return out, beats, n2(end)


# ----------------------------------------------------------------------------
# Combined film
# ----------------------------------------------------------------------------
FILES = ["01_market_bias.svg", "02_market_health_scores.svg", "03_sector_performance.svg",
         "04_fii_dii_flows.svg", "05_sector_stage_board.svg", "06_opportunity_radar.svg",
         "07_sector_leaders_laggards.svg", "08_volatility_options.svg"]


def make_combined(out, scenes):
    parts, schedule, timeline, stops, start = [], [], [], [], 0.0
    for i, sc in enumerate(scenes):
        dur = sc["dur"]
        schedule.append({"start": n2(start), "dur": n2(dur)})
        for b in sc["beats"]:
            stops.append([n2(start + b["start"]), n2(start + b["start"])])
        last_scene = i == len(scenes) - 1
        stops.append([n2(start + sc["end"] + .05), n2(start + dur) if last_scene else n2(start + dur - .5)])
        timeline.append({"scene": i + 1, "file": sc["file"], "start": n2(start), "duration": n2(dur),
                         "animation_ends_at": n2(start + sc["end"]), "hold_seconds": n2(dur - sc["end"]),
                         "beats": [{"n": k + 1, "t": n2(start + b["start"]), "length": b["len"], "label": b["label"]}
                                   for k, b in enumerate(sc["beats"])]})
        # parts.append(f'<g class="scene" id="scene{i + 1}"{"" if i == 0 else " style=\"display:none\""}>{sc["content"]}</g>')
        display_style = '' if i == 0 else ' style="display:none"'
        parts.append(f'<g class="scene" id="scene{i + 1}"{display_style}>{sc["content"]}</g>')

        
        start += dur
    base = ('<rect width="1920" height="1080" fill="#303030"/>'
            '<rect x="88" y="46" width="1744" height="988" rx="30" fill="#080a0d"/>')
    dots = "".join(f'<circle class="pdot" cx="{1744 - (len(scenes) - 1 - k) * 24}" cy="985" r="5" fill="#243040"/>'
                   for k in range(len(scenes)))
    master = ('<svg xmlns="http://www.w3.org/2000/svg" overflow="hidden" width="1920" height="1080" viewBox="0 0 1920 1080">'
              + DEFS + STYLE + base + "".join(parts) + dots +
              "<script><![CDATA[" + engine(schedule, start, stops) + "]]></script></svg>")
    (out / "combined_six_card_animation.svg").write_text(master, encoding="utf-8")
    (out / "scene_timeline.json").write_text(
        json.dumps({"total_seconds": n2(start), "scenes": timeline}, indent=2, ensure_ascii=False), encoding="utf-8")
    return start


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    import argparse
    base = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Generate the animated market cards.")
    ap.add_argument("input", nargs="?", default=str(base / "input" / "daily_data.json"))
    ap.add_argument("output", nargs="?", default=str(base / "output" / "svg"))
    ap.add_argument("--timing", default=None, help="timing.json (default: input/timing.json if it exists)")
    a = ap.parse_args()
    inp, out = Path(a.input), Path(a.output)
    tpath = Path(a.timing) if a.timing else base / "input" / "timing.json"
    tcfg = json.loads(tpath.read_text(encoding="utf-8")) if tpath.exists() else {}
    if tpath.exists():
        print("Using timing file:", tpath)

    raw = json.loads(inp.read_text(encoding="utf-8"))
    d, warnings = normalize(raw)
    out.mkdir(parents=True, exist_ok=True)
    for w in warnings:
        print("WARNING:", w)

    c3title = d["card3"].get("title") or "SECTOR PERFORMANCE · TODAY & 7 DAYS"
    builders = [
        (FILES[0], card1, "THE BIG PICTURE", ""),
        (FILES[1], card2, "MARKET HEALTH", "HOW STRONG IS THE FOUNDATION?"),
        (FILES[2], card3, "SECTOR MOMENTUM", c3title),
        (FILES[3], card4, "FII / DII FLOWS", "WHERE IS INSTITUTIONAL MONEY GOING?"),
        (FILES[4], card5, "SECTOR STAGE BOARD", "WHERE IS STRENGTH BUILDING?"),
        (FILES[5], card6, "OPPORTUNITY RADAR", "WHAT TO WATCH NEXT"),
    ]
    if "card7" in d:
        builders.append((FILES[6], card7, "SECTOR LEADERS & LAGGARDS", "WHO IS LEADING, WHO IS FADING?"))
    if "card8" in d:
        builders.append((FILES[7], card8, "VOLATILITY & OPTIONS", "HOW NERVOUS IS THE MARKET?"))
    scenes, template = [], {}
    for i, (fn, builder, eyebrow, title) in enumerate(builders, 1):
        cfg = {"gap": GAP, "hold": HOLD, "intro_pause": 0.0}
        for layer in (tcfg.get("default") or {}, (tcfg.get("scenes") or {}).get(str(i)) or {}):
            cfg.update({k: v for k, v in layer.items() if v is not None and not k.startswith("_")})
        pauses = {int(k): float(v) for k, v in (cfg.get("pauses") or {}).items()}
        raw_content = header(eyebrow, title, d["date"], brand=d["brand"]) + builder(d)
        content, beats, end = sequence(raw_content, float(cfg["gap"]), INTRO + float(cfg["intro_pause"]), pauses)
        dur = end + float(cfg["hold"])
        if cfg.get("duration"):
            want = float(cfg["duration"])
            if want < end + 0.5:
                print(f"WARNING: scene {i}: duration {want:g}s is shorter than its animation ({end:.1f}s) - using {end + .5:.1f}s")
            dur = max(want, end + 0.5)
        stops = [[b["start"], b["start"]] for b in beats] + [[n2(end + .05), n2(dur)]]
        (out / fn).write_text(svg_doc(content, True, fn, total=dur, stops=stops), encoding="utf-8")
        scenes.append({"file": fn, "content": content, "beats": beats, "end": end, "dur": dur})
        template[str(i)] = {"duration": None, "hold": HOLD, "intro_pause": 0.0, "gap": GAP, "pauses": {},
                            "_beats_natural_end_s": end,
                            "_beats": [f'{k + 1}: {b["label"]}  ({b["len"]}s)' for k, b in enumerate(beats)]}
    total = make_combined(out, scenes)
    (out / "timing_template.json").write_text(json.dumps({
        "_help": "Copy to input/timing.json and keep only what you change. duration = total seconds of a card "
                 "(overrides hold). hold = seconds the finished card stays. intro_pause = extra wait before the first "
                 "animation. gap = pause between steps. pauses = {\"3\": 6} adds 6 s after step 3 (see _beats).",
        "default": {"gap": GAP, "hold": HOLD, "intro_pause": 0.0}, "scenes": template},
        indent=2, ensure_ascii=False), encoding="utf-8")
    for p in out.glob("*.svg"):
        ET.parse(p)
    print(f"Generated {len(scenes)} SVGs + combined_six_card_animation.svg ({total:.1f}s), scene_timeline.json, timing_template.json:", out)
    for k, s in enumerate(scenes, 1):
        print(f"  card {k}: {len(s['beats'])} steps, animation ends {s['end']:.1f}s, card length {s['dur']:.1f}s")


if __name__ == "__main__":
    main()