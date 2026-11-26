"""A line graph of the fastest areas' median response time, sliding one day at a time.

The video covers a range of days. Each day is the 7-day window that ends on it, so every
point is measured the way the dashboards measure (group.py figures, rows rewound to the end of
the window) and matches a dashboard for the same 7 days.

On screen, the right edge is the day being looked at and the left edge is 7 days earlier. The
graph slides continuously through the range. Only the top TOP_AREAS areas on each day are
shown: they set the scale, they are the lines, and they are the legend. The horizontal axis is
days, the vertical axis is median minutes with 0 at the top, and each area is coloured by zone.
"""
import colorsys
import hashlib
import math
import os
import subprocess
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from .district_summary import area_records, folder_label
from .engine import measure, parse_export, safe_name
from .period import end_of_day, mission_zone
from .settings import app_directory, validate_settings

# Videos cover `video_days` days of referrals (a setting, 42 by default); a day is the 7 days ending on it.
DEFAULT_VIDEO_DAYS = 42
WINDOW_DAYS = 7
# An area is ranked in a window only with this many referrals in it; fewer makes a median too noisy to compare.
MIN_REFERRALS = 3
TOP_AREAS = 10
VIEW_DAYS = 7              # the graph shows this many days: the newest day on the right edge
VIDEO_FOLDER = "Referral Dash videos"

WIDTH, HEIGHT = 1280, 720
FPS = 30
DAYS_PER_SECOND = 0.8      # how fast the view slides
START_HOLD_SECONDS = 1.0
END_HOLD_SECONDS = 1.5
SAMPLE_STEP = 0.05         # lines are drawn with a point every 1/20 of a day, so they stay smooth
BACKGROUND_MID = (7, 15, 23)
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

BACKGROUND_TOP = (7, 17, 26)
BACKGROUND_BOTTOM = (7, 13, 20)
TEXT = (246, 248, 250)
MUTED = (170, 184, 196)
GRID = (34, 48, 62)
PANEL = (14, 26, 38)

PLOT_LEFT, PLOT_RIGHT, PLOT_TOP, PLOT_BOTTOM = 120, 940, 215, 630
LEGEND_LEFT, LEGEND_ROW = 968, 46
LINE_WIDTH = 4


def video_range(end, days=DEFAULT_VIDEO_DAYS):
    """From `days` days before `end` up to `end`, as (first, last) dates: a full transfer of referrals."""
    return end - timedelta(days=days), end


def video_files(output):
    """The videos saved in `output`, newest first. Unfinished ones are hidden files and are left out."""
    folder = Path(output) / VIDEO_FOLDER
    if not folder.is_dir():
        return []
    files = [p for p in folder.glob("*.mp4") if p.is_file() and not p.name.startswith(".")]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def windows(first, last):
    """Each day's 7-day window: (first day, last day), one window per day that ends inside the range."""
    return [(first + timedelta(days=i), first + timedelta(days=i + WINDOW_DAYS - 1))
            for i in range((last - first).days - WINDOW_DAYS + 2)]


def _key(record):
    return (record["zone"], record["district"], record["area"])


def standings(kept, settings, zone, first, last, progress=lambda done, total: None):
    """One board per window: every ranked area, quickest median first."""
    frames = []
    spans = windows(first, last)
    for done, (start, end) in enumerate(spans):
        mission, _ = measure(kept, start, end, end_of_day(end, zone), zone, settings)
        ranked = [r for r in area_records(mission)
                  if r["referrals"] >= MIN_REFERRALS and r["stats"]["median_response_time"] is not None]
        ranked.sort(key=lambda r: (r["stats"]["median_response_time"], r["area"].casefold()))
        frames.append([{"key": _key(r), "median": r["stats"]["median_response_time"], "area": r["area"],
                        "district": r["district"], "zone": r["zone"], "referrals": r["referrals"]}
                       for r in ranked])
        progress(done + 1, len(spans))
    return spans, frames


def top_streaks(boards):
    """For each day: the fastest area, its median, and how many days in a row it has been the fastest.

    A day with no ranked area gives None and ends any streak.
    """
    result = []
    previous = None
    streak = 0
    for board in boards:
        if not board:
            result.append(None)
            previous, streak = None, 0
            continue
        top = board[0]
        streak = streak + 1 if top["key"] == previous else 1
        previous = top["key"]
        result.append({"key": top["key"], "area": top["area"], "district": top["district"],
                       "zone": top["zone"], "median": top["median"], "streak": streak})
    return result


def top_only(boards):
    """Keep only each day's top TOP_AREAS areas, so the scale, the lines and the legend all show those."""
    return [board[:TOP_AREAS] for board in boards]


def _positions(count):
    """The day on the right edge of the view, for every frame: hold, a steady slide, then hold."""
    last = count - 1
    start = [0.0] * round(START_HOLD_SECONDS * FPS)
    moving = []
    if last > 0:
        for step in range(1, math.ceil(last / DAYS_PER_SECOND * FPS) + 1):
            moving.append(min(float(last), step * DAYS_PER_SECOND / FPS))
    end = [float(last)] * round(END_HOLD_SECONDS * FPS)
    return start + moving + end


def frame_count(windows_count):
    return len(_positions(windows_count))


# ----- colours: worked out from names, nothing is written in per zone -----

def zone_color(zone):
    """The colour for a zone, computed from its name. The same name always gives the same colour."""
    digest = hashlib.sha256(zone.strip().casefold().encode("utf-8")).digest()
    hue = int.from_bytes(digest[:4], "big") / 2 ** 32
    red, green, blue = colorsys.hls_to_rgb(hue, 0.64, 0.8)
    return (round(red * 255), round(green * 255), round(blue * 255))


def area_color(key, zone_keys):
    """A zone's colour, lighter or darker for each of its areas, so areas in one zone stay apart."""
    base = zone_color(key[0])
    ordered = sorted(zone_keys)
    position = ordered.index(key)
    shift = (position / (len(ordered) - 1) - 0.5) * 0.4 if len(ordered) > 1 else 0.0
    return _mix(base, (255, 255, 255) if shift > 0 else (0, 0, 0), abs(shift))


# ----- drawing -----

@lru_cache(maxsize=None)
def _font(size, bold=False, display=False):
    name = "CalSans-Regular.ttf" if display else ("Nunito-Bold.ttf" if bold else "Nunito-Regular.ttf")
    return ImageFont.truetype(str(app_directory() / "fonts" / name), size)


def _fit(draw, text, size, width, **style):
    while size > 12 and draw.textbbox((0, 0), text, font=_font(size, **style))[2] > width:
        size -= 1
    return _font(size, **style)


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _background():
    strip = Image.new("RGB", (1, HEIGHT))
    strip.putdata([_mix(BACKGROUND_TOP, BACKGROUND_BOTTOM, y / (HEIGHT - 1)) for y in range(HEIGHT)])
    return strip.resize((WIDTH, HEIGHT))


def _nice_step(raw):
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        if multiple * magnitude >= raw:
            return multiple * magnitude
    return 10 * magnitude


def _x(day, position):
    """Horizontal place of a day: the right edge is `position`, the left edge is VIEW_DAYS earlier."""
    return PLOT_LEFT + (day - (position - VIEW_DAYS)) / VIEW_DAYS * (PLOT_RIGHT - PLOT_LEFT)


def _y(value, scale):
    """0 is at the top of the plot; larger medians sit lower."""
    return PLOT_TOP + value / scale * (PLOT_BOTTOM - PLOT_TOP)


MOVE_SHARE = 0.6   # each day, the legend moves in the first 60% of the way to the next day, then holds still


def _track(boards, key):
    """Day by day, where an area is.

    A day with no referrals for the area in that window is `ranked` False: it has no point, so the
    line stops there and the legend row leaves the list. Ranked but not in the top areas puts the
    line at the bottom of the graph.
    """
    raw = []
    for board in boards:
        index = next((i for i, entry in enumerate(board) if entry["key"] == key), None)
        raw.append(None if index is None else
                   {"median": board[index]["median"], "rank": min(index, TOP_AREAS),
                    "top": index < TOP_AREAS, "ranked": True})
    known = [i for i, day in enumerate(raw) if day is not None]
    track = []
    for i, day in enumerate(raw):
        if day is None:
            # No median is set for an empty window. The nearest real median is only kept as the legend's label.
            near = min(known, key=lambda k: abs(k - i))
            day = {"median": raw[near]["median"], "rank": TOP_AREAS, "top": False, "ranked": False}
        track.append(day)
    return track


def _ease_out(t):
    """Starts steep and settles smoothly into place."""
    return 1 - (1 - t) ** 3


def _lerp(a, b, t):
    return a + (b - a) * t


def _line_height(track, x, scale):
    """Where an area's line is at a fractional day, as a height in pixels. None where it has no point."""
    low = math.floor(x)
    frac = x - low

    def place(index):
        day = track[index]
        if not day["ranked"]:
            return None
        return _y(day["median"], scale) if day["top"] else PLOT_BOTTOM

    if frac == 0 or low + 1 >= len(track):
        return place(min(max(low, 0), len(track) - 1))
    start, end = place(low), place(low + 1)
    if start is None or end is None:
        return None
    return _lerp(start, end, frac)


def _legend_row(track, x):
    """Where an area's legend row is at a fractional day: (rank, median). A rank of TOP_AREAS is off the list.

    The row moves to its next place in the first part of the day, then holds still for the rest of it.
    """
    low = math.floor(x)
    frac = x - low
    if frac == 0 or low + 1 >= len(track):
        day = track[min(max(low, 0), len(track) - 1)]
        return day["rank"], day["median"]
    start, end = track[low], track[low + 1]
    eased = _ease_out(min(1.0, frac / MOVE_SHARE))
    return _lerp(start["rank"], end["rank"], eased), _lerp(start["median"], end["median"], eased)


def _trail(track, start, end, scale):
    """Samples of an area's line from `start` to `end` as (day, height or None)."""
    steps = max(1, math.ceil((end - start) / SAMPLE_STEP))
    samples = []
    for i in range(steps + 1):
        day = start + (end - start) * i / steps
        samples.append((day, _line_height(track, day, scale)))
    return samples


def _draw_frame(ctx, position):
    image = ctx["background"].copy()
    draw = ImageDraw.Draw(image)
    count, scale, series = ctx["count"], ctx["scale"], ctx["series"]
    ends = [end for _, end in ctx["windows"]]
    day = min(count - 1, max(0, round(position)))

    # Header: the range being shown, the fastest area, and the day on the right edge.
    draw.text((WIDTH - 48, 36), "Fastest median response times", font=_font(28, display=True), fill=TEXT, anchor="ra")
    draw.text((WIDTH - 48, 74), ctx["range_label"], font=_font(20), fill=MUTED, anchor="ra")
    shown = ends[day]
    draw.text((WIDTH - 48, 130), f"{shown:%b} {shown.day}, {shown.year}", font=_font(56, display=True),
              fill=TEXT, anchor="ra")
    top = ctx["tops"][day]
    if top:
        draw.text((48, 36), top["area"], font=_fit(draw, top["area"], 44, 560, display=True), fill=TEXT, anchor="la")
        days_word = "day" if top["streak"] == 1 else "days"
        draw.text((48, 98), f"Fastest for {top['streak']} {days_word}", font=_font(24, bold=True), fill=TEXT, anchor="la")
        draw.text((48, 134), f"Median {top['median']:.1f} min · {top['district']} · {top['zone']}",
                  font=_font(19), fill=MUTED, anchor="la")
    else:
        draw.text((48, 36), "No area ranked this day", font=_font(32, display=True), fill=TEXT, anchor="la")

    # Vertical grid: one line per day in view, labelled with its date.
    for shown_day in range(max(0, math.ceil(position - VIEW_DAYS)), min(count - 1, math.floor(position)) + 1):
        x = _x(shown_day, position)
        draw.line((x, PLOT_TOP, x, PLOT_BOTTOM), fill=GRID, width=1)
        label_day = ends[shown_day]
        draw.text((x, PLOT_BOTTOM + 12), f"{label_day:%b} {label_day.day}", font=_font(15), fill=MUTED, anchor="ma")

    # Horizontal grid: median minutes, with 0 at the top.
    step = _nice_step(scale / 5)
    value = 0.0
    while value <= scale:
        y = _y(value, scale)
        draw.line((PLOT_LEFT, y, PLOT_RIGHT, y), fill=GRID, width=1)
        draw.text((PLOT_LEFT - 12, y), f"{value:g}", font=_font(16), fill=MUTED, anchor="rm")
        value += step
    draw.text((PLOT_LEFT, PLOT_TOP - 12), "median minutes · 0 at top", font=_font(16), fill=MUTED, anchor="ls")

    # Lines: every area in view, drawn solid. A day with no referrals leaves a gap; off the top areas a line drops to the bottom.
    left = max(0.0, position - VIEW_DAYS)
    for line in series:
        samples = _trail(line["track"], left, position, scale)
        for (d0, y0), (d1, y1) in zip(samples, samples[1:]):
            if y0 is None or y1 is None:
                continue
            draw.line((_x(d0, position), y0, _x(d1, position), y1), fill=line["color"], width=LINE_WIDTH)
        head = _line_height(line["track"], position, scale)
        if head is not None:
            x = _x(position, position)
            draw.ellipse((x - 6, head - 6, x + 6, head + 6), fill=line["color"])

    # Legend: the top areas, moving into their places and holding there. Rows off the list are not drawn.
    for line in series:
        rank, median = _legend_row(line["track"], position)
        if round(rank) >= TOP_AREAS:  # the row's number is its rounded rank, so the list and the labels agree
            continue
        y = PLOT_TOP + rank * LEGEND_ROW
        bottom = y + LEGEND_ROW - 6
        draw.rounded_rectangle((LEGEND_LEFT, y, WIDTH - 36, bottom), 8, fill=PANEL, outline=line["color"], width=3)
        centre = (y + bottom) / 2
        draw.text((LEGEND_LEFT + 12, centre), str(round(rank) + 1), font=_font(18, bold=True), fill=line["color"], anchor="lm")
        draw.text((LEGEND_LEFT + 40, centre), line["area"], font=_fit(draw, line["area"], 19, 170, bold=True),
                  fill=line["color"], anchor="lm")
        draw.text((WIDTH - 46, centre), f"{median:.0f}", font=_font(17), fill=TEXT, anchor="rm")

    draw.text((48, HEIGHT - 22), "Median minutes from referral to first contact · each day is the 7 days ending on it · "
              f"top {TOP_AREAS} areas, at least {MIN_REFERRALS} referrals", font=_font(15), fill=MUTED, anchor="ls")
    return image


def _encode(path, frames, progress):
    """Pipe raw frames into ffmpeg (bundled with the app through imageio-ffmpeg)."""
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
               "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "22", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(path)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    try:
        for number, image in enumerate(frames, 1):
            process.stdin.write(image.tobytes())
            if number % 30 == 0:
                progress("Drawing frames...", number, None)
    except BrokenPipeError:
        pass  # ffmpeg stopped early; its error explains why below.
    finally:
        process.stdin.close()
        error = process.stderr.read().decode("utf-8", "replace")
        process.stderr.close()
        code = process.wait()
    if code != 0 or not path.is_file():
        raise ValueError("The video could not be made. " + (error.strip().splitlines() or ["ffmpeg stopped."])[-1])


def make_video(filename, output_parent, settings, end, progress=lambda message, done=None, total=None: None,
               now=None, first=None):
    """Make the line graph from `first` to `end`, or the VIDEO_DAYS days ending on `end` by default.

    Referrals dated outside that range are left out. Returns (path, facts).
    """
    settings = validate_settings(settings)
    zone = mission_zone(settings)
    if first is None:
        first, last = video_range(end, settings["video_days"])
    else:
        last = end
    kept, skipped, _ = parse_export(filename, settings)
    in_range = [day for _, day in kept if first <= day <= last]
    if not in_range:
        raise ValueError(f"This export has no referrals from {first:%b %d, %Y} to {last:%b %d, %Y}. "
                         "Check the dates, or download a file that covers them.")
    spans, boards = standings(kept, settings, zone, first, last,
                              lambda done, total: progress(f"Measuring day {done} of {total}...", done, total))
    if not any(boards):
        raise ValueError(f"No area has at least {MIN_REFERRALS} referrals in any {WINDOW_DAYS}-day window of this "
                         "range, so there is nothing to rank.")

    keys = sorted({entry["key"] for board in boards for entry in board})
    top_keys = {entry["key"] for board in top_only(boards) for entry in board}
    zone_keys = {}
    for key in keys:
        zone_keys.setdefault(key[0], []).append(key)
    series = [{"key": key, "area": key[2], "track": _track(boards, key),
               "color": area_color(key, zone_keys[key[0]])} for key in keys]

    # One scale for the whole video, set by the top areas only.
    scale = max(entry["median"] for board in top_only(boards) for entry in board) * 1.05
    tops = top_streaks(boards)
    ctx = {"background": _background(), "count": len(boards), "scale": scale, "series": series,
           "tops": tops, "windows": spans,
           "range_label": f"{first:%b} {first.day} – {last:%b} {last.day}, {last.year}"}

    def images():
        for position in _positions(len(boards)):
            yield _draw_frame(ctx, position)

    folder = Path(output_parent) / VIDEO_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_name(f"Fastest response - {folder_label(first.isoformat(), last.isoformat())}")
    final = folder / (name + ".mp4")
    copy = 2
    while final.exists():
        final = folder / f"{name} ({copy}).mp4"
        copy += 1
    building = folder / (".building_" + final.name)
    try:
        _encode(building, images(), progress)
        building.replace(final)
    finally:
        building.unlink(missing_ok=True)
    facts = {"windows": len(boards), "frames": frame_count(len(boards)), "areas_ranked": len(top_keys),
             "referrals": len(in_range), "first_day": first.isoformat(), "last_day": last.isoformat(),
             "skipped_rows": skipped}
    return final, facts
