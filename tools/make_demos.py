"""Generate looping stick-figure demo GIFs for every exercise (side view, original artwork, no licensing issues).
Run: python tools/make_demos.py  -> static/demos/<id>.gif
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static" / "demos"
W, H, S = 320, 260, 3          # final size, supersample factor
BASE = 230                      # screen_y = BASE - y   (y-up world, ground at y=0)
TORSO, NECK, HEAD = 62, 8, 12
UA, FA, TH, SH = 34, 32, 44, 42
BG = (17, 24, 39)
NEAR = (249, 115, 22)
FAR = (154, 72, 30)
GROUND = (55, 65, 81)
PROP = (107, 114, 128)
PROP_FAR = (75, 85, 99)


def d(a):
    r = math.radians(a)
    return math.cos(r), math.sin(r)


def add(p, a, l):
    c, s = d(a)
    return p[0] + l * c, p[1] + l * s


def ik(root, target, a, b, bend):
    dx, dy = target[0] - root[0], target[1] - root[1]
    dist = max(1e-3, min(math.hypot(dx, dy), a + b - 0.01))
    base = math.degrees(math.atan2(dy, dx))
    cosv = (a * a + dist * dist - b * b) / (2 * a * dist)
    off = math.degrees(math.acos(max(-1, min(1, cosv))))
    mid = add(root, base + bend * off, a)
    c, s = d(base)
    end = (root[0] + dist * c, root[1] + dist * s)
    return mid, end


def limb(root, spec, a, b):
    if spec[0] == "ik":
        return ik(root, spec[1], a, b, spec[2])
    mid = add(root, spec[1], a)
    return mid, add(mid, spec[2], b)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_spec(s1, s2, t):
    if s1[0] == "ik":
        return ("ik", (lerp(s1[1][0], s2[1][0], t), lerp(s1[1][1], s2[1][1], t)), s1[2])
    return ("ang", lerp(s1[1], s2[1], t), lerp(s1[2], s2[2], t))


def lerp_pose(p1, p2, t):
    out = {"hip": (lerp(p1["hip"][0], p2["hip"][0], t), lerp(p1["hip"][1], p2["hip"][1], t)),
           "torso": lerp(p1["torso"], p2["torso"], t)}
    for k in ("na", "fa", "nl", "fl"):
        out[k] = lerp_spec(p1[k], p2[k], t)
    return out


def ease(t):
    return 0.5 - 0.5 * math.cos(math.pi * t)


def draw_frame(pose, props, ground_y=0):
    img = Image.new("RGB", (W * S, H * S), BG)
    g = ImageDraw.Draw(img)

    def P(p):
        return (p[0] * S, (BASE - p[1]) * S)

    def line(a, b, col, w):
        g.line([P(a), P(b)], fill=col, width=int(w * S))
        r = w * S / 2
        for q in (a, b):
            x, y = P(q)
            g.ellipse([x - r, y - r, x + r, y + r], fill=col)

    for pr in props:
        kind = pr[0]
        if kind == "bar":
            line((pr[1], pr[3]), (pr[2], pr[3]), PROP, 5)
            line((pr[1], pr[3]), (pr[1], ground_y), PROP_FAR, 4)
            line((pr[2], pr[3]), (pr[2], ground_y), PROP_FAR, 4)
        elif kind == "table":
            x1, x2, y = pr[1], pr[2], pr[3]
            line((x1 + 8, y), (x1 + 8, ground_y), PROP_FAR, 4)
            line((x2 - 8, y), (x2 - 8, ground_y), PROP_FAR, 4)
            line((x1, y), (x2, y), PROP, 7)
        elif kind == "wall":
            g.rectangle([P((pr[1] - 14, 225)), P((pr[1], ground_y))], fill=(31, 41, 55))
            line((pr[1], ground_y), (pr[1], 225), PROP, 3)
    line((0, ground_y), (W, ground_y), GROUND, 4)

    hip = pose["hip"]
    sh = add(hip, pose["torso"], TORSO)
    head = add(sh, pose["torso"], NECK + HEAD)
    # far side first
    k, a = limb(hip, pose["fl"], TH, SH)
    line(hip, k, FAR, 9); line(k, a, FAR, 8)
    e, h = limb(sh, pose["fa"], UA, FA)
    line(sh, e, FAR, 8); line(e, h, FAR, 7)
    line(hip, sh, NEAR, 12)
    x, y = P(head)
    r = HEAD * S
    g.ellipse([x - r, y - r, x + r, y + r], fill=NEAR)
    k, a = limb(hip, pose["nl"], TH, SH)
    line(hip, k, NEAR, 10); line(k, a, NEAR, 9)
    e, h = limb(sh, pose["na"], UA, FA)
    line(sh, e, NEAR, 9); line(e, h, NEAR, 8)
    for pr in props:   # bar / table top drawn over the hands
        if pr[0] == "bar":
            line((pr[1], pr[3]), (pr[2], pr[3]), PROP, 5)
    return img.resize((W, H), Image.LANCZOS)


def make(name, a, b, props=(), ground_y=0, hold_a=5, hold_b=4, move=11, ms=70):
    rgb = []
    seq = [0.0] * hold_a + [ease(i / move) for i in range(1, move)] + [1.0] * hold_b + \
          [ease(1 - i / move) for i in range(1, move)]
    for t in seq:
        rgb.append(draw_frame(a(t) if callable(a) else lerp_pose(a, b, t), props, ground_y))
    box = None
    for im in rgb:
        diff = ImageChops.difference(im, Image.new("RGB", im.size, BG)).convert("L").point(lambda v: 255 if v > 12 else 0).getbbox()
        if diff:
            box = diff if box is None else (min(box[0], diff[0]), min(box[1], diff[1]), max(box[2], diff[2]), max(box[3], diff[3]))
    top = max(0, box[1] - 18)
    bottom = min(H, box[3] + 10)
    if bottom - top < 150:
        top = max(0, bottom - 150)
    frames = [im.crop((0, top, W, bottom)).convert("P", palette=Image.ADAPTIVE, colors=64) for im in rgb]
    OUT.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUT / f"{name}.gif", save_all=True, append_images=frames[1:], duration=ms, loop=0,
                   optimize=True, disposal=2)
    frames[len(frames) // 2 if hold_b else 0].convert("RGB").save(OUT / f"{name}.png")


ARMS_DOWN = ("ang", -93, -90)
ARMS_DOWN_F = ("ang", -88, -86)

# Push-up: feet fixed, hands fixed, body straight.
def pushup():
    ank = (70, 4)
    def pose(angle):
        hip = add(ank, angle, TH + SH)
        return {"hip": hip, "torso": angle, "nl": ("ik", ank, 1), "fl": ("ik", (ank[0] - 4, 4), 1),
                "na": ("ik", (207, 4), -1), "fa": ("ik", (200, 4), -1)}
    make("pushups", lambda t: pose(lerp(26.5, 5.5, t)), None, ms=75)


def squat():
    a = {"hip": (165, 89), "torso": 90, "nl": ("ik", (165, 4), 1), "fl": ("ik", (159, 4), 1),
         "na": ARMS_DOWN, "fa": ARMS_DOWN_F}
    b = {"hip": (139, 45), "torso": 52, "nl": ("ik", (165, 4), 1), "fl": ("ik", (159, 4), 1),
         "na": ("ang", 6, 4), "fa": ("ang", 10, 8)}
    make("squats", a, b, ms=80)


def lunge():
    hands = ("ang", -122, -62)
    a = {"hip": (158, 80), "torso": 90, "nl": ("ik", (205, 4), 1), "fl": ("ik", (108, 6), 1),
         "na": hands, "fa": ("ang", -118, -60)}
    b = {"hip": (155, 47), "torso": 90, "nl": ("ik", (205, 4), 1), "fl": ("ik", (108, 6), 1),
         "na": hands, "fa": ("ang", -118, -60)}
    make("lunges", a, b, ms=80)


def row():
    heel = (40, 4)
    def pose(angle):
        return {"hip": add(heel, angle, TH + SH), "torso": angle, "nl": ("ik", heel, 1), "fl": ("ik", (44, 4), 1),
                "na": ("ik", (186, 90), 1), "fa": ("ik", (182, 90), 1)}
    make("rows", lambda t: pose(lerp(7.5, 26.0, t)), None, props=[("table", 160, 300, 90)], ms=80)


def bridge():
    sh = (90, 10)
    def pose(angle):   # angle from shoulder to hip
        hip = add(sh, angle, TORSO)
        return {"hip": hip, "torso": angle + 180, "nl": ("ik", (200, 4), 1), "fl": ("ik", (194, 4), 1),
                "na": ("ang", -3, 0), "fa": ("ang", -6, -2)}
    make("bridges", lambda t: pose(lerp(-1.0, 34.0, t)), None, ms=80)


def plank():
    ank = (60, 4)
    def pose(angle):
        return {"hip": add(ank, angle, TH + SH), "torso": angle, "nl": ("ik", ank, 1), "fl": ("ik", (56, 4), 1),
                "na": ("ang", -90, 0), "fa": ("ang", -92, 2)}
    make("plank", lambda t: pose(lerp(13.2, 14.2, t)), None, hold_a=6, hold_b=6, move=14, ms=90)


def hang():
    a = {"hip": (160, 84), "torso": 90, "nl": ("ang", -86, -96), "fl": ("ang", -92, -100),
         "na": ("ik", (168, 214), -1), "fa": ("ik", (152, 214), 1)}
    b = {"hip": (163, 82), "torso": 91.5, "nl": ("ang", -84, -94), "fl": ("ang", -90, -98),
         "na": ("ik", (168, 214), -1), "fa": ("ik", (152, 214), 1)}
    make("hang", a, b, props=[("bar", 100, 230, 214)], ground_y=-24, hold_a=6, hold_b=6, move=16, ms=90)


def wall():
    a = {"hip": (134, 89), "torso": 90, "nl": ("ik", (138, 4), 1), "fl": ("ik", (134, 4), 1),
         "na": ("ang", -91, -90), "fa": ("ang", -90, -89)}
    b = {"hip": (134, 89.5), "torso": 90, "nl": ("ik", (138, 4), 1), "fl": ("ik", (134, 4), 1),
         "na": ("ang", -91, -90), "fa": ("ang", -90, -89)}
    make("wall", a, b, props=[("wall", 121)], hold_a=8, hold_b=8, move=12, ms=100)


if __name__ == "__main__":
    for f in (pushup, squat, lunge, row, bridge, plank, hang, wall):
        f()
    print("ok", sorted(p.name for p in OUT.iterdir()))
