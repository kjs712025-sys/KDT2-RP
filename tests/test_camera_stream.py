from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import app


def test_placeholder_frame_is_created():
    frame = app.create_placeholder_frame("Camera unavailable")

    assert frame is not None
    assert frame.shape[0] > 0
    assert frame.shape[1] > 0
    assert frame.shape[2] == 3
