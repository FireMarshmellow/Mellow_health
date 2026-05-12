import gzip
import io
from fitparse import FitFile

MAX_TRACK_POINTS = 200
SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)


def parse_fit_gz(file_bytes: bytes) -> dict:
    with gzip.open(io.BytesIO(file_bytes), "rb") as f:
        fit_bytes = f.read()

    fitfile = FitFile(io.BytesIO(fit_bytes))

    points = []
    messages = fitfile.get_messages("record")
    while True:
        try:
            record = next(messages)
        except StopIteration:
            break
        except (TypeError, ValueError):
            # fitparse bug with Python 3.14 on some date_time fields; skip record
            continue
        data = {d.name: d.value for d in record}

        lat = data.get("position_lat")
        lon = data.get("position_long")

        if lat is None or lon is None:
            continue

        lat_deg = lat * SEMICIRCLE_TO_DEG
        lon_deg = lon * SEMICIRCLE_TO_DEG

        if not (-90 <= lat_deg <= 90) or not (-180 <= lon_deg <= 180):
            continue

        ele = data.get("altitude")
        timestamp = data.get("timestamp")

        points.append({
            "lat": round(lat_deg, 6),
            "lon": round(lon_deg, 6),
            "ele": round(ele, 1) if ele is not None else None,
            "time": timestamp.isoformat() if timestamp else None,
        })

    downsampled = _downsample(points, MAX_TRACK_POINTS)
    return {
        "has_gps": len(points) > 0,
        "gps_track": downsampled if points else None,
    }


def _downsample(points: list, max_points: int) -> list:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]
