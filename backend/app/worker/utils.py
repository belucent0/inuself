"""워커 유틸리티 함수들."""
import sys


def safe_print(*args, **kwargs):
    """Windows cp949 인코딩 문제를 피하기 위한 안전한 print 함수."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        # Unicode 문자를 ASCII로 대체
        safe_args = []
        for arg in args:
            if isinstance(arg, str):
                safe_arg = (
                    arg.replace("ℹ", "[INFO]")
                    .replace("✗", "[ERROR]")
                    .replace("✓", "[OK]")
                    .replace("⚠", "[WARN]")
                    .replace("→", "->")
                )
                safe_args.append(safe_arg)
            else:
                safe_args.append(arg)
        print(*safe_args, **kwargs)




