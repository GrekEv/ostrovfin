import math

RADIUS_M = 6371008.8


def haversine_m(a_lat, a_lon, b_lat, b_lon):
    a, b = math.radians(a_lat), math.radians(b_lat)
    dl = math.radians(b_lon - a_lon)
    dp = b - a
    h = math.sin(dp / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dl / 2) ** 2
    return 2 * RADIUS_M * math.asin(min(1, math.sqrt(h)))


def line_distance_m(lat, lon, lines):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError('Invalid coordinates')
    factor_x = RADIUS_M * math.cos(math.radians(lat)) * math.pi / 180
    factor_y = RADIUS_M * math.pi / 180
    best = None
    for line in lines:
        if not isinstance(line, list) or not line:
            continue
        xy = [((p[1] - lon) * factor_x, (p[0] - lat) * factor_y) for p in line]
        if len(xy) == 1:
            d = math.hypot(*xy[0])
            best = d if best is None else min(best, d)
            continue
        for a, b in zip(xy, xy[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            denom = dx * dx + dy * dy
            t = max(0.0, min(1.0, -((a[0] * dx + a[1] * dy) / denom))) if denom else 0.0
            d = math.hypot(a[0] + t * dx, a[1] + t * dy)
            best = d if best is None else min(best, d)
    if best is None:
        raise ValueError('No coastline geometry available')
    return round(best)
