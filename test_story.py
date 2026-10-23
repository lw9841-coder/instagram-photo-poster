"""Tests for the story canvas. Run: .venv/bin/python -m unittest test_story -v"""
import tempfile, unittest
from pathlib import Path
from PIL import Image

import story


def make(w, h, color=(200, 30, 30), orientation=None):
    d = tempfile.mkdtemp(prefix="storytest-")
    p = Path(d) / "src.jpg"
    img = Image.new("RGB", (w, h), color)
    if orientation:
        ex = Image.Exif(); ex[274] = orientation
        img.save(p, "JPEG", exif=ex)
    else:
        img.save(p, "JPEG")
    return p, d


class PrepStory(unittest.TestCase):
    def test_output_is_1080x1920_jpeg(self):
        src, d = make(2000, 1500)
        out = story.prep_story(src, d)
        img = Image.open(out)
        self.assertEqual(img.size, (1080, 1920))
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(img.mode, "RGB")

    def test_landscape_photo_is_centered_full_width(self):
        # Solid red source: the sharp photo band is pure red, the canvas
        # above and below it is darkened, so it is measurably less red.
        src, d = make(2000, 1500)
        img = Image.open(story.prep_story(src, d))
        center = img.getpixel((540, 960))
        top = img.getpixel((540, 40))
        self.assertGreater(center[0], 180)
        self.assertLess(top[0], center[0] - 30)
        # 2000x1500 fit to 1080 wide is 810 tall: band spans y 555..1365
        self.assertGreater(img.getpixel((540, 600))[0], 180)
        self.assertLess(img.getpixel((540, 500))[0], center[0] - 30)

    def test_portrait_photo_fits_inside_frame(self):
        src, d = make(1125, 1500)          # 3:4
        img = Image.open(story.prep_story(src, d))
        self.assertEqual(img.size, (1080, 1920))
        self.assertGreater(img.getpixel((540, 960))[0], 180)

    def test_exif_rotation_is_baked_in(self):
        # Stored landscape with orientation 6 = really a portrait. Upright,
        # it is 1500x2000, so the sharp photo must reach near the top edge
        # region that a landscape fit would leave as dark canvas.
        src, d = make(2000, 1500, orientation=6)
        img = Image.open(story.prep_story(src, d))
        self.assertGreater(img.getpixel((540, 400))[0], 180)
        self.assertIsNone(img.getexif().get(274))

    def test_small_photo_is_not_upscaled_past_its_size(self):
        src, d = make(400, 300)
        img = Image.open(story.prep_story(src, d))
        # band would be 810 tall if upscaled to full width; it must not be
        self.assertLess(img.getpixel((540, 700))[0], 170)


if __name__ == "__main__":
    unittest.main()
