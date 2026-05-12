import gpxpy

MAX_TRACK_POINTS = 200


def parse_gpx(file_bytes: bytes) -> dict:
    gpx = gpxpy.parse(file_bytes.decode("utf-8"))

    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for pt in segment.points:
                points.append({
                    "lat": round(pt.latitude, 6),
                    "lon": round(pt.longitude, 6),
                    "ele": round(pt.elevation, 1) if pt.elevation is not None else None,
                    "time": pt.time.isoformat() if pt.time else None,
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
