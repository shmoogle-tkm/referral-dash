"""The two dials' scales and needles, drawn from the goals so the needle points at the real number.

Response time: the speed goal sits at the top. The left half counts up evenly to the goal; the
right half doubles at each mark (2x, 4x, 8x, 16x, 32x the goal).
% found: 0 to 10, each mark 10%.
On both, a light blue line marks the stretch where the goal is met, so it means the same thing on each.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

SWEEP = 122          # Degrees either side of straight up, matching the dial art.
RING = (130, 216)    # The band between the inner circle and the rim that the scale occupies.
LABEL_RADIUS = 172
TICK_OUTER = 214
# One colour, one meaning on both dials: the stretch where the goal is met.
GOAL = (110, 200, 255)  # A light blue that matches the dials' inner ring.
TICK_BLUE = (43, 160, 255)
NEEDLE = (255, 47, 115)


def _number(value):
    return f"{value:g}" if value != int(value) else str(int(value))


def _nice_step(goal):
    for step in (0.5, 1, 2, 5, 10, 15, 20, 30, 60, 120):
        if step >= goal / 6:
            return step
    return goal / 5


def speed_scale(goal):
    """Labels as (fraction of the sweep, text) for a response-time dial with the goal at the top."""
    goal = max(float(goal), 1.0)
    step = _nice_step(goal)
    labels, value = [], 0.0
    while value < goal - 0.6 * step:
        labels.append((0.5 * value / goal, _number(value)))
        value += step
    labels.append((0.5, _number(goal)))
    for k in range(1, 6):
        labels.append((0.5 + 0.1 * k, _number(goal * 2 ** k) + ("+" if k == 5 else "")))
    return labels


def speed_fraction(minutes, goal):
    goal = max(float(goal), 1.0)
    if minutes is None:
        return None
    if minutes <= goal:
        return 0.5 * max(minutes, 0) / goal
    return 0.5 + 0.5 * min(math.log2(minutes / goal) / 5, 1.0)


def found_scale():
    return [(i / 10, str(i)) for i in range(11)]


def found_fraction(percent):
    return None if percent is None else min(max(percent, 0), 100) / 100


def _angle(fraction):
    return math.radians(-SWEEP + 2 * SWEEP * fraction)


def _point(center, radius, fraction):
    a = _angle(fraction)
    return center[0] + radius * math.sin(a), center[1] - radius * math.cos(a)


def _arc(draw, center, radius, width, start, end, color):
    box = (center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius)
    # PIL measures from 3 o'clock, clockwise; the sweep is measured from 12 o'clock.
    draw.arc(box, -90 - SWEEP + 2 * SWEEP * start, -90 - SWEEP + 2 * SWEEP * end, fill=color, width=width)


def draw_dial(img, center, labels, zones, needle, font):
    """Zones as (start, end, color) fractions, a needle fraction or None, on an RGBA image."""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    for start, end, color in zones:
        _arc(g, center, TICK_OUTER - 2, 9, start, end, color + (170,))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5)))
    draw = ImageDraw.Draw(img)
    for start, end, color in zones:
        _arc(draw, center, TICK_OUTER - 2, 4, start, end, color + (255,))

    marks = [f for f, _ in labels]
    for f, _ in labels:
        draw.line([_point(center, 194, f), _point(center, TICK_OUTER, f)], fill=(255, 255, 255, 255), width=3)
    for a, b in zip(marks, marks[1:]):
        middle = (a + b) / 2
        draw.line([_point(center, 203, middle), _point(center, TICK_OUTER, middle)], fill=TICK_BLUE + (255,), width=2)

    if needle is not None:
        a = _angle(needle)
        side = (math.cos(a), math.sin(a))  # Perpendicular to the needle.
        base, tip = _point(center, RING[0] + 2, needle), _point(center, TICK_OUTER + 2, needle)
        shape = [(base[0] - 4 * side[0], base[1] - 4 * side[1]), (base[0] + 4 * side[0], base[1] + 4 * side[1]),
                 (tip[0] + 1 * side[0], tip[1] + 1 * side[1]), (tip[0] - 1 * side[0], tip[1] - 1 * side[1])]
        shine = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shine).polygon(shape, fill=NEEDLE + (210,))
        img.alpha_composite(shine.filter(ImageFilter.GaussianBlur(6)))
        ImageDraw.Draw(img).polygon(shape, fill=NEEDLE + (255,))

    # Numbers go on top, outlined, so the needle never hides the one it points at.
    draw = ImageDraw.Draw(img)
    for f, text in labels:
        x, y = _point(center, LABEL_RADIUS, f)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=3)
        draw.text((x - (box[0] + box[2]) / 2, y - (box[1] + box[3]) / 2), text, font=font, fill=(255, 255, 255, 255),
                  stroke_width=3, stroke_fill=(6, 9, 14, 255))
