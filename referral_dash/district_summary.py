"""Area-by-area district summaries in the Referral Care Data Hub style."""
import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .settings import app_directory
from .template import save_picture

METRICS = [("CONTACT ATTEMPTS", "percent_contacted", "%", False),
           ("SUCCESSFUL CONTACTS", "percent_successful_contact", "%", False),
           ("MEDIAN RESPONSE TIME", "median_response_time", " min", True),
           ("FOUND TAUGHT %", "percent_found", "%", False),
           ("FOUND AT CHURCH %", "percent_attended_church", "%", False)]
ATTRIBUTES = [m[1] for m in METRICS]

WIDTH = 1600
HEADER_HEIGHT = 174
ROW_HEIGHT = 148
ROW_GAP = 18
BOTTOM_PADDING = 46
AREA_WIDTH = 420
PAGE_PADDING = 34
METRIC_GAP = 14

BACKGROUND_TOP = (7, 17, 26)
BACKGROUND_BOTTOM = (7, 13, 20)
ROW_LEFT = (18, 34, 47)
ROW_RIGHT = (13, 27, 38)
LINE = (38, 60, 76)
DIVIDER = (39, 56, 70)
TEXT = (246, 248, 250)
HEADING = (219, 226, 232)
MUTED = (170, 184, 196)
ACCENT = (255, 75, 75)
GOOD = (69, 242, 139)
BAD = (255, 83, 83)


def area_records(mission):
    return [{"zone": zone.name, "district": district.name, "area": area.name,
             "stats": {attr: getattr(area, attr) for attr in ATTRIBUTES},
             "referrals": area.total_referrals}
            for zone in mission.zones.values() for district in zone.districts.values()
            for area in district.areas.values()]


def key(record):
    return (record["zone"], record["district"], record["area"])


@lru_cache(maxsize=None)
def _font(size, bold=False, serif=False):
    if serif:
        # Georgia ships with every Windows install; fall back to the bundled sans.
        system = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / ("georgiab.ttf" if bold else "georgia.ttf")
        if system.exists():
            return ImageFont.truetype(str(system), size)
    bundled = app_directory() / "fonts" / ("Nunito-Bold.ttf" if bold else "Nunito-Regular.ttf")
    return ImageFont.truetype(str(bundled), size)


def _fit(draw, value, size, width, **style):
    while draw.textbbox((0, 0), value, font=_font(size, **style))[2] > width and size > 12:
        size -= 1
    return _font(size, **style)


def _text(draw, xy, value, size, color=TEXT, anchor="la", **style):
    draw.text(xy, str(value), font=_font(size, **style), fill=color, anchor=anchor)


@lru_cache(maxsize=16)
def _gradient_row(width, height, radius):
    strip = Image.new("RGB", (width, 1))
    strip.putdata([tuple(round(ROW_LEFT[c] + (ROW_RIGHT[c] - ROW_LEFT[c]) * x / max(width - 1, 1)) for c in range(3))
                   for x in range(width)])
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius, fill=255)
    return strip.resize((width, height)), mask


@lru_cache(maxsize=8)
def _red_wash(width, height):
    strip = Image.new("RGBA", (width, 1))
    strip.putdata([(123, 30, 32, round(70 * (1 - x / max(width - 1, 1)))) for x in range(width)])
    return strip.resize((width, height))


def _background(height):
    strip = Image.new("RGB", (1, height))
    strip.putdata([tuple(round(BACKGROUND_TOP[c] + (BACKGROUND_BOTTOM[c] - BACKGROUND_TOP[c]) * y / max(height - 1, 1))
                         for c in range(3)) for y in range(height)])
    return strip.resize((WIDTH, height))


def _format_value(value, suffix):
    if value is None:
        return "—"
    rounded = round(value, 1)
    return (str(int(rounded)) if float(rounded).is_integer() else f"{rounded:.1f}") + suffix


def percent_change(current, previous):
    if current is None or previous is None:
        return None
    if previous == 0:
        return 0.0 if current == 0 else 100.0
    return (current - previous) / abs(previous) * 100


def trend(change, lower_is_better, prior_available=True):
    """Return (label, color, points_up); points_up is None when no arrow is drawn."""
    if change is None:
        return ("—" if prior_available else "No prior data"), MUTED, None
    if abs(change) < 0.05:
        return "— 0.0%", MUTED, None
    improving = change < 0 if lower_is_better else change > 0
    return f"{abs(change):.1f}%", GOOD if improving else BAD, change > 0


def _draw_trend(draw, center_x, y, change, lower_is_better, prior_available):
    label, color, points_up = trend(change, lower_is_better, prior_available)
    if points_up is None:
        _text(draw, (center_x, y), label, 16, color, "mm", bold=True)
        return
    font = _font(16, bold=True)
    arrow, gap = 12, 7
    left = center_x - (arrow + gap + draw.textbbox((0, 0), label, font=font)[2]) / 2
    middle = left + arrow / 2
    tip, base = (y - 8, y + 4) if points_up else (y + 8, y - 4)
    draw.polygon([(middle, tip), (middle - 6, base), (middle + 6, base)], fill=color)
    draw.text((left + arrow + gap, y), label, font=font, fill=color, anchor="lm")


def period_label(first_day, last_day):
    def short(day):
        return date.fromisoformat(day).strftime("%b %d, %Y").replace(" 0", " ")
    return f"{short(first_day)} – {short(last_day)}"


def render_summary(zone, district, records, previous, period, output):
    current = {key(r): r for r in records}
    old = {key(r): r for r in previous["areas"]} if previous else {}
    identities = sorted({k for k in (*current, *old) if k[:2] == (zone, district)}, key=lambda k: k[2].casefold())
    height = max(520, HEADER_HEIGHT + len(identities) * (ROW_HEIGHT + ROW_GAP) - ROW_GAP + BOTTOM_PADDING)
    image = _background(height)
    draw = ImageDraw.Draw(image)

    draw.rectangle((PAGE_PADDING, 44, PAGE_PADDING + 6, 111), fill=ACCENT)
    title = district if district.casefold().endswith("district") else district + " District"
    draw.text((PAGE_PADDING + 34, 55), title, font=_fit(draw, title, 34, 1180, serif=True), fill=TEXT)
    _text(draw, (PAGE_PADDING + 34, 99), f"{zone.upper()} ZONE", 11, MUTED)
    draw.rounded_rectangle((1278, 48, 1564, 102), 9, fill=(17, 27, 37), outline=(50, 64, 78), width=1)
    draw.text((1421, 75), period, font=_fit(draw, period, 14, 262), fill=(216, 222, 229), anchor="mm")

    metric_width = (WIDTH - PAGE_PADDING * 2 - AREA_WIDTH - METRIC_GAP * len(METRICS)) / len(METRICS)
    _text(draw, (PAGE_PADDING + 18, 150), "AREA", 12, HEADING, bold=True)
    for index, (label, _, _, _) in enumerate(METRICS):
        center = PAGE_PADDING + AREA_WIDTH + METRIC_GAP + index * (metric_width + METRIC_GAP) + metric_width / 2
        _text(draw, (center, 150), label, 11, HEADING, "mm", bold=True)

    for row, identity in enumerate(identities):
        top = HEADER_HEIGHT + row * (ROW_HEIGHT + ROW_GAP)
        bottom = top + ROW_HEIGHT
        layer, mask = _gradient_row(WIDTH - PAGE_PADDING * 2, ROW_HEIGHT, 12)
        image.paste(layer, (PAGE_PADDING, top), mask)
        wash = _red_wash(AREA_WIDTH - 4, ROW_HEIGHT - 2)
        image.paste(wash, (PAGE_PADDING + 4, top + 1), wash)
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((PAGE_PADDING, top, WIDTH - PAGE_PADDING, bottom), 12, outline=LINE, width=1)
        draw.rounded_rectangle((PAGE_PADDING, top, PAGE_PADDING + 4, bottom), 2, fill=ACCENT)
        name = identity[2]
        draw.text((PAGE_PADDING + 26, (top + bottom) / 2), name,
                  font=_fit(draw, name, 27, AREA_WIDTH - 46, bold=True, serif=True), fill=TEXT, anchor="lm")

        record, prior = current.get(identity), old.get(identity)
        for index, (_, attribute, suffix, lower_is_better) in enumerate(METRICS):
            left = PAGE_PADDING + AREA_WIDTH + METRIC_GAP + index * (metric_width + METRIC_GAP)
            center = left + metric_width / 2
            draw.line((left - METRIC_GAP / 2, top + 20, left - METRIC_GAP / 2, bottom - 20), fill=DIVIDER, width=1)
            # An area absent from a run had no activity, except response time, which is then unknown.
            value = record["stats"][attribute] if record else (None if lower_is_better else 0)
            earlier = prior["stats"][attribute] if prior else (None if lower_is_better or previous is None else 0)
            label = _format_value(value, suffix)
            draw.text((center, top + 59), label, font=_fit(draw, label, 29, metric_width - 16, bold=True),
                      fill=TEXT, anchor="mm")
            _draw_trend(draw, center, top + 103, percent_change(value, earlier), lower_is_better, previous is not None)

    save_picture(image, output)
