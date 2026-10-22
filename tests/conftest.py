import calendar
import struct
import textwrap
import time
from pathlib import Path

import pytest

TCX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
  xmlns:ns3="http://www.garmin.com/xmlschemas/ActivityExtension/v2">
  <Activities>
    <Activity Sport="{sport}">
      <Id>{start}</Id>
      <Lap StartTime="{start}">
        <TotalTimeSeconds>{total}</TotalTimeSeconds>
        <DistanceMeters>{distance}</DistanceMeters>
        <Calories>100</Calories>
        <Intensity>Active</Intensity>
        <TriggerMethod>Manual</TriggerMethod>
        <Track>{points}</Track>
      </Lap>
      {notes}
      <Creator><Name>Garmin Forerunner 310XT</Name></Creator>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""


def tcx_points(start_epoch_min: int, n: int, gps: bool = True, hr: bool = True, step_s: int = 10) -> str:
    out = []
    for i in range(n):
        secs = i * step_s
        t = f"2015-06-01T10:{start_epoch_min + secs // 60:02d}:{secs % 60:02d}Z"
        pos = (
            f"<Position><LatitudeDegrees>{47.6 + i * 0.0002}</LatitudeDegrees>"
            f"<LongitudeDegrees>-122.3</LongitudeDegrees></Position>"
            if gps
            else ""
        )
        hr_el = f"<HeartRateBpm><Value>{120 + i % 10}</Value></HeartRateBpm>" if hr else ""
        out.append(
            f"<Trackpoint><Time>{t}</Time>{pos}<AltitudeMeters>{10 + i}</AltitudeMeters>"
            f"<DistanceMeters>{i * 22.2}</DistanceMeters>{hr_el}</Trackpoint>"
        )
    return "".join(out)


@pytest.fixture
def write_tcx(tmp_path: Path):
    def _write(name, sport="Other", start="2015-06-01T10:00:00Z", notes=None, n=30, gps=True, hr=True):
        notes_el = f"<Notes>{notes}</Notes>" if notes else ""
        body = TCX_TEMPLATE.format(
            sport=sport,
            start=start,
            total=(n - 1) * 10,
            distance=(n - 1) * 22.2,
            points=tcx_points(0, n, gps=gps, hr=hr),
            notes=notes_el,
        )
        path = tmp_path / name
        path.write_text(body)
        return path

    return _write


@pytest.fixture
def write_gpx(tmp_path: Path):
    def _write(name, times, bom=False, with_hr=False, trk_name=None, trk_type=None):
        pts = "".join(
            f'<trkpt lat="{47.6 + i * 0.0002}" lon="-122.3"><ele>{5 + i}</ele><time>{t}</time>'
            + (
                f"<extensions><ns3:TrackPointExtension><ns3:hr>{130 + i}</ns3:hr>"
                "</ns3:TrackPointExtension></extensions>"
                if with_hr
                else ""
            )
            + "</trkpt>"
            for i, t in enumerate(times)
        )
        name_el = f"<name>{trk_name}</name>" if trk_name else ""
        type_el = f"<type>{trk_type}</type>" if trk_type else ""
        body = textwrap.dedent(
            f"""<?xml version="1.0" encoding="UTF-8"?>
            <gpx creator="GPSMAP 78sc" version="1.1" xmlns="http://www.topografix.com/GPX/1/1"
              xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
              <trk>{name_el}{type_el}<trkseg>{pts}</trkseg></trk>
            </gpx>"""
        )
        path = tmp_path / name
        path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + body.encode())
        return path

    return _write


def minute_times(day: str, hour: int, start_min: int, count: int, step_s: int = 30):
    out = []
    for i in range(count):
        secs = start_min * 60 + i * step_s
        out.append(f"{day}T{hour + secs // 3600:02d}:{(secs // 60) % 60:02d}:{secs % 60:02d}Z")
    return out


# --- Minimal FIT-file encoder (little-endian, no developer fields) ---

FIT_EPOCH_OFFSET = 631065600  # seconds from Unix epoch to the FIT epoch (1989-12-31)

# base type name -> (identifier byte, size, struct format)
FIT_BASE_TYPES = {
    "enum": (0x00, 1, "B"),
    "uint8": (0x02, 1, "B"),
    "sint16": (0x83, 2, "h"),
    "uint16": (0x84, 2, "H"),
    "sint32": (0x85, 4, "i"),
    "uint32": (0x86, 4, "I"),
    "uint32z": (0x8C, 4, "I"),
}

FIT_SPORTS = {"generic": 0, "running": 1, "cycling": 2, "swimming": 5, "walking": 11, "hiking": 17, "unrecognized": 75}

_FIT_CRC_TABLE = (
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
)


def _fit_crc(data: bytes) -> int:
    crc = 0
    for byte in data:
        for nibble in (byte & 0xF, byte >> 4):
            crc = ((crc >> 4) & 0x0FFF) ^ _FIT_CRC_TABLE[crc & 0xF] ^ _FIT_CRC_TABLE[nibble]
    return crc


def _fit_messages(local: int, global_num: int, fields: list, rows: list) -> bytes:
    """A definition message followed by one data message per row.

    fields: [(field_def_num, base_type_name)]; rows: tuples of values in the same order.
    """
    out = bytearray([0x40 | local, 0, 0])
    out += struct.pack("<HB", global_num, len(fields))
    for num, base in fields:
        base_id, size, _ = FIT_BASE_TYPES[base]
        out += struct.pack("<BBB", num, size, base_id)
    for row in rows:
        out.append(local)
        for (_, base), value in zip(fields, row):
            out += struct.pack("<" + FIT_BASE_TYPES[base][2], value)
    return bytes(out)


def fit_epoch(iso_time: str) -> int:
    """ISO-8601 UTC timestamp -> FIT timestamp (seconds since 1989-12-31)."""
    return calendar.timegm(time.strptime(iso_time, "%Y-%m-%dT%H:%M:%SZ")) - FIT_EPOCH_OFFSET


def write_fit_file(path: Path, rows: list, sport: str = "running", start="2015-06-01T10:00:00Z"):
    """Write a single-session FIT file. rows: (t_offset_s, lat, lon, ele_m, hr, cadence) — None for missing."""
    t0 = fit_epoch(start)
    n = len(rows)

    record_fields = [
        (253, "uint32"), (0, "sint32"), (1, "sint32"), (2, "uint16"),
        (3, "uint8"), (4, "uint8"), (5, "uint32"), (6, "uint16"),
    ]
    record_rows = []
    for i, (dt, lat, lon, ele, hr, cad) in enumerate(rows):
        semicircle = 2**31 / 180
        dist_m = i * 22.2
        record_rows.append(
            (
                t0 + dt,
                int(lat * semicircle) if lat is not None else 0x7FFFFFFF,
                int(lon * semicircle) if lon is not None else 0x7FFFFFFF,
                int((ele + 500) * 5) if ele is not None else 0xFFFF,
                hr if hr is not None else 0xFF,
                cad if cad is not None else 0xFF,
                int(dist_m * 100),
                int(2.5 * 1000),
            )
        )

    lap_fields = [
        (253, "uint32"), (2, "uint32"), (8, "uint32"), (9, "uint32"), (11, "uint16"),
        (14, "uint16"), (15, "uint8"), (16, "uint8"), (17, "uint8"),
    ]
    hrs = [r[4] for r in rows if r[4] is not None]
    lap_rows = [(
        t0 + rows[-1][0], t0, int(rows[-1][0] * 1000), int((n - 1) * 22.2 * 100), 150,
        int(4.2 * 1000), int(sum(hrs) / len(hrs)) if hrs else 0xFF, max(hrs) if hrs else 0xFF, 88,
    )]

    session_fields = [
        (253, "uint32"), (2, "uint32"), (5, "enum"), (8, "uint32"), (9, "uint32"), (11, "uint16"),
    ]
    session_rows = [(t0 + rows[-1][0], t0, FIT_SPORTS[sport], int(rows[-1][0] * 1000), int((n - 1) * 22.2 * 100), 150)]

    body = (
        _fit_messages(0, 0, [(0, "enum"), (1, "uint16"), (2, "uint16"), (4, "uint32")], [(4, 1, 1036, t0)])
        + _fit_messages(1, 20, record_fields, record_rows)
        + _fit_messages(2, 19, lap_fields, lap_rows)
        + _fit_messages(3, 18, session_fields, session_rows)
    )
    header = struct.pack("<BBHI", 12, 0x10, 100, len(body)) + b".FIT"
    path.write_bytes(header + body + struct.pack("<H", _fit_crc(header + body)))
    return path


@pytest.fixture
def write_fit(tmp_path: Path):
    def _write(name, sport="running", start="2015-06-01T10:00:00Z", n=30, gps=True, hr=True):
        rows = [
            (
                i * 10,
                47.6 + i * 0.0002 if gps else None,
                -122.3 if gps else None,
                10 + i,
                120 + i % 10 if hr else None,
                90,
            )
            for i in range(n)
        ]
        return write_fit_file(tmp_path / name, rows, sport=sport, start=start)

    return _write
