"""Independent media fixture shared by headless and Qt workflows."""

import tempfile
from pathlib import Path

from frameflow.media import Processes, Tools, probe


class MediaFixture:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tools = Tools.find()

    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory(prefix="帧转 测试-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self._source = None
        self._info = None

    @property
    def source(self):
        if self._source is None:
            path = self.root / "样片 01.mov"
            Processes().run([
                self.tools.ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                "testsrc2=size=320x180:rate=24", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=44100", "-t", "2.4", "-c:v", "mpeg4",
                "-c:a", "aac", str(path),
            ], timeout=20)
            self._source = path
        return self._source

    @property
    def info(self):
        if self._info is None:
            self._info = probe(self.source, self.tools, Processes())
        return self._info

    def decode(self, file: Path):
        Processes().run([self.tools.ffmpeg, "-v", "error", "-nostdin", "-i", str(file),
                         "-f", "null", "-"], timeout=20)
