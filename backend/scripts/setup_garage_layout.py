#!/usr/bin/env python3
"""Garage 레이아웃 설정 스크립트."""

import sys
from pathlib import Path
import time

# backend 디렉토리를 Python 경로에 추가
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import requests
from app.core.config import get_settings

settings = get_settings()

# Garage Admin API 설정
admin_url = settings.s3_endpoint.replace(":3900", ":3903")
admin_token = "CHANGE_ME_ADMIN_TOKEN"

headers = {
    "Authorization": f"Bearer {admin_token}",
    "Content-Type": "application/json",
}

print("Garage 레이아웃 설정 중...")

# 1. 노드 정보 조회
print("\n1. 노드 정보 조회 중...")
try:
    nodes_url = f"{admin_url}/v1/node"
    response = requests.get(nodes_url, headers=headers, timeout=10)
    if response.status_code == 200:
        nodes = response.json()
        print(f"  ✓ 노드 {len(nodes)}개 발견")
        if nodes:
            node_id = nodes[0].get("id")
            print(f"  노드 ID: {node_id}")
            
            # 2. 레이아웃 생성
            print("\n2. 레이아웃 생성 중...")
            layout_url = f"{admin_url}/v1/layout"
            layout_data = {
                "version": 1,
                "roles": [
                    {
                        "id": node_id,
                        "zone": "dc1",
                        "capacity": 1,
                        "tags": [],
                    }
                ],
                "stagedRoleChanges": {},
            }
            response = requests.post(layout_url, json=layout_data, headers=headers, timeout=10)
            if response.status_code == 200:
                print("  ✓ 레이아웃 생성 성공")
            elif response.status_code == 409:
                print("  ✓ 레이아웃 이미 존재함")
            else:
                print(f"  ✗ 레이아웃 생성 실패: {response.status_code} - {response.text}")
            
            # 3. 레이아웃 적용
            print("\n3. 레이아웃 적용 중...")
            apply_url = f"{admin_url}/v1/layout/apply"
            apply_data = {"version": 1}
            response = requests.post(apply_url, json=apply_data, headers=headers, timeout=10)
            if response.status_code == 200:
                print("  ✓ 레이아웃 적용 성공")
                print("  잠시 기다린 후 버킷을 생성하세요...")
            else:
                print(f"  ✗ 레이아웃 적용 실패: {response.status_code} - {response.text}")
        else:
            print("  ✗ 노드를 찾을 수 없음")
    else:
        print(f"  ✗ 노드 정보 조회 실패: {response.status_code} - {response.text}")
except requests.exceptions.RequestException as e:
    print(f"  ✗ 요청 실패: {e}")

print("\n설정 완료!")

