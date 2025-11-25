"""워커 설정 및 초기화."""
import os
from pathlib import Path
from typing import Optional

# MIOpen 최적화 설정
MIOPEN_MODE = 8  # FAST mode - 최적 설정


def setup_rocm_environment() -> None:
    """ROCm 환경 설정 (MIOpen 최적화)."""
    if MIOPEN_MODE == 8:
        try:
            import rocm_sdk_devel
            import rocm_sdk_core
            
            devel_path = os.path.dirname(rocm_sdk_devel.__file__)
            core_path = os.path.dirname(rocm_sdk_core.__file__)
            site_lib_path = os.path.dirname(devel_path)
            
            possible_include_paths = [
                os.path.join(site_lib_path, '_rocm_sdk_devel', '_rocm_sdk_devel', 'include'),
                os.path.join(site_lib_path, '_rocm_sdk_core', 'include'),
                os.path.join(core_path, '..', '_rocm_sdk_core', 'include'),
                os.path.join(devel_path, 'include'),
                os.path.join(devel_path, 'rocm', 'include'),
                os.path.join(devel_path, '..', 'rocm_sdk_libraries', 'include'),
            ]
            include_paths = []
            for path in possible_include_paths:
                abs_path = os.path.abspath(path)
                if os.path.exists(abs_path):
                    include_paths.append(abs_path)
            
            if include_paths:
                include_path_str = ';'.join(include_paths)
                os.environ['HIP_INCLUDE_PATH'] = include_path_str
                os.environ['HIP_PLATFORM'] = 'amd'
                os.environ['ROCM_PATH'] = os.path.dirname(include_paths[0])
                os.environ['CPLUS_INCLUDE_PATH'] = include_path_str
                os.environ['C_INCLUDE_PATH'] = include_path_str
                hip_include_flags = ' '.join([f'-I{path}' for path in include_paths])
                os.environ['HIPCC_COMPILE_FLAGS_APPEND'] = hip_include_flags
            
            os.environ['MIOPEN_FIND_MODE'] = 'FAST'
            os.environ['MIOPEN_DISABLE_CACHE'] = '1'
            os.environ['MIOPEN_DEBUG_DISABLE_FIND_DB'] = '1'
            
            import torch
            torch.backends.cudnn.benchmark = True
        except ImportError:
            # ROCm SDK가 없으면 CPU 모드로 진행
            pass


def get_whispercpp_model_path(model_size: str, project_root: Optional[Path] = None) -> str:
    """whisper.cpp 모델 경로 찾기."""
    model_mapping = {
        "tiny": "ggml-tiny.bin",
        "base": "ggml-base.bin",
        "small": "ggml-small.bin",
        "medium": "ggml-medium.bin",
        "large": "ggml-large.bin",
        "large-v1": "ggml-large-v1.bin",
        "large-v2": "ggml-large-v2.bin",
        "large-v3": "ggml-large-v3.bin",
        "large-v3-turbo": "ggml-large-v3-turbo.bin",
        "turbo": "ggml-large-v3-turbo.bin",
    }
    
    model_filename = model_mapping.get(model_size)
    if not model_filename:
        raise ValueError(f"Unsupported model size: {model_size}")
    
    # 우선순위 1: 프로젝트 내 모델 (src/asr/models/)
    if project_root:
        asr_dir = project_root / "src" / "asr" / "models"
        project_model_path = asr_dir / model_filename
        if project_model_path.exists():
            return str(project_model_path)
    
    # 우선순위 2: C:/whisper-cpp/models/
    external_model_path = Path("C:/whisper-cpp/models") / model_filename
    if external_model_path.exists():
        return str(external_model_path)
    
    raise FileNotFoundError(f"Model file not found: {model_filename}")


def get_whisper_cli_path() -> str:
    """whisper-cli.exe 경로 찾기."""
    default_path = "C:/whisper-cpp/build/bin/Release/whisper-cli.exe"
    if os.path.exists(default_path):
        return default_path
    raise FileNotFoundError(f"whisper-cli.exe not found: {default_path}")

