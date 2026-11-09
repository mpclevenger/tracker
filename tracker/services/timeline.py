"""Server-rendered SVG timelines (DESIGN.md → Timelines). Returns markup strings."""

from datetime import date
from html import escape

from core import clock
from tracker.lifecycle import color as stage_color
from tracker.services.lifecycle import stage_infos
from tracker.models import Initiative, Milestone, Rag

RAG_COLOR = {Rag.GREEN: "#1a7f37", Rag.AMBER: "#bf8700", Rag.RED: "#cf222e"}
MS_COLOR = {
    Milestone.Status.DONE: "#1a7f37",
    Milestone.Status.SLIPPED: "#cf222e",
    Milestone.Status.ON_TRACK: "#0969da",
    Milestone.Status.PLANNED: "#0969da",
}



def _d(d):
    return f"{d:%b} {d.day}, {d.year}"

def _short(d: date) -> str:
    return f"{d:%b} {d.day} ’{d:%y}"


def _month_start(d: date, offset=0) -> date:
    y, m = d.year, d.month + offset
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return date(y, m, 1)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


class Scale:
    def __init__(self, start: date, months: int, left: float, width: float):
        self.start, self.months, self.left = start, months, left
        self.mw = (width - left - 20) / max(months, 1)

    def x(self, d: date) -> float:
        return self.left + ((d - self.start).days / 30.4375) * self.mw

    def axis(self, top: float, height: float) -> str:
        out = []
        for m in range(self.months):
            dt = _month_start(self.start, m)
            xx = self.left + m * self.mw
            label = f"{dt:%b}" + (f" ’{dt:%y}" if dt.month == 1 or m == 0 else "")
            out.append(
                f'<line x1="{xx:.1f}" y1="{top-6}" x2="{xx:.1f}" y2="{top+height}" stroke="#eee"/>'
                f'<text x="{xx+2:.1f}" y="{top-10}" font-size="10" fill="#666">{label}</text>'
            )
        return "".join(out)

    def today_line(self, top, bottom) -> str:
        tx = self.x(clock.today())
        return (
            f'<line x1="{tx:.1f}" y1="{top-6}" x2="{tx:.1f}" y2="{bottom}" stroke="#cf222e" stroke-dasharray="3 3"/>'
            f'<text x="{tx+3:.1f}" y="{bottom+12}" font-size="10" fill="#cf222e">today</text>'
        )


def legend() -> str:
    stages = "".join(
        f'<span><span class="swatch" style="background:{s.color}"></span>{escape(s.name)}</span>'
        for s in stage_infos()[1:6]
    )
    return (
        f'<div class="legend">{stages}'
        '<span><span class="swatch" style="border:1.5px solid #0969da;background:none"></span>planned (outline)</span>'
        '<span><span class="swatch" style="background:#0969da"></span>forecast/actual (filled)</span>'
        '<span>◆ key milestone: <span style="color:#1a7f37">done</span> · <span style="color:#0969da">on track</span>'
        ' · <span style="color:#cf222e">slipped</span> (hollow = original plan)</span></div>'
    )


def _phase_dates(ph):
    ps, pe = ph.planned_start, ph.planned_end
    fs = ph.actual_start or ph.forecast_start or ps
    fe = ph.actual_end or ph.forecast_end or pe
    return ps, pe, fs, fe


def portfolio_window(bundles, months_before: int = 2) -> tuple[date, int]:
    """Window start (first of the month, `months_before` months back) and month count to the latest date."""
    today = clock.today()
    start = _month_start(today, -months_before)
    latest = today
    for b in bundles:
        for ph in b.phases:
            latest = max([latest] + [d for d in _phase_dates(ph) if d])
        for m in b.milestones:
            latest = max([latest] + [d for d in (m.planned, m.forecast, m.actual) if d])
    end = _month_start(latest, 2)  # one full month after the month holding the latest date
    return start, max(_months_between(start, end), 4)


def portfolio_svg(bundles, evaluations: dict, start: date | None = None, months: int | None = None) -> str:
    """One lane per initiative. `evaluations` maps entity_id -> Evaluation."""
    auto_start, auto_months = portfolio_window(bundles)
    start = start or auto_start
    months = months or auto_months
    W, left, row_h, top, bar_h = 1100, 230, 50, 28, 26
    sc = Scale(start, months, left, W)
    height = len(bundles) * row_h
    out = [f'<svg viewBox="0 0 {W} {top+height+30}" width="100%">', sc.axis(top, height),
           sc.today_line(top, top + height)]
    for r, b in enumerate(bundles):
        y = top + r * row_h + 8
        init = b.initiative
        ev = evaluations.get(b.entity_id)
        color = "#6e7781" if init.status == Initiative.Status.PROPOSED else RAG_COLOR[ev.effective if ev else Rag.GREEN]
        name = init.name if len(init.name) <= 30 else init.name[:29] + "…"
        suffix = " (proposed)" if init.status == Initiative.Status.PROPOSED else ""
        out.append(f'<text x="4" y="{y+16}" font-size="12.5" font-weight="600" fill="{color}">{escape(name)}{suffix}</text>')
        if init.status == Initiative.Status.ACTIVE and ev and ev.effective == Rag.GREEN:
            out.append(f'<text x="4" y="{y+29}" font-size="10" fill="{RAG_COLOR[Rag.GREEN]}">✓ On track</text>')
        for ph in b.phases:
            ps, pe, fs, fe = _phase_dates(ph)
            if not (ps and pe and fs and fe):
                continue
            c = stage_color(ph.stage)
            if fe < start and pe < start:
                continue  # entirely before the window
            px1, px2 = max(sc.x(ps), left), sc.x(pe)
            fx1, fx2 = max(sc.x(fs), left), sc.x(fe)
            w = max(2.0, fx2 - fx1)
            out.append(
                f'<rect x="{px1:.1f}" y="{y}" width="{max(2.0, px2-px1):.1f}" height="{bar_h}" fill="none" stroke="{c}" stroke-width="1.2" rx="3"/>'
                f'<rect x="{fx1:.1f}" y="{y+6}" width="{w:.1f}" height="{bar_h-12}" fill="{c}" opacity=".8" rx="2">'
                f'<title>{escape(ph.name)} · planned {_d(ps)} – {_d(pe)} · forecast {_d(fs)} – {_d(fe)}</title></rect>'
            )
            if w > len(ph.name) * 6.2 + 6:
                out.append(f'<text x="{fx1+4:.1f}" y="{y+bar_h/2+3.5:.1f}" font-size="9.5" fill="#fff">{escape(ph.name)}</text>')
        for m in b.key_milestones():
            when = m.actual or m.forecast or m.planned
            if not when or when < start:
                continue
            mx, yy, c = sc.x(when), y - 3, MS_COLOR[m.status]
            out.append(
                f'<polygon points="{mx:.1f},{yy-6} {mx+6:.1f},{yy} {mx:.1f},{yy+6} {mx-6:.1f},{yy}" fill="{c}" stroke="#fff">'
                f'<title>{escape(m.name)} · {m.get_status_display()} · planned {_d(m.planned)}'
                f'{f", forecast {_d(m.forecast)}" if m.forecast else ""}</title></polygon>'
            )
    out.append("</svg>")
    return "".join(out) + legend()


def initiative_svg(b) -> str:
    """Scaled to the initiative ±1 month; one lane per phase (planned above, forecast/actual below)."""
    dates = [clock.today()]
    for ph in b.phases:
        dates += [d for d in _phase_dates(ph) if d]
    for m in b.milestones:
        dates += [d for d in (m.planned, m.forecast, m.actual) if d]
    lo, hi = min(dates), max(dates)
    start, end = _month_start(lo, -1), _month_start(hi, 2)
    months = max(_months_between(start, end), 3)
    W, left, row_h, top, bar_h = 1100, 150, 58, 28, 16
    sc = Scale(start, months, left, W)
    ms = sorted([m for m in b.milestones if m.actual or m.forecast or m.planned],
                key=lambda m: m.actual or m.forecast or m.planned)
    ms_h = 90 if ms else 0
    H = top + len(b.phases) * row_h + ms_h
    out = [f'<svg viewBox="0 0 {W} {H+20}" width="100%">', sc.axis(top, H - top),
           sc.today_line(top, H)]

    def label(x1, x2, y, txt, col):
        if x2 - x1 > len(txt) * 5.6 + 8:
            return f'<text x="{x1+4:.1f}" y="{y+bar_h/2+3.5:.1f}" font-size="9.5" fill="#fff">{escape(txt)}</text>'
        return f'<text x="{x2+5:.1f}" y="{y+bar_h/2+3.5:.1f}" font-size="9.5" fill="{col}">{escape(txt)}</text>'

    infos = stage_infos()
    for r, ph in enumerate(b.phases):
        ps, pe, fs, fe = _phase_dates(ph)
        y = top + r * row_h + 6
        st = infos[ph.stage]
        out.append(
            f'<text x="4" y="{y+bar_h+4}" font-size="12" font-weight="600" fill="{st.color}">{escape(ph.name)}</text>'
            f'<text x="4" y="{y+bar_h+16}" font-size="9.5" fill="#666">{escape(st.name)}</text>'
            f'<line x1="{left}" y1="{y+row_h-8}" x2="{W-20}" y2="{y+row_h-8}" stroke="#f0f0f0"/>'
        )
        if ps and pe:
            p1, p2 = sc.x(ps), sc.x(pe)
            out.append(f'<rect x="{p1:.1f}" y="{y}" width="{max(2.0,p2-p1):.1f}" height="{bar_h}" fill="none" stroke="{st.color}" stroke-width="1.4" rx="3"/>')
            out.append(label(p1, p2, y, f"Planned {_short(ps)} – {_short(pe)}", "#444").replace('fill="#fff"', f'fill="{st.color}"'))
        if fs and fe:
            f1, f2 = sc.x(fs), sc.x(fe)
            slip = (fe - pe).days if pe else 0
            actual = clock.today() > fe
            txt = f"{'Actual' if actual else 'Forecast'} {_short(fs)} – {_short(fe)}" + (f" ({'+' if slip > 0 else ''}{slip}d)" if slip else "")
            out.append(f'<rect x="{f1:.1f}" y="{y+bar_h+6}" width="{max(2.0,f2-f1):.1f}" height="{bar_h}" fill="{st.color}" opacity=".85" rx="3"/>')
            out.append(label(f1, f2, y + bar_h + 6, txt, "#cf222e" if slip > 0 else "#444"))

    if ms:
        y = top + len(b.phases) * row_h + 18
        out.append(f'<text x="4" y="{y+6}" font-size="12" font-weight="600" fill="#444">Key milestones</text>'
                   f'<line x1="{left}" y1="{y+2}" x2="{W-20}" y2="{y+2}" stroke="#ccc"/>')
        last_x = [-1e9, -1e9, -1e9]
        for m in ms:
            when = m.actual or m.forecast or m.planned
            mx, c = sc.x(when), MS_COLOR[m.status]
            lvl = 0
            while lvl < 2 and mx - last_x[lvl] < 110:
                lvl += 1
            last_x[lvl] = mx
            ly = y + 22 + lvl * 22
            slipped = m.status != Milestone.Status.DONE and not m.actual and m.forecast and m.planned and m.forecast != m.planned
            if slipped:
                px = sc.x(m.planned)
                out.append(
                    f'<line x1="{px:.1f}" y1="{y+2}" x2="{mx:.1f}" y2="{y+2}" stroke="#cf222e" stroke-width="1.5"/>'
                    f'<polygon points="{px:.1f},{y-3} {px+5:.1f},{y+2} {px:.1f},{y+7} {px-5:.1f},{y+2}" fill="#fff" stroke="#cf222e">'
                    f'<title>{escape(m.name)} originally planned {_d(m.planned)}</title></polygon>'
                )
            name = m.name if len(m.name) <= 24 else m.name[:23] + "…"
            was = f" (was {_short(m.planned)})" if slipped else ""
            out.append(
                f'<polygon points="{mx:.1f},{y-5} {mx+7:.1f},{y+2} {mx:.1f},{y+9} {mx-7:.1f},{y+2}" fill="{c}" stroke="#fff">'
                f'<title>{escape(m.name)} · {m.get_status_display()}</title></polygon>'
                f'<line x1="{mx:.1f}" y1="{y+9}" x2="{mx:.1f}" y2="{ly-8}" stroke="#ccc"/>'
                f'<text x="{mx:.1f}" y="{ly}" font-size="9.5" fill="{c}" text-anchor="middle">{escape(name)}</text>'
                f'<text x="{mx:.1f}" y="{ly+10}" font-size="8.5" fill="#666" text-anchor="middle">{_short(when)}{was}</text>'
            )
    out.append("</svg>")
    return "".join(out) + legend()
