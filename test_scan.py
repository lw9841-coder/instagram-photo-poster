"""Tests for the photo filter and ranking. Run: .venv/bin/python -m unittest -v"""
import copy, tempfile, unittest
from pathlib import Path
from PIL import Image

import post, scan, seen

SAMPLE = Path(__file__).parent / "examples" / "sample-library.json"


def library():
    return scan.load_saved_library(SAMPLE)


def by_uuid(photos, uid):
    return next(p for p in photos if p["uuid"] == uid)


class Filter(unittest.TestCase):
    def test_each_kind_of_junk_is_caught_with_its_reason(self):
        lib = library()
        expect = {
            "0000-receipt-0007": "junk content (paper, receipt)",
            "0000-shot-0008": "screenshot",
            "0000-aiimg-0009": "not your photo (saved from ChatGPT)",
            "0000-texted-0010": "not your photo (saved from Messages)",
            "0000-video-0011": "not a photo",
            "0000-blurry-0012": "Apple flagged as a bad shot",
            "0000-hidden-0013": "trashed/hidden",
            "0000-burst-0014": "unselected burst frame",
        }
        for uid, why in expect.items():
            self.assertEqual(scan.is_junk(by_uuid(lib, uid)), why, uid)

    def test_camera_photos_pass(self):
        lib = library()
        self.assertIsNone(scan.is_junk(by_uuid(lib, "0000-sunset-0001")))

    def test_ai_image_is_dropped_even_when_it_scores_highest(self):
        lib = library()
        ai = by_uuid(lib, "0000-aiimg-0009")
        best_real = max(scan.postability(p) for p in lib if not scan.is_junk(p))
        self.assertGreater(scan.postability(ai), best_real)
        self.assertIsNotNone(scan.is_junk(ai))


class Ranking(unittest.TestCase):
    def kept(self):
        lib = [p for p in library() if not scan.is_junk(p)]
        for p in lib:
            p["_score"] = scan.postability(p)
        return lib

    def test_three_shots_of_one_moment_collapse_to_the_best(self):
        moments = scan.dedupe_moments(self.kept())
        group = [p for p in moments if p["uuid"].startswith("0000-group")]
        self.assertEqual(len(group), 1)
        self.assertEqual(group[0]["uuid"], "0000-group-0002")
        self.assertEqual(group[0]["_moment_size"], 3)

    def test_spread_caps_finalists_from_one_day(self):
        base = self.kept()[0]
        same_day = []
        for i in range(5):
            p = copy.deepcopy(base)
            p["uuid"] = f"same-day-{i}"
            p["place"] = {"name": f"Spot {i}"}
            same_day.append(p)
        picked = scan.spread_picks(same_day, top=3, max_per_day=2)
        # cap of 2 per day, then backfilled from overflow to reach top=3
        self.assertEqual(len(picked), 3)
        self.assertEqual([p["uuid"] for p in picked[:2]], ["same-day-0", "same-day-1"])

    def test_favorite_outranks_an_otherwise_equal_photo(self):
        a = by_uuid(library(), "0000-trail-0005")
        b = copy.deepcopy(a)
        b["favorite"] = True
        self.assertGreater(scan.postability(b), scan.postability(a))


class AlreadyPosted(unittest.TestCase):
    def test_identical_image_has_zero_distance(self):
        img = Image.open(Path(__file__).parent / "examples" / "photos" / "sample-a.jpg")
        h = seen.dhash_bytes(img)
        self.assertEqual(seen.hamming(h, h), 0)

    def test_tall_photo_is_also_checked_as_instagram_would_crop_it(self):
        img = Image.new("RGB", (900, 1200))   # 3:4, taller than Instagram allows
        names = [name for name, _ in seen.crops(img)]
        self.assertEqual(names, ["full", "4:5", "1:1"])


class PrepImage(unittest.TestCase):
    def test_exif_rotation_is_baked_in_and_width_capped(self):
        d = tempfile.mkdtemp(prefix="posttest-")
        src = Path(d) / "src.jpg"
        ex = Image.Exif(); ex[274] = 6       # stored sideways, really portrait
        Image.new("RGB", (2000, 1500)).save(src, "JPEG", exif=ex)
        out, w, h = post.prep_image(src, d)
        self.assertEqual((w, h), (1440, 1920))
        self.assertIsNone(Image.open(out).getexif().get(274))


if __name__ == "__main__":
    unittest.main()
