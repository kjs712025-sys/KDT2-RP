#!/usr/bin/env python3
"""
Smart Closet Auto-Capture Test
숫자(1-10) + 상의 동시 감지 → 자동 캡처 검증
"""

import cv2
import numpy as np
import sys
from pathlib import Path

# app.py 경로 추가
sys.path.insert(0, str(Path(__file__).parent))

from app import detect_numeric_slot_id, yolo_model, process_detected_frame, IMAGE_DIR
import sqlite3
import time

def create_test_frame_with_digit_and_clothing(digit: int, clothing_label: str = "shirt") -> np.ndarray:
    """숫자 + 상의 시뮬레이션 프레임 생성 (회귀 테스트 방식)"""
    frame = np.ones((600, 500, 3), dtype=np.uint8) * 255
    
    # 왼쪽: 숫자 1-10 (회귀 테스트와 동일한 방식)
    if digit == 10:
        cv2.putText(frame, "10", (180, 240), cv2.FONT_HERSHEY_SIMPLEX, 4.2, (0, 0, 0), 10, cv2.LINE_AA)
    else:
        cv2.putText(frame, str(digit), (250, 240), cv2.FONT_HERSHEY_SIMPLEX, 5.0, (0, 0, 0), 10, cv2.LINE_AA)
    
    # 오른쪽: 의류 영역 (흰 박스로 표시)
    cv2.rectangle(frame, (300, 100), (480, 450), (100, 150, 200), -1)  # 옷 영역
    cv2.putText(frame, "clothing", (310, 280), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
    
    return frame

def test_simultaneous_detection(digit: int):
    """동시 감지 + 자동 캡처 테스트"""
    print(f"\n{'='*60}")
    print(f"테스트: 숫자 {digit} + 상의 동시 감지")
    print(f"{'='*60}")
    
    # 1. 테스트 프레임 생성
    frame = create_test_frame_with_digit_and_clothing(digit)
    
    # 2. 슬롯 숫자 감지
    detected_slot = detect_numeric_slot_id(frame)
    print(f"✓ 슬롯 숫자 감지: {detected_slot}")
    
    if detected_slot != digit:
        print(f"  ⚠️  예상: {digit}, 실제: {detected_slot}")
    
    # 3. 동시 감지 및 자동 캡처 프로세싱
    print(f"✓ 동시 감지 처리 시작...")
    process_detected_frame(frame)
    
    # 4. DB에 저장되었는지 확인
    time.sleep(1)  # Gemini 분석 시간
    
    db_path = Path("./local_gallery.db")
    if db_path.exists():
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 가장 최신 레코드 확인
            cursor.execute("""
                SELECT id, filepath, description 
                FROM images 
                WHERE id = ? 
                ORDER BY created_at DESC LIMIT 1
            """, (detected_slot,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                record_id, filepath, description = row
                print(f"✅ DB 저장 확인")
                print(f"   ID: {record_id}")
                print(f"   파일: {Path(filepath).name}")
                print(f"   설명: {description[:50]}...")
                return True
            else:
                print(f"⚠️  ID {detected_slot}의 레코드 미발견")
                return False
        except Exception as e:
            print(f"❌ DB 조회 오류: {e}")
            return False
    else:
        print(f"⚠️  DB 파일 없음 (초기 실행 상태)")
        return False

def test_all_digits():
    """모든 숫자 1-10 테스트"""
    print("\n" + "🎯 "*30)
    print("Smart Closet 자동 캡처 기능 검증")
    print("🎯 "*30)
    
    results = {}
    for digit in range(1, 11):
        try:
            results[digit] = test_simultaneous_detection(digit)
            time.sleep(2)  # API 호출 간격
        except Exception as e:
            print(f"❌ 테스트 실패 (숫자 {digit}): {e}")
            results[digit] = False
    
    # 요약
    print("\n" + "="*60)
    print("📊 테스트 요약")
    print("="*60)
    
    for digit, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - 숫자 {digit}")
    
    passed = sum(1 for r in results.values() if r)
    total = len(results)
    print(f"\n결과: {passed}/{total} 통과")
    
    if passed == total:
        print("\n🎉 모든 자동 캡처 테스트 통과!")
    else:
        print(f"\n⚠️  {total - passed}개 테스트 실패")

if __name__ == "__main__":
    print("\n🚀 Smart Closet 자동 캡처 기능 테스트")
    print(f"   DB: ./local_gallery.db")
    print(f"   이미지 저장소: {IMAGE_DIR}")
    print(f"   YOLO 모델: {'활성' if yolo_model else '비활성'}")
    
    if yolo_model is None:
        print("\n⚠️  YOLO 모델 로드 안 됨")
        print("   실제 카메라에서는 의류 감지가 작동합니다")
    
    # 전체 테스트 실행
    test_all_digits()
