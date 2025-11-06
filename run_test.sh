#!/bin/bash
source rocm_env/Scripts/activate
python src/diarization/test_pyannote.py
deactivate

