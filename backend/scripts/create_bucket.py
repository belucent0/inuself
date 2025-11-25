#!/usr/bin/env python3
"""Garage 버킷 및 접근 키 설정 스크립트."""

import sys
from pathlib import Path

# backend 디렉토리를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import requests
from app.core.config import get_settings

settings = get_settings()

# Garage Admin API 설정
admin_url = settings.s3_endpoint.replace(":3900", ":3903")
admin_token = "CHANGE_ME_ADMIN_TOKEN"  # garage.toml에서 설정한 값
bucket_name = settings.s3_bucket

headers = {
    "Authorization": f"Bearer {admin_token}",
    "Content-Type": "application/json",
}

print("Garage 버킷 및 접근 키 설정 중...")

# 1. 접근 키 생성 (이미 있으면 스킵)
print("\n1. 접근 키 확인/생성 중...")
access_key_name = settings.s3_access_key
access_key_secret = settings.s3_secret_key

# 접근 키 목록 조회
try:
    list_keys_url = f"{admin_url}/v1/key"
    response = requests.get(list_keys_url, headers=headers, timeout=10)
    if response.status_code == 200:
        keys = response.json()
        key_exists = any(k.get("name") == access_key_name for k in keys)
        if key_exists:
            print(f"  ✓ 접근 키 '{access_key_name}' 이미 존재함")
        else:
            # 접근 키 생성
            create_key_url = f"{admin_url}/v1/key"
            key_data = {"name": access_key_name}
            response = requests.post(create_key_url, json=key_data, headers=headers, timeout=10)
            if response.status_code == 200:
                created_key = response.json()
                print(f"  ✓ 접근 키 '{access_key_name}' 생성 성공")
                print(f"    Access Key ID: {created_key.get('accessKeyId')}")
                print(f"    Secret Key: {created_key.get('secretAccessKey')}")
                print("\n  ⚠ 주의: 생성된 Secret Key를 .env 파일에 설정하세요!")
            else:
                print(f"  ✗ 접근 키 생성 실패: {response.status_code} - {response.text}")
    else:
        print(f"  ✗ 접근 키 목록 조회 실패: {response.status_code} - {response.text}")
except requests.exceptions.RequestException as e:
    print(f"  ✗ 요청 실패: {e}")

# 2. 버킷 생성
print(f"\n2. 버킷 '{bucket_name}' 생성 중...")
try:
    create_bucket_url = f"{admin_url}/v1/bucket"
    bucket_data = {
        "name": bucket_name,
        "websiteAccess": {"enabled": False},
        "quotas": {"maxSize": None, "maxObjects": None},
    }
    response = requests.post(create_bucket_url, json=bucket_data, headers=headers, timeout=10)
    if response.status_code == 200:
        print(f"  ✓ 버킷 '{bucket_name}' 생성 성공")
    elif response.status_code == 409:
        print(f"  ✓ 버킷 '{bucket_name}' 이미 존재함")
    else:
        print(f"  ✗ 버킷 생성 실패: {response.status_code} - {response.text}")
except requests.exceptions.RequestException as e:
    print(f"  ✗ 요청 실패: {e}")

# 3. 접근 키에 버킷 권한 부여
print(f"\n3. 접근 키에 버킷 권한 부여 중...")
try:
    # 접근 키 ID 조회
    list_keys_url = f"{admin_url}/v1/key"
    response = requests.get(list_keys_url, headers=headers, timeout=10)
    if response.status_code == 200:
        keys = response.json()
        key_id = None
        for k in keys:
            if k.get("name") == access_key_name:
                key_id = k.get("accessKeyId")
                break
        
        if key_id:
            # 버킷 권한 설정 (읽기/쓰기)
            allow_bucket_url = f"{admin_url}/v1/bucket/{bucket_name}/allow"
            allow_data = {
                "accessKeyId": key_id,
                "permissions": ["read", "write"],
            }
            response = requests.post(allow_bucket_url, json=allow_data, headers=headers, timeout=10)
            if response.status_code == 200:
                print(f"  ✓ 접근 키에 버킷 권한 부여 성공")
            else:
                print(f"  ✗ 권한 부여 실패: {response.status_code} - {response.text}")
        else:
            print(f"  ✗ 접근 키 '{access_key_name}'를 찾을 수 없음")
except requests.exceptions.RequestException as e:
    print(f"  ✗ 요청 실패: {e}")

print("\n설정 완료!")

