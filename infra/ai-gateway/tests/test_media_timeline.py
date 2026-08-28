import unittest
import wave
from array import array
from io import BytesIO

from routes.media import _correct_vllm_chunk_timestamps


def _wav_with_quiet_splits() -> bytes:
    sample_rate = 16000
    samples = array("h", [1000]) * (61 * sample_rate)
    for second in (29.2, 58.7):
        start = int(second * sample_rate)
        samples[start : start + sample_rate // 10] = array(
            "h", [0]
        ) * (sample_rate // 10)

    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


class VllmTimelineTest(unittest.TestCase):
    def test_corrects_nominal_chunk_offsets(self):
        segments = [
            {"seek": 0, "start": 28, "end": 30},
            {"seek": 30, "start": 30, "end": 60},
            {"seek": 60, "start": 60, "end": 61},
        ]

        audio = _wav_with_quiet_splits()
        corrected = _correct_vllm_chunk_timestamps(segments, audio)

        self.assertAlmostEqual(corrected[0]["end"], 29.2)
        self.assertAlmostEqual(corrected[1]["start"], 29.2)
        self.assertAlmostEqual(corrected[1]["end"], 58.7)
        self.assertAlmostEqual(corrected[2]["start"], 58.7)

        already_correct = [{"seek": 29.2, "start": 29.2, "end": 30}]
        self.assertEqual(
            _correct_vllm_chunk_timestamps(already_correct, audio), already_correct
        )


if __name__ == "__main__":
    unittest.main()
