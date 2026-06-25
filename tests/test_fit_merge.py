"""Self-test of the FIT merge core through our own modules (run, don't trust)."""
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from sgs import fit_merge
from sgs.models import StrongWorkout, StrongExercise, StrongSet
from fit_tool.fit_file import FitFile
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.set_message import SetMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.profile_type import (
    FileType, Manufacturer, Sport, SubSport, SetType,
)

cfg = SimpleNamespace(strong_weight_unit="kg")
start = datetime(2024, 1, 2, 8, 0, 0, tzinfo=timezone.utc)

workout = StrongWorkout(
    id="abc12345", name="Day B", start=start, end=start + timedelta(minutes=10),
    exercises=[
        StrongExercise(id="m1", name="Bench Press (Barbell)",
                       sets=[StrongSet(reps=5, weight=60.0), StrongSet(reps=3, weight=65.0)]),
        StrongExercise(id="m2", name="Plank", sets=[StrongSet(reps=0, duration=60.0)]),
        StrongExercise(id="m3", name="Custom Thing", sets=[StrongSet(reps=10, weight=40.0)]),
    ])


def sets_of(ff):
    return [r.message for r in ff.records if isinstance(r.message, SetMessage)]


# --- standalone (no watch) ---
b = fit_merge.build_merged_fit(workout, None, cfg)
ss = sets_of(FitFile.from_bytes(b))
print("standalone sets:", len(ss))
for s in ss:
    print(f"  idx={s.message_index} reps={s.repetitions} wt={s.weight} "
          f"cat={s.category} sub={s.category_subtype} dur={s.duration}")
assert len(ss) == 4
assert ss[0].repetitions == 5 and abs(ss[0].weight - 60.0) < 1e-6
assert ss[0].category == [0] and ss[0].category_subtype == [1], "bench mapping wrong"
assert ss[2].category == [19], f"plank category wrong: {ss[2].category}"
assert abs(ss[2].duration - 60.0) < 1e-6, "plank duration lost"
assert ss[3].repetitions == 10 and abs(ss[3].weight - 40.0) < 1e-6
assert ss[3].category == [65534], "unmapped should be UNKNOWN category"
print("standalone OK\n")


# --- synthetic watch fit (HR + 2 detected sets) ---
def watch_bytes():
    base = round(start.timestamp()) * 1000
    bd = FitFileBuilder(auto_define=True, min_string_size=50)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.GARMIN.value
    fid.product = 3589
    fid.time_created = base
    fid.serial_number = 0x111
    bd.add(fid)
    recs = []
    for i in range(10):
        r = RecordMessage()
        r.timestamp = base + i * 60000
        r.heart_rate = 100 + i
        recs.append(r)
    bd.add_all(recs)
    for i in range(2):
        s = SetMessage()
        s.start_time = base + (i + 1) * 120000 - 30000
        s.timestamp = base + (i + 1) * 120000
        s.set_type = SetType.ACTIVE.value
        s.duration = 30.0
        s.repetitions = 99
        s.weight = 0.0
        s.category = [65534]
        s.message_index = i
        bd.add(s)
    sess = SessionMessage()
    sess.start_time = base
    sess.timestamp = base + 600000
    sess.total_elapsed_time = 600.0
    sess.total_timer_time = 600.0
    sess.sport = Sport.TRAINING.value
    sess.sub_sport = SubSport.STRENGTH_TRAINING.value
    sess.first_lap_index = 0
    sess.num_laps = 1
    bd.add(sess)
    return bytes(bd.build().to_bytes())


merged = fit_merge.build_merged_fit(workout, watch_bytes(), cfg)
mf = FitFile.from_bytes(merged)
recs = [r.message for r in mf.records if isinstance(r.message, RecordMessage)]
ms = sets_of(mf)
print(f"merged: {len(recs)} HR records, {len(ms)} sets")
assert len(recs) == 10, "HR records not preserved!"
assert recs[0].heart_rate == 100, "HR value not preserved"
assert len(ms) == 4, f"expected Strong's 4 sets, got {len(ms)}"
assert ms[0].repetitions == 5 and abs(ms[0].weight - 60.0) < 1e-6, "Strong set not applied"
print("merge OK (HR preserved, watch's 2 sets replaced by Strong's 4)\n")
print("ALL FIT TESTS PASSED")
