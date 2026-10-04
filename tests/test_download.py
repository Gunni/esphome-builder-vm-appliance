import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

D = runpy.run_path(str(Path(__file__).resolve().parents[1] / "download-coreos.py"))


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b"verified Fedora ISO fixture"
        self.selection = dict(location="https://builds.coreos.fedoraproject.org/fixture.iso",
            sha256=hashlib.sha256(self.data).hexdigest(), release="44.20260913.3.2")
        (self.root / "selection.json").write_text(json.dumps(self.selection))

    def test_latest_metadata_selection(self):
        stream = {"architectures": {"x86_64": {"artifacts": {"metal": {
            "release": self.selection["release"], "formats": {"iso": {"disk": {
                "location": self.selection["location"], "sha256": self.selection["sha256"]}}}}}}}}
        with patch.dict(D["prepare"].__globals__, urlopen=lambda *a, **k: io.BytesIO(json.dumps(stream).encode())):
            actual = D["prepare"](self.root)
        self.assertEqual(actual, self.selection)

    def test_download_verification_and_cache(self):
        with patch.dict(D["fetch"].__globals__, urlopen=lambda *a, **k: io.BytesIO(self.data)):
            output = D["fetch"](self.root)
        self.assertEqual(output.read_bytes(), self.data)
        def no_network(*args, **kwargs):
            raise AssertionError("Verified cache should not download again")
        with patch.dict(D["fetch"].__globals__, urlopen=no_network):
            self.assertEqual(D["fetch"](self.root), output)

    def test_bad_download_removed(self):
        with patch.dict(D["fetch"].__globals__, urlopen=lambda *a, **k: io.BytesIO(b"wrong image")):
            with self.assertRaises(ValueError):
                D["fetch"](self.root)
        self.assertFalse((self.root / "base.iso").exists())

    def test_corrupt_cache_replaced(self):
        (self.root / "base.iso").write_bytes(b"old")
        with patch.dict(D["fetch"].__globals__, urlopen=lambda *a, **k: io.BytesIO(self.data)):
            D["fetch"](self.root)
        self.assertEqual((self.root / "base.iso").read_bytes(), self.data)

    def test_unexpected_origin_rejected(self):
        self.selection["location"] = "https://example.com/image.iso"
        with self.assertRaises(ValueError):
            D["validate_selection"](self.selection)
