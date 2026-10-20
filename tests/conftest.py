import textwrap
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
