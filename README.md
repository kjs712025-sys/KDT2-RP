# Smart Closet Backend

라즈베리파이 카메라로 옷장을 비추면 QR 코드와 상의(옷)를 동시에 인식해서
자동으로 사진을 저장하고, Gemini로 옷 설명을 생성하고, 날씨·캘린더에
맞는 옷을 추천해 MQTT로 물리 레일에 전송하는 스마트 옷장 백엔드입니다.

## 주요 기능

- **실시간 카메라 스트리밍**: MJPEG 스트림(`/video_feed`)과 밝기 조절 UI 제공
- **QR 코드 인식**: YOLOv8 기반 탐지기(qrdet/qreader)로 각도가 틀어지거나
  작은 QR도 인식 (1~10번 옷장 슬롯 번호로 사용)
- **의류(상의) 인식**: DeepFashion2로 학습된 YOLOv8 세그멘테이션 모델로
  카메라에 비친 상의를 탐지
- **자동 저장**: QR + 상의가 동시에 인식되면 사진을 저장하고 Gemini
  Vision으로 한국어 설명을 생성해 DB에 기록
- **옷 추천**: 저장된 설명 + 캘린더 일정(1순위) + 날씨(2순위)를 기준으로
  Gemini가 오늘 입을 옷 3벌을 선택하고, 선택된 옷은 MQTT로 레일에 전송
- **중복 방지**: 한 번 추천/전송된 옷은 다른 옷이 한 번 나온 뒤에야
  다시 추천되며, 옷장 전체를 한 바퀴 돌면 자동으로 초기화

## 기술 스택

- **FastAPI** + **Uvicorn** — API 서버
- **OpenCV** — 카메라 캡처, 영상 처리
- **Ultralytics YOLOv8** — QR 탐지(qreader/qrdet), 의류 탐지(DeepFashion2)
- **Google Gemini API** (`google-genai`) — 이미지 설명 생성, 추천 선택
- **SQLite** — 옷장 DB, 날씨/캘린더 컨텍스트, 추천 이력 저장
- **paho-mqtt** — 물리 레일 제어 신호 발행

## 시작하기

### 1. 의존성 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn[standard] opencv-python ultralytics qreader \
            google-genai paho-mqtt python-multipart
```

### 2. 환경 변수 설정 (`.env`)

```bash
GEMINI_API_KEY=발급받은_Gemini_API_키

# 아래는 선택 사항 (기본값 있음)
MQTT_BROKER_HOST=127.0.0.1
MQTT_BROKER_PORT=1883
MQTT_TOPIC=rail/target_qr
CAMERA_DEVICE=/dev/video0
STREAM_WIDTH=1280
STREAM_HEIGHT=720
STREAM_FPS=30
STREAM_JPEG_QUALITY=92
```

`.env`는 systemd 서비스의 `EnvironmentFile`로 로드됩니다(직접
`python app.py`로 실행할 경우 별도 환경변수 로더가 필요합니다).

### 3. 의류 인식 모델 준비

`models/deepfashion2_yolov8s-seg.pt` 파일이 필요합니다. QR 인식 모델은
`qreader` 최초 실행 시 자동으로 내려받습니다.

### 4. 서버 실행

```bash
python3 app.py
# 또는
uvicorn app:app --host 0.0.0.0 --port 8000
```

라즈베리파이에서는 `smart-closet-streaming.service` systemd 유닛으로
상시 실행합니다.

## API 요약

| 분류 | 엔드포인트 | 설명 |
|---|---|---|
| 스트리밍 | `GET /video_feed` | MJPEG 실시간 영상 |
| 스트리밍 | `GET /video_feed/view` | 밝기 조절 가능한 스트리밍 뷰어 |
| 스트리밍 | `GET/POST /api/camera/brightness` | 밝기 조회/설정 |
| 옷장 | `GET /api/closet`, `GET /api/closet/{id}` | 저장된 옷 목록/상세 조회 |
| 옷장 | `POST /api/upload/{id}` | 사진 수동 업로드 (1~10번 슬롯) |
| 옷장 | `GET /images/download/{filename}` | 저장된 사진 다운로드 |
| 컨텍스트 | `GET/POST /api/context` | 날씨·캘린더 컨텍스트 조회/저장 |
| 추천 | `GET /api/recommend` | 오늘의 추천 3벌 (자동 MQTT 전송 + 중복 제외) |
| 추천 | `GET /api/recommend/context` | 저장된 컨텍스트 기반 추천 |
| 추천 | `GET/POST /api/recommend/different` | 특정 id 제외 후 재추천 |
| 추천 | `GET /api/recommend/analyze` | 추천 + 옷별 라벨/요약 포함 |
| 레일 | `GET/POST /api/send_id/{id}` | 특정 슬롯을 레일로 즉시 전송 |

## 인식 파이프라인 구조

카메라 캡처와 QR/의류 인식은 서로 다른 백그라운드 스레드에서 동작합니다.

- `camera_loop`: 카메라에서 프레임을 읽어 스트리밍용 최신 프레임을 갱신
- `detection_loop`: 가장 최근 프레임에 대해 QR 인식 → 의류 인식을 순서대로 반복

YOLO 추론이 무거워서(추론 1건당 약 1~2초) 스트리밍 루프 안에서 직접
실행하면 화면이 멈추기 때문에 분리했으며, 두 인식을 한 스레드 안에서
순차 실행해 라즈베리파이의 제한된 CPU 코어를 스트리밍용으로 남겨둡니다.
