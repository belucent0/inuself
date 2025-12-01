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


def check_celery_worker_status():
    """Celery 워커 상태 확인 (실제 작업 처리 여부)."""
    print(f"\n{CYAN}=== Celery 워커 상태 확인 ==={RESET}\n")
    
    try:
        # Python 스크립트를 실행하여 Celery Inspector 사용
        backend_dir = PROJECT_ROOT / "backend"
        check_script = f"""
import sys
import os
sys.path.insert(0, r'{str(backend_dir)}')

try:
    from app.worker.celery_app import celery_app
    
    inspector = celery_app.control.inspect()
    
    # 활성 워커 확인
    active_workers = inspector.active()
    stats = inspector.stats()
    registered = inspector.registered()
    
    print("=" * 60)
    print("Celery 워커 상태")
    print("=" * 60)
    
    if not active_workers and not stats:
        print("\\n✗ 워커가 실행 중이지 않습니다.")
        sys.exit(1)
    
    # 워커 정보 출력
    for worker_name, worker_stats in (stats or {{}}).items():
        print(f"\\n워커: {{worker_name}}")
        print(f"  상태: ● 실행 중")
        if worker_stats:
            pool_size = worker_stats.get('pool', {{}}).get('max-concurrency', 'N/A')
            print(f"  Pool 크기: {{pool_size}}")
    
    # 활성 작업 확인
    active_tasks = 0
    if active_workers:
        for worker_name, tasks in active_workers.items():
            active_tasks += len(tasks)
            if tasks:
                print(f"\\n활성 작업 ({{worker_name}}): {{len(tasks)}}개")
                for task in tasks[:3]:
                    task_name = task.get('name', 'unknown')
                    task_id = task.get('id', 'unknown')[:16]
                    print(f"  - {{task_name}}: {{task_id}}...")
    
    # 등록된 태스크 확인
    if registered:
        for worker_name, task_list in registered.items():
            if 'process_asr_task' in task_list and 'process_llm_task' in task_list:
                print(f"\\n✓ 필수 태스크 등록됨: process_asr_task, process_llm_task")
            else:
                print(f"\\n⚠ 일부 태스크가 등록되지 않았습니다.")
    
    if active_tasks == 0:
        print(f"\\n⚠ 현재 실행 중인 작업이 없습니다.")
        print(f"  (워커는 정상이지만 작업이 대기 중이거나 없습니다)")
    
    print("\\n" + "=" * 60)
    sys.exit(0)
    
except Exception as e:
    print(f"\\n✗ Celery 워커 상태 확인 실패: {{e}}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""
        
        # 임시 스크립트 파일 생성
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(check_script)
            temp_script = f.name
        
        try:
            # Poetry를 통해 Python 스크립트 실행
            poetry_path = "C:\\Users\\jg\\.local\\bin\\poetry.exe"
            if os.name == "nt" and os.path.exists(poetry_path):
                result = run_command(
                    [poetry_path, "run", "python", temp_script],
                    cwd=str(backend_dir),
                    capture_output=True
                )
            else:
                result = run_command(
                    ["python", temp_script],
                    cwd=str(backend_dir),
                    capture_output=True
                )
            
            if result.returncode == 0:
                print(result.stdout)
                print(f"\n{GREEN}✓ Celery 워커 상태 확인 완료{RESET}")
                return True
            else:
                print(f"\n{RED}✗ Celery 워커 상태 확인 실패{RESET}")
                if result.stderr:
                    print(f"{YELLOW}오류: {result.stderr[:500]}{RESET}")
                if result.stdout:
                    print(result.stdout)
                return False
        finally:
            # 임시 파일 삭제
            try:
                os.unlink(temp_script)
            except:
                pass
                
    except Exception as e:
        print(f"\n{RED}✗ Celery 워커 상태 확인 중 오류 발생: {e}{RESET}")
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
    
    # 현재 상태 확인
    pm2_services = check_pm2_services()
    is_waiting_restart = any(s["status"] == "waiting restart" for s in pm2_services)
    has_invalid_pid = any(s.get("pid", 0) == 0 for s in pm2_services)
    is_running = any(s["status"] == "online" for s in pm2_services)
    
    # 문제가 있는 프로세스가 있으면 삭제
    if is_waiting_restart or has_invalid_pid:
        print(f"{YELLOW}⚠ 문제가 있는 PM2 워커 감지. 삭제 후 재시작합니다...{RESET}")
        run_command(["pm2", "delete", "celery-worker"], capture_output=True)
        time.sleep(1)
    elif is_running:
        # 정상 실행 중이면 재시작만 시도
        restart_result = run_command(["pm2", "restart", "celery-worker", "--update-env"])
        if restart_result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 재시작 완료{RESET}")
        else:
            print(f"{YELLOW}⚠ 재시작 실패. 삭제 후 재시작합니다...{RESET}")
            run_command(["pm2", "delete", "celery-worker"], capture_output=True)
            time.sleep(1)
            config_file = PROJECT_ROOT / "ecosystem.config.js"
            result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
            else:
                print(f"{RED}✗ PM2 워커 시작 실패{RESET}")
                if result.stderr:
                    print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
        print(f"\n{GREEN}모든 서비스가 시작되었습니다!{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
        return
    
    # 새로 시작
    config_file = PROJECT_ROOT / "ecosystem.config.js"
    result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
    else:
        # 이미 존재한다고 나오면 삭제 후 재시작
        if result.stderr and ("already exists" in result.stderr.lower() or "already been started" in result.stderr.lower()):
            print(f"{YELLOW}⚠ 이미 존재하는 프로세스 감지. 삭제 후 재시작합니다...{RESET}")
            run_command(["pm2", "delete", "celery-worker"], capture_output=True)
            time.sleep(1)
            result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
            else:
                print(f"{RED}✗ PM2 워커 시작 실패{RESET}")
                if result.stderr:
                    print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
        else:
            print(f"{RED}✗ PM2 워커 시작 실패{RESET}")
            if result.stderr:
                print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
    
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
    is_waiting_restart = any(s["status"] == "waiting restart" for s in pm2_services)
    
    if is_waiting_restart:
        status_text = f"{YELLOW}재시작 대기 중 (문제 발생 가능){RESET}"
    elif is_running:
        status_text = f"{GREEN}실행 중{RESET}"
    else:
        status_text = f"{RED}중지됨{RESET}"
    
    print(f"현재 상태: {status_text}\n")
    
    print("1. PM2 워커 시작")
    print("2. PM2 워커 중지")
    print("3. PM2 워커 재시작")
    print("4. PM2 워커 삭제 후 재시작 (문제 해결용)")
    print("5. PM2 워커 로그 확인 (최근 50줄)")
    print("6. PM2 워커 로그 확인 (실시간)")
    print("7. Celery 워커 상태 확인 (작업 처리 여부)")
    print("0. 뒤로")
    
    choice = input(f"\n{BOLD}선택: {RESET}")
    
    if choice == "1":
        print(f"\n{BLUE}PM2 워커 시작 중...{RESET}")
        # 문제가 있는 프로세스가 있으면 먼저 삭제
        if is_waiting_restart or (pm2_services and any(s.get("pid", 0) == 0 for s in pm2_services)):
            print(f"{YELLOW}⚠ 문제가 있는 프로세스 감지. 삭제 후 재시작합니다...{RESET}")
            run_command(["pm2", "delete", "celery-worker"], capture_output=True)
            time.sleep(1)
        elif is_running:
            # 정상 실행 중이면 재시작만 시도
            print(f"{BLUE}이미 실행 중입니다. 재시작합니다...{RESET}")
            result = run_command(["pm2", "restart", "celery-worker", "--update-env"])
            if result.returncode == 0:
                print(f"{GREEN}✓ PM2 워커 재시작 완료{RESET}")
            else:
                print(f"{RED}✗ PM2 워커 재시작 실패. 삭제 후 재시작을 시도합니다...{RESET}")
                run_command(["pm2", "delete", "celery-worker"], capture_output=True)
                time.sleep(1)
                config_file = PROJECT_ROOT / "ecosystem.config.js"
                result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
                if result.returncode == 0:
                    print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
                else:
                    print(f"{RED}✗ PM2 워커 시작 실패{RESET}")
                    if result.stderr:
                        print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
            input("\n계속하려면 Enter를 누르세요...")
            return
        
        # 새로 시작
        config_file = PROJECT_ROOT / "ecosystem.config.js"
        result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
        if result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
        else:
            # 이미 존재한다고 나오면 삭제 후 재시작
            if "already exists" in result.stderr.lower() or "already been started" in result.stderr.lower():
                print(f"{YELLOW}⚠ 이미 존재하는 프로세스 감지. 삭제 후 재시작합니다...{RESET}")
                run_command(["pm2", "delete", "celery-worker"], capture_output=True)
                time.sleep(1)
                result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
                if result.returncode == 0:
                    print(f"{GREEN}✓ PM2 워커 시작 완료{RESET}")
                else:
                    print(f"{RED}✗ PM2 워커 시작 실패{RESET}")
                    if result.stderr:
                        print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
            else:
                print(f"{RED}✗ PM2 워커 시작 실패{RESET}")
                if result.stderr:
                    print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "2":
        print(f"\n{BLUE}PM2 워커 중지 중...{RESET}")
        result = run_command(["pm2", "stop", "celery-worker"])
        if result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 중지 완료{RESET}")
        else:
            print(f"{YELLOW}⚠ PM2 워커 중지 실패 (이미 중지되었을 수 있음){RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "3":
        print(f"\n{BLUE}PM2 워커 재시작 중...{RESET}")
        # "waiting restart" 상태면 삭제 후 재시작
        if is_waiting_restart:
            print(f"{YELLOW}⚠ 재시작 대기 상태 감지. 삭제 후 재시작합니다...{RESET}")
            run_command(["pm2", "delete", "celery-worker"])
            time.sleep(1)
            config_file = PROJECT_ROOT / "ecosystem.config.js"
            result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
        else:
            result = run_command(["pm2", "restart", "celery-worker", "--update-env"])
        
        if result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 재시작 완료{RESET}")
        else:
            print(f"{RED}✗ PM2 워커 재시작 실패{RESET}")
            if result.stderr:
                print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "4":
        print(f"\n{BLUE}PM2 워커 삭제 후 재시작 중...{RESET}")
        # 먼저 삭제
        run_command(["pm2", "delete", "celery-worker"])
        time.sleep(1)
        # 다시 시작
        config_file = PROJECT_ROOT / "ecosystem.config.js"
        result = run_command(["pm2", "start", str(config_file)], cwd=str(PROJECT_ROOT))
        if result.returncode == 0:
            print(f"{GREEN}✓ PM2 워커 삭제 후 재시작 완료{RESET}")
        else:
            print(f"{RED}✗ PM2 워커 재시작 실패{RESET}")
            if result.stderr:
                print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "5":
        print(f"\n{YELLOW}최근 로그를 불러오는 중...{RESET}\n")
        try:
            result = run_command(["pm2", "logs", "celery-worker", "--lines", "50", "--nostream"])
            if result.returncode == 0:
                print(result.stdout)
            else:
                print(f"{RED}✗ 로그를 불러올 수 없습니다.{RESET}")
                if result.stderr:
                    print(f"{YELLOW}오류: {result.stderr[:300]}{RESET}")
        except Exception as e:
            print(f"{RED}✗ 로그 확인 중 오류 발생: {e}{RESET}")
        input("\n계속하려면 Enter를 누르세요...")
    elif choice == "6":
        print(f"\n{YELLOW}실시간 로그를 보는 중... (Ctrl+C로 종료){RESET}\n")
        try:
            # 실시간 로그는 subprocess.run을 사용하되 Windows에서 cmd /c를 통해 실행
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "pm2", "logs", "celery-worker"])
            else:
                subprocess.run(["pm2", "logs", "celery-worker"])
        except KeyboardInterrupt:
            print(f"\n\n{YELLOW}로그 확인을 종료합니다...{RESET}")
            time.sleep(1)
        except Exception as e:
            print(f"\n{RED}✗ 로그 확인 중 오류 발생: {e}{RESET}")
            input("\n계속하려면 Enter를 누르세요...")
    elif choice == "7":
        check_celery_worker_status()
        input("\n계속하려면 Enter를 누르세요...")


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
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "docker", "compose", "logs", "-f"], cwd=str(PROJECT_ROOT))
            else:
                subprocess.run(["docker", "compose", "logs", "-f"], cwd=str(PROJECT_ROOT))
        elif choice == "3":
            print(f"\n{YELLOW}최근 로그를 불러오는 중...{RESET}\n")
            result = run_command(["pm2", "logs", "--lines", "50", "--nostream"])
            input("\n계속하려면 Enter를 누르세요...")
        elif choice == "4":
            print(f"\n{YELLOW}실시간 로그를 보는 중... (Ctrl+C로 종료){RESET}\n")
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "pm2", "logs"])
            else:
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
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "docker", "compose", "logs", "-f", "backend"], cwd=str(PROJECT_ROOT))
            else:
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
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "docker", "compose", "logs", "-f", "frontend"], cwd=str(PROJECT_ROOT))
            else:
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
    
    # VBS 파일을 직접 실행 (더 안정적)
    vbs_script = PROJECT_ROOT / "start_lmstudio.vbs"
    if vbs_script.exists():
        # Windows에서 VBS 파일은 cscript 또는 wscript로 실행
        if os.name == "nt":
            cmd = ["cscript", "//nologo", str(vbs_script)]
        else:
            print(f"{RED}✗ VBS 파일은 Windows에서만 실행 가능합니다.{RESET}")
            input("\n계속하려면 Enter를 누르세요...")
            return
        
        result = run_command(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True
        )
        
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
            if result.stderr:
                print(f"{YELLOW}오류: {result.stderr[:200]}{RESET}")
            print(f"\n{YELLOW}수동으로 LM Studio를 시작하세요:{RESET}")
            print(f"  1. Windows 시작 메뉴에서 'LM Studio' 검색")
            print(f"  2. LM Studio 애플리케이션 실행")
            print(f"  3. 모델 로드 (예: gpt-oss-20b)")
            print(f"  4. Local Server 시작 (포트 1234)")
            print(f"\n{CYAN}또는 start_lmstudio.vbs 파일을 편집하여 올바른 경로를 추가하세요.{RESET}")
    else:
        # VBS 파일이 없으면 .bat 파일 시도
        start_script = PROJECT_ROOT / "start_lmstudio.bat"
        if start_script.exists():
            if os.name == "nt":
                cmd = ["cmd", "/c", str(start_script)]
            else:
                cmd = [str(start_script)]
            
            result = run_command(
                cmd,
                cwd=str(PROJECT_ROOT),
                capture_output=True
            )
            
            if result.returncode == 0:
                print(f"\n{YELLOW}⚠ LM Studio 시작 후 다음을 확인하세요:{RESET}")
                print(f"  1. 모델을 로드하세요 (예: gpt-oss-20b)")
                print(f"  2. Local Server를 시작하세요 (포트 1234)")
                print(f"\n{BLUE}10초 후 상태를 확인합니다...{RESET}")
                time.sleep(10)
                
                if check_lmstudio():
                    print(f"{GREEN}✓ LM Studio가 정상적으로 실행 중입니다!{RESET}")
                else:
                    print(f"{YELLOW}⚠ LM Studio API가 아직 응답하지 않습니다.{RESET}")
                    print(f"  모델을 로드하고 Local Server를 시작했는지 확인하세요.")
            else:
                print(f"\n{RED}✗ LM Studio 실행 파일을 찾을 수 없습니다.{RESET}")
                if result.stderr:
                    print(f"{YELLOW}오류: {result.stderr[:200]}{RESET}")
        else:
            print(f"{RED}✗ start_lmstudio.vbs 또는 start_lmstudio.bat 파일을 찾을 수 없습니다.{RESET}")
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
            elif status == "waiting restart":
                status_icon = f"{YELLOW}◐ 재시작 대기 중{RESET} {RED}⚠ 문제 발생 가능{RESET}"
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

