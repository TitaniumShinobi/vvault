import hashlib
import math
import os
import io
import base64
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont


CANVAS_SIZE = 1563
CENTER = CANVAS_SIZE // 2
CIPHER_PHRASES = [
    "VEK UREN TA",
    "NYESH TORAN VAL",
    "ZAV'ARUN TAHN'KEL VARRA",
]

SYMBOL_CHARS = [
    "\u2720", "\u2721", "\u2638", "\u2602",
    "\u2744", "\u2727", "\u2726", "\u2736", "\u273A", "\u2742",
    "\u2741", "\u2740", "\u2743", "\u2749", "\u274B", "\u2756",
    "\u2766", "\u2767", "\u2619", "\u2618", "\u263C", "\u263A",
    "\u2660", "\u2663", "\u2665", "\u2666", "\u269A", "\u269B",
    "\u2694", "\u2696", "\u26A0", "\u2625", "\u2628", "\u2622",
    "\u2623", "\u26B2",
]


def _generate_number_rows(callsign, timestamp=None):
    if timestamp is None:
        timestamp = datetime.now().isoformat()
    rows = []
    for i in range(3):
        seed = f"{callsign}:{timestamp}:row{i}:vvault_codex_seal"
        h = hashlib.sha512(seed.encode('utf-8')).hexdigest()
        numeric = ''.join(c for c in h if c.isdigit())
        while len(numeric) < 80:
            extra_seed = f"{seed}:extend:{len(numeric)}"
            extra_h = hashlib.sha512(extra_seed.encode('utf-8')).hexdigest()
            numeric += ''.join(c for c in extra_h if c.isdigit())
        rows.append(numeric[:80])
    return rows


def _hex_to_rgba(hex_color, alpha=255):
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (r, g, b, alpha)


def _color_palette(base_hex):
    r, g, b, _ = _hex_to_rgba(base_hex)
    lr = min(255, r + 90)
    lg = min(255, g + 90)
    lb = min(255, b + 90)
    dr = max(0, r - 30)
    dg = max(0, g - 30)
    db = max(0, b - 30)
    return {
        'base': (r, g, b, 255),
        'light': (lr, lg, lb, 255),
        'dark': (dr, dg, db, 255),
        'mid': ((r + lr) // 2, (g + lg) // 2, (b + lb) // 2, 255),
        'faint': (r, g, b, 60),
        'glow': (lr, lg, lb, 100),
    }


_FONT_CACHE = {}

def _try_load_font(size):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/nix/store/59p03gp3vzbrhd7xjiw3npgbdd68x3y0-dejavu-fonts-2.37/share/fonts/truetype/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        try:
            f = ImageFont.truetype(p, size)
            _FONT_CACHE[size] = f
            return f
        except:
            pass
    f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def _star_points(cx, cy, outer_r, inner_r, n_points):
    coords = []
    for i in range(n_points * 2):
        angle = math.radians(i * 360 / (n_points * 2) - 90)
        r = outer_r if i % 2 == 0 else inner_r
        coords.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return coords


def _draw_text_on_arc(draw, text, cx, cy, radius, start_angle, font, color, spacing=0.04):
    angle = start_angle
    for ch in text:
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        bbox = draw.textbbox((0, 0), ch, font=font)
        cw = bbox[2] - bbox[0]
        ch_h = bbox[3] - bbox[1]
        draw.text((x - cw / 2, y - ch_h / 2), ch, font=font, fill=color)
        angle += spacing


def generate_glyph(callsign, color_hex="#722F37", center_image_bytes=None, timestamp=None):
    if timestamp is None:
        timestamp = datetime.now().isoformat()

    pal = _color_palette(color_hex)
    number_rows = _generate_number_rows(callsign, timestamp)

    img = Image.new('RGBA', (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = CENTER, CENTER

    coords = _star_points(cx, cy, 680, 500, 12)
    draw.polygon(coords, fill=pal['dark'], outline=pal['base'], width=3)

    for i in range(24):
        angle = math.radians(i * 15)
        dist = 700
        fx = cx + dist * math.cos(angle)
        fy = cy + dist * math.sin(angle)
        tip_d = 55
        tip_x = fx + tip_d * math.cos(angle)
        tip_y = fy + tip_d * math.sin(angle)
        perp = angle + math.pi / 2
        bw = 14
        lx = fx + bw * math.cos(perp)
        ly = fy + bw * math.sin(perp)
        rx = fx - bw * math.cos(perp)
        ry = fy - bw * math.sin(perp)
        bx = fx - 12 * math.cos(angle)
        by = fy - 12 * math.sin(angle)
        draw.polygon([(tip_x, tip_y), (lx, ly), (bx, by), (rx, ry)], fill=pal['mid'])

    hs = 480
    draw.rectangle([cx - hs, cy - hs, cx + hs, cy + hs], fill=None, outline=pal['base'], width=3)
    draw.rectangle([cx - hs + 4, cy - hs + 4, cx + hs - 4, cy + hs - 4], fill=None, outline=pal['base'], width=1)

    inner_coords = _star_points(cx, cy, 465, 320, 12)
    draw.polygon(inner_coords, fill=pal['faint'], outline=pal['base'], width=2)

    for r in [440, 410, 370, 335, 290]:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=None, outline=pal['base'], width=2)

    draw.ellipse([cx - 250, cy - 250, cx + 250, cy + 250], fill=pal['dark'], outline=pal['base'], width=3)

    sym_font = _try_load_font(20)
    for i in range(36):
        angle = math.radians(i * 10)
        sr = 490
        sx = cx + sr * math.cos(angle)
        sy = cy + sr * math.sin(angle)
        sym = SYMBOL_CHARS[i % len(SYMBOL_CHARS)]
        bbox = draw.textbbox((0, 0), sym, font=sym_font)
        sw = bbox[2] - bbox[0]
        sh = bbox[3] - bbox[1]
        draw.text((sx - sw / 2, sy - sh / 2), sym, font=sym_font, fill=pal['light'])

    font_cipher = _try_load_font(28)
    diamond = "\u2666"
    full_cipher = f"{diamond} {CIPHER_PHRASES[0]} {diamond} {CIPHER_PHRASES[1]} {diamond} {CIPHER_PHRASES[2]} {diamond}"
    total_chars = len(full_cipher)
    step = 0.036
    total_arc = total_chars * step
    start = -math.pi / 2 - total_arc / 2
    _draw_text_on_arc(draw, full_cipher, cx, cy, 352, start, font_cipher, pal['light'], step)

    font_num = _try_load_font(18)
    nstep = 0.024

    r1_arc = len(number_rows[0]) * nstep
    _draw_text_on_arc(draw, number_rows[0], cx, cy, 425, -r1_arc / 2, font_num, pal['light'], nstep)

    r2_arc = len(number_rows[1]) * nstep
    _draw_text_on_arc(draw, number_rows[1], cx, cy, 392, math.pi - r2_arc / 2, font_num, pal['light'], nstep)

    r3_arc = len(number_rows[2]) * nstep
    _draw_text_on_arc(draw, number_rows[2], cx, cy, 310, math.pi / 2 - r3_arc / 2, font_num, pal['mid'], nstep)

    star8 = _star_points(cx, cy, 200, 85, 8)
    draw.polygon(star8, fill=pal['mid'], outline=pal['light'], width=3)

    for gr in [220, 210]:
        draw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=None, outline=pal['glow'], width=1)

    if center_image_bytes:
        try:
            center_img = Image.open(io.BytesIO(center_image_bytes)).convert('RGBA')
            ts = 280
            center_img = center_img.resize((ts, ts), Image.Resampling.LANCZOS)
            mask = Image.new('L', (ts, ts), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, ts, ts], fill=255)
            center_img.putalpha(mask)
            img.paste(center_img, (cx - ts // 2, cy - ts // 2), center_img)
        except Exception as e:
            print(f"Warning: Could not composite center image: {e}")

    for i in range(24):
        angle = math.radians(i * 15)
        dr = 570
        dx = cx + dr * math.cos(angle)
        dy = cy + dr * math.sin(angle)
        for g in range(4, 0, -1):
            c = pal['glow'] if g > 2 else pal['light']
            draw.ellipse([dx - g, dy - g, dx + g, dy + g], fill=c)

    return img, number_rows


def generate_glyph_to_bytes(callsign, color_hex="#722F37", center_image_bytes=None, timestamp=None):
    img, number_rows = generate_glyph(callsign, color_hex, center_image_bytes, timestamp)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf.getvalue(), number_rows


def generate_glyph_to_base64(callsign, color_hex="#722F37", center_image_bytes=None, timestamp=None):
    png_bytes, number_rows = generate_glyph_to_bytes(callsign, color_hex, center_image_bytes, timestamp)
    b64 = base64.b64encode(png_bytes).decode('utf-8')
    return b64, number_rows


if __name__ == "__main__":
    import sys
    callsign = sys.argv[1] if len(sys.argv) > 1 else "test-001"
    color = sys.argv[2] if len(sys.argv) > 2 else "#722F37"
    center_bytes = None
    if len(sys.argv) > 3:
        with open(sys.argv[3], 'rb') as f:
            center_bytes = f.read()
    img, rows = generate_glyph(callsign, color, center_bytes)
    out_path = f"{callsign}_glyph.png"
    img.save(out_path, "PNG")
    print(f"Glyph saved: {out_path} ({img.size[0]}x{img.size[1]})")
    for i, row in enumerate(rows):
        print(f"  Row {i+1}: {row}")
