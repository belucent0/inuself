#!/usr/bin/env python
"""서비스 관리 TUI (Text User Interface) - Docker Compose + PM2."""
import subprocess
import sys
import os
import time
from pathlib import Path

# Windows 환경설정
if os.name == "nt":
    # Windows에서 UTF-8 출력 설정
    os.system("chcp 65001 >nul 2>&1")

# 프로젝트 루트 디렉토리
PROJECT_ROOT = Path(__file__).parent.absolute()

# ANSI 색상 코드
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"


def run_command(cmd, **kwargs):
    """명령 실행 (인코딩 처리)."""
    # Windows에서 pm2, docker 같은 명령어는 cmd /c를 통해 실행
    if os.name == "nt" and isinstance(cmd, list) and len(cmd) > 0:
        if cmd[0] in ["pm2", "docker"]:
            cmd = ["cmd", "/c"] + cmd
    
    return subprocess.run(
        cmd,
        encoding="utf-8",
        errors="replace",
        **kwargs
    )


def clear_screen():
    """화면 지우기."""
    os.system("cls" if os.name == "nt" else "clear")


def check_docker_services():
    """Docker Compose 서비스 상태 확인."""
    try:
        result = run_command(
            ["docker", "compose", "ps", "--format", "json"],
            capture_output=True,
            timeout=5,
            cwd=str(PROJECT_ROOT),
        )
        
        if result.returncode == 0 and result.stdout.strip():
            import json
            services = []
            for line in result.stdout.strip().split("\n"):
                try:
                    service = json.loads(line)
                    services.append({
                        "name": service.get("Service", "unknown"),
                        "state": service.get("State", "unknown"),
                        "status": service.get("Status", ""),
                    })
                except json.JSONDecodeError:
                    pass
            return services
        return []
    except Exception as e:
        return []


def check_pm2_services():
    """PM2 서비스 상태 확인."""
    try:
        result = run_command(
            ["pm2", "jlist"],
            capture_output=True,
            timeout=5,
        )
        
        if result.returncode == 0 and result.stdout.strip():
            import json
            services = json.loads(result.stdout)
            return [{
                "name": s.get("name", "unknown"),
                "status": s.get("pm2_env", {}).get("status", "unknown"),
                "pid": s.get("pid", 0),
            } for s in services]
        return []
    except Exception as e:
        return []


def check_lmstudio():
    """LM Studio 상태 확인."""
    try:
        import httpx
        with httpx.Client(timeout=3.0) as client:
            response = client.get("http://localhost:1234/v1/models")
            return response.status_code == 200
    except Exception:
        return False


def start_all_services():
    """모든 서비스 시작."""
    print(f"\n{CYAN}=== 모든 서비스 시작 중... ==={RESET}\n")
    
    # Docker Compose 시작
    print(f"{BLUE}[1/2] Docker Compose 서비스 시작 중...{RESET}")
    result = run_command(["docker", "compose", "up", "-d"], cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        print(f"{GREEN}✓ Docker Compose 시작 완료{RESET}")
    else:
        print(f"{RED}✗ Docker Compose 시작 실패{RESET}")
    
    time.sleep(1)
    
    # PM2 시작
    print(f"\n{BLUE}[2/2] PM2 워커 시작 중...{RESET}")
    config_file = PROJECT_ROOT / "ecosystem.config.js"
    result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
    else:
        # 이미 실행 중일 수 있으므로 재시작 시도
        run_command(["pm2", "restart", "celery-worker", "--update-env"])
        print(f"{YELLOW}⚠ PM2 워커 재시작됨 (이미 실행 중이었음){RESET}")
    
    print(f"\n{GREEN}모든 서비스가 시작되었습니다!{RESET}")
    input("\n계속하려면 Enter를 누르세요...")


def stop_all_services():
    """모든 서비스 중지."""
    print(f"\n{CYAN}=== 모든 서비스 중지 중... ==={RESET}\n")
    
    # PM2 중지
    print(f"{BLUE}[1/2] PM2 워커 중지 중...{RESET}")
    result = run_command(["pm2", "stop", "celery-worker"])
    if result.returncode == 0:
        print(f"{GREEN}✓ PM2 워커 중지 완료{RESET}")
    else:
        print(f"{YELLOW}⚠ PM2 워커 중지 실패 (이미 중지되었을 수 있음){RESET}")
    
    time.sleep(1)
    
    # Docker Compose 중지
    print(f"\n{BLUE}[2/2] Docker Compose 서비스 중지 중...{RESET}")
    result = run_command(["docker", "compose", "down"], cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        print(f"{GREEN}✓ Docker Compose 중지 완료{RESET}")
    else:
        print(f"{RED}✗ Docker Compose 중지 실패{RESET}")
    
    print(f"\n{GREEN}모든 서비스가 중지되었습니다!{RESET}")
    input("\n계속하려면 Enter를 누르세요...")


def restart_all_services():
    """모든 서비스 재시작."""
    print(f"\n{CYAN}=== 모든 서비스 재시작 중... ==={RESET}\n")
    
    # PM2 재시작
    print(f"{BLUE}[1/2] PM2 워커 재시작 중...{RESET}")
    result = run_command(["pm2", "restart", "celery-worker", "--update-env"])
    if result.returncode == 0:
        print(f"{GREEN}✓ PM2 워커 재시작 완료{RESET}")
    else:
        print(f"{RED}✗ PM2 워커 재시작 실패{RESET}")
    
    time.sleep(1)
    
    # Docker Compose 재시작
    print(f"\n{BLUE}[2/2] Docker Compose 서비스 재시작 중...{RESET}")
    result = run_command(["docker", "compose", "restart"], cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        print(f"{GREEN}✓ Docker Compose 재시작 완료{RESET}")
    else:
        print(f"{RED}✗ Docker Compose 재시작 실패{RESET}")
    
    print(f"\n{GREEN}모든 서비스가 재시작되었습니다!{RESET}")
    input("\n계속하려면 Enter를 누르세요...")


def start_docker_service():
    """개별 Docker 서비스 시작."""
    clear_screen()
    print(f"{BOLD}{CYAN}=== Docker 서비스 시작 ==={RESET}\n")
    
    services = check_docker_services()
    all_services = ["backend", "frontend", "postgres", "redis", "minio", "nginx", "redis-insight"]
    
    print("시작할 서비스를 선택하세요:\n")
    for i, svc in enumerate(all_services, 1):
        # 현재 상태 확인
        status = "중지됨"
        for s in services:
            if s["name"] == svc:
                status = "실행 중" if s["state"] == "running" else s["state"]
                break
        
        status_color = GREEN if status == "실행 중" else RED
        print(f"  {i}. {svc.ljust(15)} [{status_color}{status}{RESET}]")
    
    print(f"\n  {len(all_services) + 1}. 모든 Docker 서비스")
    print("  0. 뒤로")
    
    choice = input(f"\n{BOLD}선택: {RESET}")
    
    try:
        choice_num = int(choice)
        if choice_num == 0:
            return
        elif 1 <= choice_num <= len(all_services):
            service_name = all_services[choice_num - 1]
            print(f"\n{BLUE}{service_name} 서비스 시작 중...{RESET}")
            result = run_command(["docker", "compose", "up", "-d", service_name], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ {service_name} 시작 완료{RESET}")
            else:
                print(f"{RED}✗ {service_name} 시작 실패{RESET}")
        elif choice_num == len(all_services) + 1:
            print(f"\n{BLUE}모든 Docker 서비스 시작 중...{RESET}")
            result = run_command(["docker", "compose", "up", "-d"], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ 모든 Docker 서비스 시작 완료{RESET}")
            else:
                print(f"{RED}✗ Docker 서비스 시작 실패{RESET}")
        
        input("\n계속하려면 Enter를 누르세요...")
    except ValueError:
        print(f"{RED}잘못된 입력입니다.{RESET}")
        time.sleep(1)


def stop_docker_service():
    """개별 Docker 서비스 중지."""
    clear_screen()
    print(f"{BOLD}{CYAN}=== Docker 서비스 중지 ==={RESET}\n")
    
    services = check_docker_services()
    all_services = ["backend", "frontend", "postgres", "redis", "minio", "nginx", "redis-insight"]
    
    print("중지할 서비스를 선택하세요:\n")
    for i, svc in enumerate(all_services, 1):
        # 현재 상태 확인
        status = "중지됨"
        for s in services:
            if s["name"] == svc:
                status = "실행 중" if s["state"] == "running" else s["state"]
                break
        
        status_color = GREEN if status == "실행 중" else RED
        print(f"  {i}. {svc.ljust(15)} [{status_color}{status}{RESET}]")
    
    print(f"\n  {len(all_services) + 1}. 모든 Docker 서비스")
    print("  0. 뒤로")
    
    choice = input(f"\n{BOLD}선택: {RESET}")
    
    try:
        choice_num = int(choice)
        if choice_num == 0:
            return
        elif 1 <= choice_num <= len(all_services):
            service_name = all_services[choice_num - 1]
            print(f"\n{BLUE}{service_name} 서비스 중지 중...{RESET}")
            result = run_command(["docker", "compose", "stop", service_name], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ {service_name} 중지 완료{RESET}")
            else:
                print(f"{RED}✗ {service_name} 중지 실패{RESET}")
        elif choice_num == len(all_services) + 1:
            print(f"\n{BLUE}모든 Docker 서비스 중지 중...{RESET}")
            result = run_command(["docker", "compose", "down"], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ 모든 Docker 서비스 중지 완료{RESET}")
            else:
                print(f"{RED}✗ Docker 서비스 중지 실패{RESET}")
        
        input("\n계속하려면 Enter를 누르세요...")
    except ValueError:
        print(f"{RED}잘못된 입력입니다.{RESET}")
        time.sleep(1)


def restart_docker_service():
    """개별 Docker 서비스 재시작."""
    clear_screen()
    print(f"{BOLD}{CYAN}=== Docker 서비스 재시작 ==={RESET}\n")
    
    services = check_docker_services()
    all_services = ["backend", "frontend", "postgres", "redis", "minio", "nginx", "redis-insight"]
    
    print("재시작할 서비스를 선택하세요:\n")
    for i, svc in enumerate(all_services, 1):
        # 현재 상태 확인
        status = "중지됨"
        for s in services:
            if s["name"] == svc:
                status = "실행 중" if s["state"] == "running" else s["state"]
                break
        
        status_color = GREEN if status == "실행 중" else RED
        print(f"  {i}. {svc.ljust(15)} [{status_color}{status}{RESET}]")
    
    print(f"\n  {len(all_services) + 1}. 모든 Docker 서비스")
    print("  0. 뒤로")
    
    choice = input(f"\n{BOLD}선택: {RESET}")
    
    try:
        choice_num = int(choice)
        if choice_num == 0:
            return
        elif 1 <= choice_num <= len(all_services):
            service_name = all_services[choice_num - 1]
            print(f"\n{BLUE}{service_name} 서비스 재시작 중...{RESET}")
            result = run_command(["docker", "compose", "restart", service_name], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ {service_name} 재시작 완료{RESET}")
            else:
                print(f"{RED}✗ {service_name} 재시작 실패{RESET}")
        elif choice_num == len(all_services) + 1:
            print(f"\n{BLUE}모든 Docker 서비스 재시작 중...{RESET}")
            result = run_command(["docker", "compose", "restart"], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ 모든 Docker 서비스 재시작 완료{RESET}")
            else:
                print(f"{RED}✗ Docker 서비스 재시작 실패{RESET}")
        
        input("\n계속하려면 Enter를 누르세요...")
    except ValueError:
        print(f"{RED}잘못된 입력입니다.{RESET}")
        time.sleep(1)


def manage_pm2_worker():
    """PM2 워커 관리."""
    clear_screen()
    print(f"{BOLD}{CYAN}=== PM2 워커 관리 ==={RESET}\n")
    
    pm2_services = check_pm2_services()
    is_running = any(s["status"] == "online" for s in pm2_services)
    
    status_text = f"{GREEN}실행 중{RESET}" if is_running else f"{RED}중지됨{RESET}"
    print(f"현재 상태: {status_text}\n")
    
    print("1. PM2 워커 시작")
    print("2. PM2 워커 중지")
    print("3. PM2 워커 재시작")
    print("4. PM2 워커 로그 확인")
    print("0. 뒤로")
    
    choice = input(f"\n{BOLD}선택: {RESET}")
    
    if choice == "1":
        print(f"\n{BLUE}PM2 워커 시작 중...{RESET}")
        config_file = PROJECT_ROOT / "ecosystem.config.js"
        result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
        if result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
        else:
            run_command(["pm2", "restart", "celery-worker", "--update-env"])
            print(f"{YELLOW}⚠ PM2 워커 재시작됨{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "2":
        print(f"\n{BLUE}PM2 워커 중지 중...{RESET}")
        result = run_command(["pm2", "stop", "celery-worker"])
        if result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 중지 완료{RESET}")
        else:
            print(f"{YELLOW}⚠ PM2 워커 중지 실패{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "3":
        print(f"\n{BLUE}PM2 워커 재시작 중...{RESET}")
        result = run_command(["pm2", "restart", "celery-worker", "--update-env"])
        if result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 재시작 완료{RESET}")
        else:
            print(f"{RED}✗ PM2 워커 재시작 실패{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "4":
        subprocess.run(["pm2", "logs", "celery-worker"])


def show_logs():
    """로그 확인 메뉴."""
    clear_screen()
    print(f"{BOLD}{CYAN}=== 로그 확인 ==={RESET}\n")
    print("1. Docker Compose 로그 (최근 50줄)")
    print("2. Docker Compose 로그 (실시간, Ctrl+C로 종료)")
    print("3. PM2 로그 (최근 50줄)")
    print("4. PM2 로그 (실시간, Ctrl+C로 종료)")
    print("5. Backend 로그 (최근 50줄)")
    print("6. Backend 로그 (실시간, Ctrl+C로 종료)")
    print("7. Frontend 로그 (최근 50줄)")
    print("8. Frontend 로그 (실시간, Ctrl+C로 종료)")
    print("0. 뒤로")
    
    choice = input(f"\n{BOLD}선택: {RESET}")
    
    if choice == "0":
        return
    
    try:
        if choice == "1":
            print(f"\n{YELLOW}최근 로그를 불러오는 중...{RESET}\n")
            result = run_command(
                ["docker", "compose", "logs", "--tail=50"],
                cwd=str(PROJECT_ROOT)
            )
            input("\n계속하려면 Enter를 누르세요...")
        elif choice == "2":
            print(f"\n{YELLOW}실시간 로그를 보는 중... (Ctrl+C로 종료){RESET}\n")
            subprocess.run(["docker", "compose", "logs", "-f"], cwd=str(PROJECT_ROOT))
        elif choice == "3":
            print(f"\n{YELLOW}최근 로그를 불러오는 중...{RESET}\n")
            result = run_command(["pm2", "logs", "--lines", "50", "--nostream"])
            input("\n계속하려면 Enter를 누르세요...")
        elif choice == "4":
            print(f"\n{YELLOW}실시간 로그를 보는 중... (Ctrl+C로 종료){RESET}\n")
            subprocess.run(["pm2", "logs"])
        elif choice == "5":
            print(f"\n{YELLOW}최근 로그를 불러오는 중...{RESET}\n")
            result = run_command(
                ["docker", "compose", "logs", "--tail=50", "backend"],
                cwd=str(PROJECT_ROOT)
            )
            input("\n계속하려면 Enter를 누르세요...")
        elif choice == "6":
            print(f"\n{YELLOW}실시간 로그를 보는 중... (Ctrl+C로 종료){RESET}\n")
            subprocess.run(["docker", "compose", "logs", "-f", "backend"], cwd=str(PROJECT_ROOT))
        elif choice == "7":
            print(f"\n{YELLOW}최근 로그를 불러오는 중...{RESET}\n")
            result = run_command(
                ["docker", "compose", "logs", "--tail=50", "frontend"],
                cwd=str(PROJECT_ROOT)
            )
            input("\n계속하려면 Enter를 누르세요...")
        elif choice == "8":
            print(f"\n{YELLOW}실시간 로그를 보는 중... (Ctrl+C로 종료){RESET}\n")
            subprocess.run(["docker", "compose", "logs", "-f", "frontend"], cwd=str(PROJECT_ROOT))
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}로그 확인을 종료합니다...{RESET}")
        time.sleep(1)
        return
    except Exception as e:
        print(f"\n{RED}로그 확인 중 오류 발생: {e}{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
        return


def start_lmstudio():
    """LM Studio 시작."""
    print(f"\n{CYAN}=== LM Studio 시작 ==={RESET}\n")
    
    # LM Studio가 이미 실행 중인지 확인
    if check_lmstudio():
        print(f"{GREEN}✓ LM Studio가 이미 실행 중입니다!{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
        return
    
    print(f"{BLUE}LM Studio를 시작합니다...{RESET}\n")
    
    # start_lmstudio.bat 실행
    start_script = PROJECT_ROOT / "start_lmstudio.bat"
    if start_script.exists():
        result = run_command([str(start_script)], cwd=str(PROJECT_ROOT))
        
        if result.returncode == 0:
            print(f"\n{YELLOW}⚠ LM Studio 시작 후 다음을 확인하세요:{RESET}")
            print(f"  1. 모델을 로드하세요 (예: gpt-oss-20b)")
            print(f"  2. Local Server를 시작하세요 (포트 1234)")
            print(f"\n{BLUE}10초 후 상태를 확인합니다...{RESET}")
            time.sleep(10)
            
            # 다시 확인
            if check_lmstudio():
                print(f"{GREEN}✓ LM Studio가 정상적으로 실행 중입니다!{RESET}")
            else:
                print(f"{YELLOW}⚠ LM Studio API가 아직 응답하지 않습니다.{RESET}")
                print(f"  모델을 로드하고 Local Server를 시작했는지 확인하세요.")
        else:
            print(f"\n{RED}✗ LM Studio 실행 파일을 찾을 수 없습니다.{RESET}")
            print(f"\n{YELLOW}수동으로 LM Studio를 시작하세요:{RESET}")
            print(f"  1. Windows 시작 메뉴에서 'LM Studio' 검색")
            print(f"  2. LM Studio 애플리케이션 실행")
            print(f"  3. 모델 로드 (예: gpt-oss-20b)")
            print(f"  4. Local Server 시작 (포트 1234)")
            print(f"\n{CYAN}또는 start_lmstudio.bat 파일을 편집하여 올바른 경로를 추가하세요.{RESET}")
    else:
        print(f"{RED}✗ start_lmstudio.bat 파일을 찾을 수 없습니다.{RESET}")
        print(f"\n{YELLOW}수동으로 LM Studio를 시작하세요:{RESET}")
        print(f"  1. Windows 시작 메뉴에서 'LM Studio' 검색")
        print(f"  2. LM Studio 애플리케이션 실행")
        print(f"  3. 모델 로드 (예: gpt-oss-20b)")
        print(f"  4. Local Server 시작 (포트 1234)")
    
    input("\n계속하려면 Enter를 누르세요...")


def display_status():
    """서비스 상태 표시."""
    clear_screen()
    
    print(f"{BOLD}{CYAN}╔═══════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║         서비스 관리 - Docker Compose + PM2               ║{RESET}")
    print(f"{BOLD}{CYAN}╚═══════════════════════════════════════════════════════════╝{RESET}\n")
    
    # Docker Compose 서비스 상태
    print(f"{BOLD}{BLUE}━━━ Docker Compose 서비스 ━━━{RESET}")
    docker_services = check_docker_services()
    
    if docker_services:
        for service in docker_services:
            name = service["name"].ljust(15)
            state = service["state"]
            status = service["status"]
            
            if state == "running":
                status_icon = f"{GREEN}● 실행 중{RESET}"
            elif state == "exited":
                status_icon = f"{RED}○ 중지됨{RESET}"
            else:
                status_icon = f"{YELLOW}◐ {state}{RESET}"
            
            print(f"  {name} {status_icon} {RESET}({status})")
    else:
        print(f"  {YELLOW}서비스가 실행 중이지 않습니다.{RESET}")
    
    # PM2 서비스 상태
    print(f"\n{BOLD}{BLUE}━━━ PM2 워커 ━━━{RESET}")
    pm2_services = check_pm2_services()
    
    if pm2_services:
        for service in pm2_services:
            name = service["name"].ljust(15)
            status = service["status"]
            pid = service["pid"]
            
            if status == "online":
                status_icon = f"{GREEN}● 실행 중{RESET}"
            elif status == "stopped":
                status_icon = f"{RED}○ 중지됨{RESET}"
            else:
                status_icon = f"{YELLOW}◐ {status}{RESET}"
            
            print(f"  {name} {status_icon} (PID: {pid})")
    else:
        print(f"  {YELLOW}워커가 실행 중이지 않습니다.{RESET}")
    
    # LM Studio 상태
    print(f"\n{BOLD}{BLUE}━━━ LM Studio ━━━{RESET}")
    lmstudio_running = check_lmstudio()
    
    if lmstudio_running:
        print(f"  {'LM Studio'.ljust(15)} {GREEN}● 실행 중{RESET} (http://localhost:1234)")
    else:
        print(f"  {'LM Studio'.ljust(15)} {RED}○ 중지됨{RESET} {YELLOW}⚠ 요약 기능이 작동하지 않습니다{RESET}")
    
    print(f"\n{BOLD}{CYAN}{'─' * 59}{RESET}")


def main_menu():
    """메인 메뉴."""
    while True:
        display_status()
        
        print(f"\n{BOLD}메뉴:{RESET}")
        print(f"{GREEN}  1. 모든 서비스 시작{RESET}")
        print(f"{RED}  2. 모든 서비스 중지{RESET}")
        print(f"{YELLOW}  3. 모든 서비스 재시작{RESET}")
        print(f"{BLUE}  4. Docker 서비스 개별 시작{RESET}")
        print(f"{BLUE}  5. Docker 서비스 개별 중지{RESET}")
        print(f"{BLUE}  6. Docker 서비스 개별 재시작{RESET}")
        print(f"{CYAN}  7. PM2 워커 관리{RESET}")
        print(f"{CYAN}  8. 로그 확인{RESET}")
        print(f"{CYAN}  9. LM Studio 시작{RESET}")
        print(f"  r. 상태 새로고침")
        print(f"  0. 종료")
        
        choice = input(f"\n{BOLD}선택: {RESET}")
        
        if choice == "1":
            start_all_services()
        elif choice == "2":
            stop_all_services()
        elif choice == "3":
            restart_all_services()
        elif choice == "4":
            start_docker_service()
        elif choice == "5":
            stop_docker_service()
        elif choice == "6":
            restart_docker_service()
        elif choice == "7":
            manage_pm2_worker()
        elif choice == "8":
            show_logs()
        elif choice == "9":
            start_lmstudio()
        elif choice.lower() == "r":
            continue
        elif choice == "0":
            print(f"\n{GREEN}종료합니다.{RESET}")
            sys.exit(0)
        else:
            print(f"\n{RED}잘못된 선택입니다.{RESET}")
            time.sleep(1)


if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print(f"\n\n{GREEN}종료합니다.{RESET}")
        sys.exit(0)

