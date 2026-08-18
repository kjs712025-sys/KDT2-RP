from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import app


import cv2
import numpy as np


def test_placeholder_frame_is_created():
    frame = app.create_placeholder_frame("Camera unavailable")

    assert frame is not None
    assert frame.shape[0] > 0
    assert frame.shape[1] > 0
    assert frame.shape[2] == 3


def test_detect_numeric_slot_id_from_digit_image():
    frame = np.full((400, 600, 3), 255, dtype=np.uint8)
    cv2.putText(
        frame,
        "7",
        (250, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        5.0,
        (0, 0, 0),
        12,
        cv2.LINE_AA,
    )

    detected = app.detect_numeric_slot_id(frame)

    assert detected == 7


def test_detect_numeric_slot_id_10_marker():
    frame = np.full((400, 600, 3), 255, dtype=np.uint8)
    cv2.putText(
        frame,
        "10",
        (180, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        4.2,
        (0, 0, 0),
        12,
        cv2.LINE_AA,
    )

    detected = app.detect_numeric_slot_id(frame)

    assert detected == 10


def test_draw_detection_overlay_marks_clothing_and_digit_box():
    frame = np.full((200, 300, 3), 255, dtype=np.uint8)

    annotated = app.draw_detection_overlay(
        frame,
        clothing_boxes=[(10, 20, 120, 170)],
        digit_boxes=[(180, 50, 70, 90)],
        clothing_labels=["CLOTHING"],
        digit_labels=["SLOT 7"],
    )

    assert annotated.shape == frame.shape
    assert np.any(annotated[20:170, 10:120] != 255)
    assert np.any(annotated[50:90, 180:250] != 255)


def test_download_image_uses_png_mime_type_for_png_files(tmp_path):
    png_path = tmp_path / "qr_2.png"
    png_path.write_bytes(b"fake-png-bytes")

    original_dir = app.IMAGE_DIR
    app.IMAGE_DIR = tmp_path
    try:
        response = app.download_image("qr_2.png")
        assert response.media_type == "image/png"
    finally:
        app.IMAGE_DIR = original_dir
