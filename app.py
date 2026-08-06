from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

import cv2
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

try:
    from paho.mqtt import client as mqtt
except Exception:
    mqtt = None

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None


BASE_DIR = Path(__file__).resolve().parent
IMAGE_DIR = BASE_DIR / "saved_images"
DATABASE_PATH = BASE_DIR / "local_gallery.db"
STREAM_FRAME_DELAY_SECONDS = 1 / 25
SNAPSHOT_COOLDOWN_SECONDS = 3.0
VALID_QR_IDS = set(range(1, 11))


def parse_int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "127.0.0.1").strip()
MQTT_BROKER_PORT = parse_int(os.getenv("MQTT_BROKER_PORT", "1883"), 1883)
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "rail/target_qr").strip()

CAMERA_DEVICE = os.getenv("CAMERA_DEVICE", "/dev/video0").strip()
CAMERA_INDEX = parse_int(os.getenv("CAMERA_INDEX", "0"), 0)
MAX_CAMERA_INDEX = parse_int(os.getenv("MAX_CAMERA_INDEX", "5"), 5)


def open_camera_source(source: int | str) -> cv2.VideoCapture:
    return cv2.VideoCapture(str(source))


def find_available_camera_source() -> str | int:
    seen = set()
    candidates: list[str | int] = []
    if CAMERA_DEVICE:
        candidates.append(CAMERA_DEVICE)
    candidates.extend([CAMERA_INDEX] + list(range(MAX_CAMERA_INDEX + 1)))

    for source in candidates:
        if source in seen:
            continue
        seen.add(source)
        capture = None
        try:
            capture = open_camera_source(source)
            if capture.isOpened():
                capture.release()
                return source
        except Exception:
            pass
        finally:
            if capture is not None:
                capture.release()

    return CAMERA_DEVICE if CAMERA_DEVICE else CAMERA_INDEX


IMAGE_DIR.mkdir(parents=True, exist_ok=True)

api_key = os.getenv("GEMINI_API_KEY", "").strip()
if api_key:
    os.environ.setdefault("GOOGLE_API_KEY", api_key)

try:
    gemini_client = None
except Exception:
    gemini_client = None

app = FastAPI(title="Smart Closet Backend")

frame_lock = threading.Lock()
latest_frame: Any = None
last_snapshot_time = 0.0
camera_thread_started = False
camera_lock = threading.Lock()
camera_unavailable_reported = False
mqtt_client: Any = None
mqtt_client_lock = threading.Lock()

try:
    yolo_model = YOLO("yolov8n.pt") if YOLO is not None else None
except Exception:
    yolo_model = None

try:
    qr_detector = cv2.QRCodeDetector()
except Exception:
    qr_detector = None


def initialize_database() -> None:
    try:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS images (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT,
                    description TEXT,
                    created_at REAL
                )
                """
            )
            connection.commit()
    except Exception:
        pass


def create_placeholder_frame(message: str = "Camera unavailable") -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:, :] = (18, 24, 32)
    cv2.putText(
        frame,
        message,
        (40, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return frame


initialize_database()


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_mqtt() -> None:
    global mqtt_client
    if mqtt is None or mqtt_client is not None:
        return

    try:
        mqtt_client = mqtt.Client(client_id="smart_closet_backend", clean_session=True)
        mqtt_client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 10)
        mqtt_client.loop_start()
    except Exception:
        mqtt_client = None


def publish_slot_id(image_id: int) -> bool:
    global mqtt_client
    if mqtt is None:
        return False

    if mqtt_client is None:
        initialize_mqtt()

    if mqtt_client is None:
        return False

    try:
        payload = json.dumps(
            {
                "slot_id": image_id,
                "device_id": "smart_closet_backend",
                "ts": int(time.time()),
            },
            ensure_ascii=False,
        )
        result = mqtt_client.publish(MQTT_TOPIC, payload, qos=1, retain=False)
        return getattr(result, "rc", None) == mqtt.MQTT_ERR_SUCCESS
    except Exception:
        return False


def get_weather_info() -> dict[str, Any]:
    return {
        "temperature_c": 10,
        "conditions": "Windy and Chilly",
    }


def get_gemini_client() -> Any | None:
    if genai is None:
        return None

    api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        return genai.Client(api_key=api_key)
    except Exception:
        return None


def build_llm_recommendation(inventory: list[dict[str, Any]], weather_info: dict[str, Any]) -> dict[str, Any]:
    if genai is None or types is None:
        return {
            "summary": "오늘은 가볍고 편한 스타일을 추천해요.",
            "recommendations": [
                {"id": item.get("id"), "reason": "데이터 기반 기본 추천"}
                for item in inventory[:3]
            ],
            "weather_note": f"현재 기온 {weather_info.get('temperature_c')}°C, {weather_info.get('conditions')}",
        }

    prompt = (
        "당신은 스마트 코디 도우미입니다. "
        "아래의 옷장 데이터와 현재 날씨를 바탕으로 사용자가 입기 좋은 추천 옷 3개를 JSON으로만 응답하세요. "
        "응답 형식은 {\"summary\": 문자열, \"recommendations\": [{\"id\": 숫자, \"reason\": 문자열}], \"weather_note\": 문자열} 입니다.\n"
        f"날씨: {json.dumps(weather_info, ensure_ascii=False)}\n"
        f"옷장: {json.dumps(inventory, ensure_ascii=False)}"
    )

    try:
        client = get_gemini_client()
        if client is None:
            raise RuntimeError("Gemini client unavailable")

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw_text = getattr(response, "text", "") or ""
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    return {
        "summary": "오늘은 가볍고 편한 스타일을 추천해요.",
        "recommendations": [
            {"id": item.get("id"), "reason": "기본 추천"}
            for item in inventory[:3]
        ],
        "weather_note": f"현재 기온 {weather_info.get('temperature_c')}°C, {weather_info.get('conditions')}",
    }


def resolve_class_name(model: Any, class_id: int) -> str:
    try:
        names = getattr(model, "names", {})
        if isinstance(names, dict):
            return str(names.get(class_id, "")).lower()
        if isinstance(names, list) and class_id < len(names):
            return str(names[class_id]).lower()
    except Exception:
        pass
    return ""


def is_clothing_like(class_name: str) -> bool:
    return class_name in {"person", "clothing", "backpack", "tie", "suitcase"}


def is_qr_anchor(class_name: str) -> bool:
    return class_name in {"qrcode", "backpack"}


def decode_qr_from_roi(roi: Any) -> str:
    if qr_detector is None or roi is None or roi.size == 0:
        return ""

    try:
        decoded_text, _, _ = qr_detector.detectAndDecode(roi)
        if decoded_text:
            return decoded_text.strip()
    except Exception:
        return ""

    return ""


def parse_valid_qr_id(raw_value: str) -> int | None:
    try:
        value = int(str(raw_value).strip())
        if value in VALID_QR_IDS:
            return value
    except Exception:
        return None
    return None


def remove_existing_file(file_path: str) -> None:
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass


def save_frame_to_disk(frame: Any, image_path: Path) -> None:
    try:
        success = cv2.imwrite(str(image_path), frame)
        if not success:
            raise RuntimeError("Failed to write frame to disk")
    except Exception:
        raise


def analyze_image_with_gemini(image_path: Path) -> str:
    prompt = (
        "Analyze this clothing item and provide a one-sentence summary of its type, "
        "color, and fabric thickness suitable for a mobile app display."
    )

    if genai is None or types is None:
        return "Gemini analysis unavailable"

    try:
        client = get_gemini_client()
        if client is None:
            raise RuntimeError("Gemini client unavailable")

        image_bytes = image_path.read_bytes()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, image_part],
        )
        text = getattr(response, "text", None)
        if text:
            return text.strip()
    except Exception:
        pass

    return "Gemini analysis unavailable"


def upsert_image_record(image_id: int, file_path: str, description: str) -> None:
    timestamp = time.time()
    try:
        with get_connection() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO images (id, filepath, description, created_at) VALUES (?, ?, ?, ?)",
                (image_id, file_path, description, timestamp),
            )
            connection.commit()
    except Exception:
        pass


def handle_snapshot(frame: Any, qr_id: int) -> None:
    image_name = f"qr_{qr_id}.jpg"
    image_path = IMAGE_DIR / image_name

    try:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT filepath FROM images WHERE id = ?",
                (qr_id,),
            ).fetchone()

        if row and row["filepath"]:
            remove_existing_file(row["filepath"])
    except Exception:
        pass

    try:
        save_frame_to_disk(frame, image_path)
    except Exception:
        return

    description = "Gemini analysis unavailable"
    try:
        description = analyze_image_with_gemini(image_path)
    except Exception:
        description = "Gemini analysis unavailable"

    try:
        upsert_image_record(qr_id, str(image_path), description)
    except Exception:
        pass


def process_detected_frame(frame: Any) -> None:
    global last_snapshot_time

    if yolo_model is None or frame is None:
        return

    try:
        results = yolo_model.predict(frame, verbose=False)
    except Exception:
        return

    try:
        detections = results[0].boxes if results else []
    except Exception:
        detections = []

    clothing_detected = False
    qr_candidates: list[tuple[str, list[float]]] = []

    for box in detections:
        try:
            class_id = int(box.cls[0])
            class_name = resolve_class_name(yolo_model, class_id)
            xyxy = box.xyxy[0].tolist()
        except Exception:
            continue

        if is_clothing_like(class_name):
            clothing_detected = True
        if is_qr_anchor(class_name):
            qr_candidates.append((class_name, xyxy))

    if not clothing_detected or not qr_candidates:
        return

    try:
        current_time = time.time()
        if current_time - last_snapshot_time < SNAPSHOT_COOLDOWN_SECONDS:
            return
    except Exception:
        return

    frame_height, frame_width = frame.shape[:2]

    for _, xyxy in qr_candidates:
        try:
            x1, y1, x2, y2 = [int(max(0, value)) for value in xyxy]
            x2 = min(x2, frame_width)
            y2 = min(y2, frame_height)
            roi = frame[y1:y2, x1:x2]
        except Exception:
            continue

        decoded_text = decode_qr_from_roi(roi)
        if not decoded_text:
            decoded_text = decode_qr_from_roi(frame)

        qr_id = parse_valid_qr_id(decoded_text)
        if qr_id is None:
            continue

        try:
            last_snapshot_time = time.time()
            handle_snapshot(frame.copy(), qr_id)
        except Exception:
            pass
        break


def camera_loop() -> None:
    while True:
        capture = None
        camera_source = None
        try:
            camera_source = find_available_camera_source()
            capture = open_camera_source(camera_source)
            if not capture.isOpened():
                raise RuntimeError(f"Camera unavailable: {camera_source}")

            while True:
                try:
                    success, frame = capture.read()
                    if not success or frame is None:
                        raise RuntimeError("No frame read from camera")

                    with frame_lock:
                        global latest_frame
                        latest_frame = frame.copy()

                    try:
                        process_detected_frame(frame)
                    except Exception:
                        pass

                    time.sleep(STREAM_FRAME_DELAY_SECONDS)
                except Exception:
                    with frame_lock:
                        latest_frame = create_placeholder_frame(
                            f"Camera unavailable: {camera_source}"
                        )
                    time.sleep(0.5)
        except Exception:
            with frame_lock:
                latest_frame = create_placeholder_frame(
                    f"Camera unavailable: {camera_source or CAMERA_DEVICE}"
                )
            time.sleep(1.0)
        finally:
            try:
                if capture is not None:
                    capture.release()
            except Exception:
                pass


def start_camera_thread_once() -> None:
    global camera_thread_started

    with camera_lock:
        if camera_thread_started:
            return
        camera_thread_started = True

        thread = threading.Thread(target=camera_loop, daemon=True)
        thread.start()


@app.on_event("startup")
def on_startup() -> None:
    initialize_mqtt()
    start_camera_thread_once()


@app.api_route("/api/send_id/{image_id}", methods=["GET", "POST"])
def send_image_id(image_id: int) -> JSONResponse:
    if image_id not in VALID_QR_IDS:
        raise HTTPException(status_code=400, detail="Image ID must be between 1 and 10")

    published = publish_slot_id(image_id)
    if not published:
        raise HTTPException(status_code=502, detail="Failed to publish to MQTT broker")

    return JSONResponse(
        content={
            "ok": True,
            "id": image_id,
            "topic": MQTT_TOPIC,
            "broker": MQTT_BROKER_HOST,
        }
    )


@app.get("/api/closet")
def list_closet_items() -> JSONResponse:
    try:
        inventory = fetch_closet_inventory()
        return JSONResponse(content=inventory)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/closet/{image_id}")
def get_closet_item(image_id: int) -> JSONResponse:
    try:
        inventory = fetch_closet_inventory()
        for item in inventory:
            if int(item.get("id", -1)) == image_id:
                return JSONResponse(content=item)
        raise HTTPException(status_code=404, detail="Item not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/recommend/llm")
def recommend_with_llm() -> JSONResponse:
    try:
        weather_info = get_weather_info()
        inventory = fetch_closet_inventory()
        recommendation = build_llm_recommendation(inventory, weather_info)
        return JSONResponse(content={"weather": weather_info, **recommendation})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
        inventory = fetch_closet_inventory()

        rows_html = []
        if inventory:
                for item in inventory:
                        image_id = item.get('id', '')
                        file_path = item.get('filepath', '')
                        image_name = Path(str(file_path)).name if file_path else ""
                        image_url = f"/images/download/{image_name}" if image_name else ""
                        preview_html = (
                                f"<div class='preview-card'>"
                                f"<button type='button' class='send-btn' onclick='sendSlot({image_id})'>📤</button>"
                                f"<img src=\"{image_url}\" alt=\"{image_name}\" class=\"preview\" />"
                                f"</div>"
                        ) if image_url else ""
                        rows_html.append(
                                "<tr>"
                                f"<td>{image_id}</td>"
                                f"<td>{preview_html}</td>"
                                f"<td>{file_path}</td>"
                                f"<td>{item.get('description', '')}</td>"
                                f"<td>{item.get('created_at', '')}</td>"
                                "</tr>"
                        )
                db_section = """
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Preview</th>
                                <th>File Path</th>
                                <th>Description</th>
                                <th>Created At</th>
                            </tr>
                        </thead>
                        <tbody>
                """ + "".join(rows_html) + """
                        </tbody>
                    </table>
                """
        else:
                db_section = "<div class='empty-state'>DB에 아직 저장된 항목이 없습니다.</div>"

        html = """
        <!doctype html>
        <html lang="ko">
            <head>
                <meta charset="utf-8" />
                <meta name="viewport" content="width=device-width, initial-scale=1" />
                <title>Smart Closet Backend</title>
                <style>
                    body {
                        font-family: Arial, sans-serif;
                        margin: 0;
                        min-height: 100vh;
                        display: grid;
                        place-items: center;
                        background: linear-gradient(135deg, #f7f7f7, #e8eef7);
                        color: #1f2937;
                    }
                    .card {
                        background: white;
                        padding: 32px;
                        border-radius: 16px;
                        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08);
                        max-width: 640px;
                        width: calc(100% - 32px);
                    }
                    .section {
                        margin-top: 24px;
                        padding-top: 20px;
                        border-top: 1px solid #e5e7eb;
                    }
                    h1 { margin-top: 0; }
                    .links a {
                        display: inline-block;
                        margin-right: 12px;
                        margin-bottom: 12px;
                        padding: 10px 14px;
                        border-radius: 999px;
                        background: #111827;
                        color: white;
                        text-decoration: none;
                    }
                    .links a.secondary { background: #2563eb; }
                    code { background: #f3f4f6; padding: 2px 6px; border-radius: 6px; }
                    form {
                        display: grid;
                        gap: 10px;
                        margin-top: 16px;
                    }
                    form label {
                        display: block;
                        font-weight: bold;
                        margin-bottom: 4px;
                    }
                    form select,
                    form input[type=file],
                    form button {
                        width: 100%;
                        padding: 10px;
                        border-radius: 10px;
                        border: 1px solid #d1d5db;
                        font-size: 14px;
                    }
                    form button {
                        background: #111827;
                        color: white;
                        border: none;
                        cursor: pointer;
                    }
                    form button:hover {
                        background: #1f2937;
                    }
                    table {
                        width: 100%;
                        border-collapse: collapse;
                        margin-top: 12px;
                        font-size: 14px;
                    }
                    th, td {
                        text-align: left;
                        border-bottom: 1px solid #e5e7eb;
                        padding: 10px 8px;
                        vertical-align: top;
                        word-break: break-word;
                    }
                    th {
                        background: #f9fafb;
                    }
                    .empty-state {
                        margin-top: 12px;
                        padding: 14px;
                        border-radius: 12px;
                        background: #f9fafb;
                        color: #6b7280;
                    }
                    .preview {
                        max-width: 120px;
                        max-height: 90px;
                        object-fit: contain;
                        border: 1px solid #e5e7eb;
                        border-radius: 8px;
                        background: #fff;
                    }
                    .preview-card {
                        display: inline-flex;
                        flex-direction: column;
                        gap: 8px;
                        align-items: flex-start;
                    }
                    .send-btn {
                        border: none;
                        border-radius: 999px;
                        background: #2563eb;
                        color: white;
                        padding: 6px 10px;
                        cursor: pointer;
                        font-size: 12px;
                    }
                </style>
                <script>
                    async function sendSlot(imageId) {
                        try {
                            const response = await fetch(`/api/send_id/${imageId}`, { method: 'POST' });
                            const data = await response.json();
                            if (response.ok) {
                                alert(`전송 완료: id=${data.id}`);
                            } else {
                                alert(`전송 실패: ${data.detail || 'unknown error'}`);
                            }
                        } catch (error) {
                            alert('전송 실패: MQTT 브로커 연결 오류');
                        }
                    }
                </script>
            </head>
            <body>
                <main class="card">
                    <h1>Smart Closet Backend</h1>
                    <p>서버가 정상 동작 중입니다.</p>
                    <p>확인할 주소:</p>
                    <div class="links">
                        <a href="/docs">API Docs</a>
                        <a class="secondary" href="/video_feed">MJPEG Stream</a>
                    </div>
                    <div class="section">
                        <h2>DB 상태</h2>
                        <p>추천 API: <code>/api/recommend</code></p>
                        <p>파일 업로드: id 1~10에 해당하는 JPG/PNG 이미지를 업로드하면 자동으로 저장 및 DB에 반영됩니다.</p>
                        <form id="upload-form" action="/api/upload/1" method="post" enctype="multipart/form-data">
                            <label for="upload-id">ID</label>
                            <select id="upload-id" name="id" onchange="document.getElementById('upload-form').action='/api/upload/'+this.value;">
                                <option value="1">1</option>
                                <option value="2">2</option>
                                <option value="3">3</option>
                                <option value="4">4</option>
                                <option value="5">5</option>
                                <option value="6">6</option>
                                <option value="7">7</option>
                                <option value="8">8</option>
                                <option value="9">9</option>
                                <option value="10">10</option>
                            </select>
                            <input type="file" name="file" accept="image/jpeg,image/png" required />
                            <button type="submit">업로드</button>
                        </form>
                        __DB_SECTION__
                    </div>
                </main>
            </body>
        </html>
        """
        return HTMLResponse(content=html.replace("__DB_SECTION__", db_section))


@app.get("/video_feed")
def video_feed() -> StreamingResponse:
    def generate_frames() -> Iterable[bytes]:
        while True:
            try:
                with frame_lock:
                    frame = None if latest_frame is None else latest_frame.copy()

                if frame is None:
                    frame = create_placeholder_frame("Camera unavailable")

                try:
                    success, buffer = cv2.imencode(".jpg", frame)
                    if not success:
                        time.sleep(STREAM_FRAME_DELAY_SECONDS)
                        continue
                except Exception:
                    time.sleep(STREAM_FRAME_DELAY_SECONDS)
                    continue

                payload = buffer.tobytes()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
                )
                time.sleep(STREAM_FRAME_DELAY_SECONDS)
            except Exception:
                time.sleep(STREAM_FRAME_DELAY_SECONDS)

    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/images/download/{filename}")
def download_image(filename: str) -> Response:
    try:
        safe_name = Path(filename).name
        file_path = IMAGE_DIR / safe_name

        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Image not found")

        return Response(content=file_path.read_bytes(), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Image not found")


@app.post("/api/upload/{image_id}")
def upload_image(image_id: int, file: UploadFile = File(...)) -> JSONResponse:
    if image_id not in VALID_QR_IDS:
        raise HTTPException(status_code=400, detail="Image ID must be between 1 and 10")

    if file.content_type not in {"image/jpeg", "image/jpg", "image/png"}:
        raise HTTPException(status_code=400, detail="File must be a JPG or PNG image")

    try:
        file_bytes = file.file.read()
        extension = ".jpg" if file.content_type != "image/png" else ".png"
        image_path = IMAGE_DIR / f"qr_{image_id}{extension}"
        with open(image_path, "wb") as out_file:
            out_file.write(file_bytes)

        description = f"Uploaded image for id {image_id}"
        upsert_image_record(image_id, str(image_path), description)
        return JSONResponse(content={"id": image_id, "filepath": str(image_path), "description": description})
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save uploaded image")


def fetch_closet_inventory() -> list[dict[str, Any]]:
    try:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT id, filepath, description, created_at FROM images ORDER BY id ASC"
            ).fetchall()

        return [dict(row) for row in rows]
    except Exception:
        return []


def strip_json_wrappers(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def get_recommendation_ids(inventory: list[dict[str, Any]], weather_info: dict[str, Any]) -> list[int]:
    if genai is None:
        return normalize_three_ids([item["id"] for item in inventory])

    prompt = (
        f"Given the current outdoor weather of {weather_info['temperature_c']}°C "
        f"({weather_info['conditions']}), look through this local closet inventory JSON data "
        "and select exactly 3 item IDs that best layer or match with the user's outfit. "
        "Return ONLY a raw JSON array of integers containing the selected IDs. Do not include markdown wraps or additional conversational text.\n\n"
        f"Inventory: {json.dumps(inventory, ensure_ascii=False)}"
    )

    try:
        client = get_gemini_client()
        if client is None:
            raise RuntimeError("Gemini client unavailable")

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw_text = getattr(response, "text", "") or ""
        cleaned = strip_json_wrappers(raw_text)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError("Gemini response is not a list")

        selected_ids: list[int] = []
        for value in parsed:
            try:
                parsed_value = int(value)
                if parsed_value not in selected_ids:
                    selected_ids.append(parsed_value)
            except Exception:
                continue

        valid_ids = {item["id"] for item in inventory}
        selected_ids = [value for value in selected_ids if value in valid_ids]

        if len(selected_ids) >= 3:
            return normalize_three_ids(selected_ids)
    except Exception:
        pass

    fallback_ids = [item["id"] for item in inventory]
    return normalize_three_ids(fallback_ids)


def normalize_three_ids(candidate_ids: list[int]) -> list[int]:
    unique_ids: list[int] = []
    for image_id in candidate_ids:
        if image_id not in unique_ids:
            unique_ids.append(image_id)

    if not unique_ids:
        return []

    index = 0
    while len(unique_ids) < 3:
        unique_ids.append(unique_ids[index % len(unique_ids)])
        index += 1

    return unique_ids[:3]


@app.get("/api/recommend")
def recommend() -> JSONResponse:
    weather_info = get_weather_info()
    inventory = fetch_closet_inventory()
    selected_ids = get_recommendation_ids(inventory, weather_info)

    response_payload = []
    for image_id in selected_ids:
        response_payload.append(
            {
                "id": image_id,
                "download_url": f"http://localhost:8000/images/download/qr_{image_id}.jpg",
            }
        )

    return JSONResponse(content=response_payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
