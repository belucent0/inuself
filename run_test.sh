#!/bin/bash
export PYTHONIOENCODING=utf-8
export PYTHONLEGACYWINDOWSSTDIO=utf-8
source rocm_env/Scripts/activate
python src/asr/test_asr.py large-v3 media/wav/audio_for_whisper_tariff.wav
# python src/diarization/test_asr_with_diarization.py large-v3-turbo media/wav/audio_for_whisper_tariff.wav

# python src/diarization/test_asr_with_diarization.py large-v3 media/wav/sample.wav
# python src/diarization/test_asr_with_diarization.py large-v3 media/wav/audio_for_whisper_tariff.wav
# python src/diarization/test_asr_with_diarization.py large-v3 media/wav/xz_library_56m.wav

deactivate

