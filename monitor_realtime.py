#!/usr/bin/env python3
"""
Smart Closet 실시간 캡처 모니터
새로운 캡처가 되는지 실시간으로 감시
"""

import sqlite3
import time
import sys
from pathlib import Path
from datetime import datetime

db_path = Path("./local_gallery.db")

def monitor_captures(duration=60):
    """실시간 캡처 모니터링"""
    print("\n" + "="*70)
    print("🎥 실시간 캡처 모니터링 시작")
    print("="*70)
    print(f"모니터링 시간: {duration}초")
    print("카메라 앞에서 슬롯 번호 + 상의를 보여주세요!\n")
    
    start_time = time.time()
    last_count = 0
    
    while time.time() - start_time < duration:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM images")
            current_count = cursor.fetchone()[0]
            
            # 새로운 캡처 감지
            if current_count > last_count:
                # 최신 이미지 조회
                cursor.execute("""
                    SELECT id, filepath, created_at 
                    FROM images 
                    ORDER BY created_at DESC 
                    LIMIT 1
                """)
                row = cursor.fetchone()
                
                if row:
                    image_id, filepath, created_at = row
                    dt = datetime.fromtimestamp(created_at)
                    elapsed = int(time.time() - start_time)
                    
                    print(f"[{elapsed:2d}s] ✅ 새로운 캡처 감지!")
                    print(f"         슬롯 ID: {image_id}")
                    print(f"         파일: {Path(filepath).name}")
                    print(f"         시간: {dt.strftime('%H:%M:%S')}")
                    print()
                    
                last_count = current_count
            
            conn.close()
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n모니터링 종료")
            break
        except Exception as e:
            print(f"오류: {e}")
            time.sleep(1)
    
    print("\n" + "="*70)
    print(f"📊 최종 결과: {last_count}개 이미지 저장됨")
    print("="*70)

if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    monitor_captures(duration)
