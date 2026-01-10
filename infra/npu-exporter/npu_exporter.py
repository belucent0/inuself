#!/usr/bin/env python3
"""
AMD NPU Prometheus Exporter
xrt-smi 명령을 사용하여 NPU 메트릭을 수집하고 Prometheus 형식으로 내보냅니다.
"""

import subprocess
import re
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import time
import threading

XRT_SMI_PATH = os.environ.get("XRT_SMI_PATH", r"C:\Windows\System32\AMD\xrt-smi.exe")
EXPORTER_PORT = int(os.environ.get("NPU_EXPORTER_PORT", "9183"))
SCRAPE_INTERVAL = int(os.environ.get("NPU_SCRAPE_INTERVAL", "5"))

# 캐시된 메트릭
cached_metrics: str = ""
metrics_lock = threading.Lock()


def parse_aie_partitions_text() -> list[dict]:
    """aie-partitions 텍스트 출력을 파싱합니다."""
    try:
        cmd = [XRT_SMI_PATH, "examine", "--report", "aie-partitions", "--batch"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return []

        contexts = []
        lines = result.stdout.split('\n')

        # 테이블 데이터 파싱
        in_table = False
        for line in lines:
            line = line.strip()
            if line.startswith('|PID'):
                in_table = True
                continue
            if line.startswith('|---'):
                continue
            if in_table and line.startswith('|'):
                parts = [p.strip() for p in line.split('|')[1:-1]]
                if len(parts) >= 13:
                    ctx = {
                        "pid": parts[0],
                        "ctx_id": parts[1],
                        "status": parts[2],
                        "instr_bo": parts[3],
                        "submissions": parts[4],
                        "completions": parts[5],
                        "migrations": parts[6],
                        "errors": parts[7],
                        "priority": parts[8],
                        "gops": parts[9],
                        "egops": parts[10],
                        "fps": parts[11],
                        "latency": parts[12]
                    }
                    contexts.append(ctx)

        return contexts
    except Exception as e:
        print(f"Error parsing aie-partitions: {e}")
        return []


def parse_platform_text() -> dict:
    """platform 텍스트 출력을 파싱합니다."""
    try:
        cmd = [XRT_SMI_PATH, "examine", "--report", "platform", "--batch"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return {}

        platform_info = {}
        lines = result.stdout.split('\n')

        for line in lines:
            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip().lower().replace(' ', '_')
                value = value.strip()
                platform_info[key] = value

        return platform_info
    except Exception as e:
        print(f"Error parsing platform: {e}")
        return {}


def parse_system_info_text() -> dict:
    """기본 시스템 정보를 파싱합니다."""
    try:
        cmd = [XRT_SMI_PATH, "examine", "--batch"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return {}

        info: dict = {
            "npu_present": 0,
            "npu_name": "",
            "driver_version": "",
            "firmware_version": ""
        }
        lines = result.stdout.split('\n')

        for line in lines:
            if 'NPU Driver Version' in line:
                info["driver_version"] = line.split(':')[1].strip()
            elif 'NPU Firmware Version' in line:
                info["firmware_version"] = line.split(':')[1].strip()
            elif 'NPU' in line and '|' in line:
                # Device table row
                parts = [p.strip() for p in line.split('|')]
                for part in parts:
                    if 'NPU' in part:
                        info["npu_name"] = part
                        info["npu_present"] = 1
                        break

        return info
    except Exception as e:
        print(f"Error parsing system info: {e}")
        return {}


def collect_metrics() -> str:
    """모든 NPU 메트릭을 수집하고 Prometheus 형식으로 반환합니다."""
    lines = []

    # 시스템 정보
    system_info = parse_system_info_text()

    lines.append("# HELP npu_present NPU device present (1=yes, 0=no)")
    lines.append("# TYPE npu_present gauge")
    lines.append(f'npu_present{{name="{system_info.get("npu_name", "unknown")}"}} {system_info.get("npu_present", 0)}')

    lines.append("# HELP npu_info NPU information")
    lines.append("# TYPE npu_info gauge")
    lines.append(f'npu_info{{name="{system_info.get("npu_name", "")}",driver_version="{system_info.get("driver_version", "")}",firmware_version="{system_info.get("firmware_version", "")}"}} 1')

    # 플랫폼 정보
    platform_info = parse_platform_text()

    power_mode_map = {"default": 0, "powersaver": 1, "balanced": 2, "performance": 3, "turbo": 4}
    power_mode = platform_info.get("power_mode", "").lower()
    power_mode_value = power_mode_map.get(power_mode, -1)

    lines.append("# HELP npu_power_mode NPU power mode (0=default, 1=powersaver, 2=balanced, 3=performance, 4=turbo)")
    lines.append("# TYPE npu_power_mode gauge")
    lines.append(f'npu_power_mode{{mode="{power_mode}"}} {power_mode_value}')

    # Total columns
    total_columns = platform_info.get("total_columns", "0")
    try:
        total_columns_int = int(total_columns)
    except ValueError:
        total_columns_int = 0

    lines.append("# HELP npu_total_columns Total NPU columns")
    lines.append("# TYPE npu_total_columns gauge")
    lines.append(f"npu_total_columns {total_columns_int}")

    # AIE 파티션/컨텍스트 정보
    contexts = parse_aie_partitions_text()

    lines.append("# HELP npu_context_status NPU context status (1=Active, 0=Idle)")
    lines.append("# TYPE npu_context_status gauge")

    lines.append("# HELP npu_context_submissions NPU context submissions count")
    lines.append("# TYPE npu_context_submissions counter")

    lines.append("# HELP npu_context_completions NPU context completions count")
    lines.append("# TYPE npu_context_completions counter")

    lines.append("# HELP npu_context_errors NPU context errors count")
    lines.append("# TYPE npu_context_errors counter")

    lines.append("# HELP npu_context_gops NPU context GOPS (Giga Operations Per Second)")
    lines.append("# TYPE npu_context_gops gauge")

    lines.append("# HELP npu_active_contexts Number of active NPU contexts")
    lines.append("# TYPE npu_active_contexts gauge")

    active_count = 0
    total_submissions = 0
    total_completions = 0
    total_errors = 0
    total_gops = 0.0

    for ctx in contexts:
        pid = ctx.get("pid", "0")
        ctx_id = ctx.get("ctx_id", "0")
        status = ctx.get("status", "Unknown")
        priority = ctx.get("priority", "Normal")

        status_value = 1 if status.lower() == "active" else 0
        if status_value == 1:
            active_count += 1

        lines.append(f'npu_context_status{{pid="{pid}",ctx_id="{ctx_id}",priority="{priority}"}} {status_value}')

        # Submissions
        try:
            submissions = int(ctx.get("submissions", "0"))
            total_submissions += submissions
            lines.append(f'npu_context_submissions{{pid="{pid}",ctx_id="{ctx_id}"}} {submissions}')
        except ValueError:
            pass

        # Completions
        try:
            completions = int(ctx.get("completions", "0"))
            total_completions += completions
            lines.append(f'npu_context_completions{{pid="{pid}",ctx_id="{ctx_id}"}} {completions}')
        except ValueError:
            pass

        # Errors
        try:
            errors = int(ctx.get("errors", "0"))
            total_errors += errors
            lines.append(f'npu_context_errors{{pid="{pid}",ctx_id="{ctx_id}"}} {errors}')
        except ValueError:
            pass

        # GOPS
        gops = ctx.get("gops", "N/A")
        if gops != "N/A":
            try:
                gops_value = float(gops)
                total_gops += gops_value
                lines.append(f'npu_context_gops{{pid="{pid}",ctx_id="{ctx_id}"}} {gops_value}')
            except ValueError:
                pass

    lines.append(f"npu_active_contexts {active_count}")

    # 총계 메트릭
    lines.append("# HELP npu_total_submissions Total NPU submissions")
    lines.append("# TYPE npu_total_submissions counter")
    lines.append(f"npu_total_submissions {total_submissions}")

    lines.append("# HELP npu_total_completions Total NPU completions")
    lines.append("# TYPE npu_total_completions counter")
    lines.append(f"npu_total_completions {total_completions}")

    lines.append("# HELP npu_total_errors Total NPU errors")
    lines.append("# TYPE npu_total_errors counter")
    lines.append(f"npu_total_errors {total_errors}")

    lines.append("# HELP npu_total_gops Total NPU GOPS")
    lines.append("# TYPE npu_total_gops gauge")
    lines.append(f"npu_total_gops {total_gops}")

    lines.append("# HELP npu_context_count Total number of NPU contexts")
    lines.append("# TYPE npu_context_count gauge")
    lines.append(f"npu_context_count {len(contexts)}")

    return "\n".join(lines) + "\n"


def update_metrics_cache():
    """백그라운드에서 메트릭 캐시를 주기적으로 업데이트합니다."""
    global cached_metrics
    while True:
        try:
            new_metrics = collect_metrics()
            with metrics_lock:
                cached_metrics = new_metrics
        except Exception as e:
            print(f"Error collecting metrics: {e}")
        time.sleep(SCRAPE_INTERVAL)


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            with metrics_lock:
                response = cached_metrics if cached_metrics else collect_metrics()

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(response.encode("utf-8"))
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # 로그 출력 억제 (필요시 활성화)
        pass


def main():
    print(f"Starting AMD NPU Prometheus Exporter on port {EXPORTER_PORT}")
    print(f"xrt-smi path: {XRT_SMI_PATH}")
    print(f"Scrape interval: {SCRAPE_INTERVAL}s")

    # 초기 메트릭 수집
    global cached_metrics
    try:
        cached_metrics = collect_metrics()
        print("Initial metrics collected successfully")
    except Exception as e:
        print(f"Warning: Initial metrics collection failed: {e}")

    # 백그라운드 스레드에서 메트릭 업데이트
    update_thread = threading.Thread(target=update_metrics_cache, daemon=True)
    update_thread.start()

    # HTTP 서버 시작
    server = HTTPServer(("0.0.0.0", EXPORTER_PORT), MetricsHandler)
    print(f"NPU Exporter listening on http://0.0.0.0:{EXPORTER_PORT}/metrics")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
