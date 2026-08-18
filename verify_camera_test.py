#!/usr/bin/env python3
"""
Smart Closet 카메라 테스트 검증
숫자(1-10) + 상의 캡처 결과 확인
"""

import sqlite3
from pathlib import Path
import json

DB_PATH = Path("./local_gallery.db")
IMAGE_DIR = Path("./saved_images")

def check_database():
    """DB 저장 현황 확인"""
    print("\n" + "="*70)
    print("💾 데이터베이스 검증")
    print("="*70)
    
    if not DB_PATH.exists():
        print("❌ DB 파일 없음")
        return False
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 전체 이미지 개수
        cursor.execute("SELECT COUNT(*) as total FROM images")
        total = cursor.fetchone()["total"]
        
        print(f"✅ DB 연결 성공")
        print(f"   총 저장된 이미지: {total}개")
        
        # 슬롯 1-10 확인
        print(f"\n🔍 슬롯별 저장 현황:")
        cursor.execute("""
            SELECT id, filepath, description, created_at 
            FROM images 
            WHERE id BETWEEN 1 AND 10
            ORDER BY id
        """)
        
        saved_slots = {}
        for row in cursor.fetchall():
            saved_slots[row["id"]] = {
                "filepath": row["filepath"],
                "description": row["description"][:60] + "..." if row["description"] else "",
                "created_at": row["created_at"]
            }
            print(f"   ✓ ID {row['id']:2d}: {Path(row['filepath']).name:15s} | {row['description'][:50]}...")
        
        conn.close()
        
        # 미저장 슬롯 확인
        missing = [i for i in range(1, 11) if i not in saved_slots]
        if missing:
            print(f"\n⚠️  미저장 슬롯: {missing}")
            return len(missing) == 0
        else:
            print(f"\n✅ 모든 슬롯 1-10 저장됨!")
            return True
            
    except Exception as e:
        print(f"❌ DB 조회 오류: {e}")
        return False

def check_saved_images():
    """저장된 이미지 파일 확인"""
    print("\n" + "="*70)
    print("🖼️  저장된 이미지 파일 확인")
    print("="*70)
    
    if not IMAGE_DIR.exists():
        print(f"❌ 이미지 디렉토리 없음: {IMAGE_DIR}")
        return False
    
    qr_files = sorted(IMAGE_DIR.glob("qr_*.jpg"))
    
    if not qr_files:
        print(f"❌ 저장된 이미지 없음")
        return False
    
    print(f"✅ {len(qr_files)}개 이미지 파일 발견\n")
    
    saved_ids = set()
    for file in qr_files:
        try:
            size_kb = file.stat().st_size / 1024
            slot_id = int(file.stem.split("_")[1])
            saved_ids.add(slot_id)
            print(f"   ✓ {file.name:15s} | {size_kb:6.1f} KB")
        except Exception as e:
            print(f"   ⚠️  {file.name}: {e}")
    
    # 슬롯 1-10 파일 검증
    print(f"\n📋 슬롯 파일 상태:")
    for i in range(1, 11):
        if i in saved_ids:
            print(f"   ✓ qr_{i}.jpg")
        else:
            print(f"   ✗ qr_{i}.jpg (없음)")
    
    return len(saved_ids) == 10

def check_image_descriptions():
    """이미지 설명(Gemini 분석) 확인"""
    print("\n" + "="*70)
    print("📝 이미지 설명 (Gemini 분석) 확인")
    print("="*70)
    
    if not DB_PATH.exists():
        print("❌ DB 파일 없음")
        return False
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, description 
            FROM images 
            WHERE id BETWEEN 1 AND 10
            ORDER BY id
        """)
        
        analyzed_count = 0
        for row in cursor.fetchall():
            desc = row["description"] or ""
            status = "✓" if desc and desc != "Gemini 분석 불가" else "⚠️"
            print(f"   {status} ID {row['id']:2d}: {desc[:60]}...")
            if desc and desc != "Gemini 분석 불가":
                analyzed_count += 1
        
        print(f"\n✅ Gemini 분석 완료: {analyzed_count}/10")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ 오류: {e}")
        return False

def generate_summary():
    """최종 요약"""
    print("\n" + "="*70)
    print("📊 테스트 최종 결과")
    print("="*70)
    
    db_ok = check_database()
    files_ok = check_saved_images()
    desc_ok = check_image_descriptions()
    
    print("\n" + "="*70)
    print("✅ 검증 요약")
    print("="*70)
    print(f"{'✅' if db_ok else '❌'} 데이터베이스 저장")
    print(f"{'✅' if files_ok else '❌'} 이미지 파일")
    print(f"{'✅' if desc_ok else '❌'} Gemini 분석")
    
    if db_ok and files_ok:
        print("\n🎉 테스트 성공! 모든 슬롯이 캡처되고 저장되었습니다!")
        print("\n📱 웹 인터페이스에서 확인:")
        print("   http://192.168.137.36:8000/api/closet")
        return True
    else:
        print("\n⚠️  일부 데이터 누락. 다시 테스트하세요:")
        print("   1. 슬롯 번호(1-10)와 상의를 동시에 카메라에 보이기")
        print("   2. 각 슬롯마다 3초 이상 유지")
        print("   3. 이후 다시 검증 실행")
        return False

if __name__ == "__main__":
    import sys
    
    print("\n🚀 Smart Closet 카메라 테스트 검증")
    print(f"   DB: {DB_PATH.resolve()}")
    print(f"   이미지: {IMAGE_DIR.resolve()}")
    
    success = generate_summary()
    sys.exit(0 if success else 1)
