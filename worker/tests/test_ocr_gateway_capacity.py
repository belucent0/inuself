import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from worker.pipelines.ocr import vision


def gateway(monkeypatch, responses):
    real_client = httpx.Client
    requests, logs = [], []

    def handler(request):
        requests.append(request)
        return responses[len(requests) - 1]

    monkeypatch.setattr(vision.httpx, "Client", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(vision, "logger", SimpleNamespace(**{name: logs.append for name in ("info", "debug", "warning", "error")}))
    return requests, logs


def response(status=200, error_type=None):
    if status == 200:
        return httpx.Response(200, json={"choices": [{"message": {"content": "<p>first page</p>"}}]})
    return httpx.Response(status, json={"error": {"type": error_type, "message": "SECRET gpu memory not found"}})


@pytest.mark.parametrize("error_type", ["overloaded", "unavailable"])
def test_second_page_shared_capacity_error_retries_whole_job_instead_of_partial_success(monkeypatch, error_type):
    requests, logs = gateway(monkeypatch, [response(), response(503, error_type)])
    images = [Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4))]
    with pytest.raises(vision.OCRGatewayRetryableError, match=f"^OCR gateway {error_type}$") as error:
        vision.OcrVisionProcessor().process_images(images, ocr_mode="portray")
    assert len(requests) == 2
    assert "SECRET" not in "\n".join(logs)
    assert not any(word in str(error.value).lower() for word in ("gpu", "vram", "memory", "not found", "allocation failed"))
    for image in images:
        with pytest.raises(ValueError):
            image.getpixel((0, 0))
    assert json.loads(requests[0].content)["extra_body"]["task_type"] == "ocr"


@pytest.mark.parametrize("error_type", ["overloaded", "unavailable"])
def test_table_detection_capacity_error_is_not_converted_to_no_table(monkeypatch, error_type):
    requests, logs = gateway(monkeypatch, [response(503, error_type)])
    image = Image.new("RGB", (4, 4))
    with pytest.raises(vision.OCRGatewayRetryableError):
        vision.OcrVisionProcessor().process_images([image], ocr_mode="document")
    assert len(requests) == 1
    assert not any("assuming no table" in log for log in logs)
    assert "SECRET" not in "\n".join(logs)


def test_genuine_second_page_ocr_failure_keeps_existing_partial_success(monkeypatch):
    requests, logs = gateway(monkeypatch, [response(), response(400, "invalid_request")])
    images = [Image.new("RGB", (4, 4)), Image.new("RGB", (4, 4))]
    result = vision.OcrVisionProcessor().process_images(images, ocr_mode="portray")
    assert len(requests) == 2
    assert result["page_count"] == 2 and result["page_texts"] == ["<p>first page</p>", ""]
    assert result["ocr_metadata"]["pages"][0]["status"] == "success"
    assert result["ocr_metadata"]["pages"][1]["status"] == "failed"
    assert result["ocr_metadata"]["pages"][1]["error"] == "OCR HTTP error: 400"
    assert "first page" in result["ocr_text"]
    assert "SECRET" not in json.dumps(result) + "\n".join(logs)


def test_genuine_table_detection_error_keeps_existing_no_table_fallback(monkeypatch):
    requests, logs = gateway(monkeypatch, [response(400, "invalid_request")])
    assert vision.OcrVisionProcessor()._detect_table("jpeg") is False
    assert len(requests) == 1
    assert any("assuming no table" in log for log in logs)
    assert "SECRET" not in "\n".join(logs)


@pytest.mark.parametrize("result", [
    response(400, "invalid_request"), response(503, "unknown"),
    httpx.Response(503, content=b"SECRET raw upstream response"),
    httpx.Response(200, json={"SECRET": "private response"}),
])
def test_non_capacity_gateway_errors_have_static_diagnostics_and_no_raw_body(monkeypatch, result):
    requests, logs = gateway(monkeypatch, [result])
    with pytest.raises(RuntimeError) as error:
        vision._call_ocr_via_ai_gateway("jpeg", "read")
    assert not isinstance(error.value, vision.OCRGatewayRetryableError)
    assert len(requests) == 1
    assert "SECRET" not in str(error.value) + "\n".join(logs)


@pytest.mark.parametrize("error_type", ["overloaded", "unavailable"])
def test_retryable_error_reaches_existing_celery_retry_policy(monkeypatch, error_type):
    from contextlib import contextmanager
    from celery.exceptions import Retry
    from worker.tasks import ocr_task
    from worker.processors import ocr_processor

    @contextmanager
    def trace(*_args, **_kwargs):
        yield SimpleNamespace(set_attribute=lambda *_: None)

    def process(**_kwargs):
        raise vision.OCRGatewayRetryableError(f"OCR gateway {error_type}")

    def retry(*_args, **_kwargs):
        raise Retry("retry requested")

    task = ocr_task.process_ocr_task
    monkeypatch.setattr(ocr_task, "trace_celery_task", trace)
    monkeypatch.setattr(ocr_processor, "process_ocr_job", process)
    monkeypatch.setattr(task, "retry", retry)
    task.push_request(retries=0, id="test-ocr")
    try:
        assert task.max_retries == 3
        with pytest.raises(Retry):
            task.run(file_id=1, image_s3_keys=["page.jpg"])
    finally:
        task.pop_request()


def test_genuine_first_page_resource_error_keeps_existing_permanent_failure_policy(monkeypatch):
    from contextlib import contextmanager
    from worker.tasks import ocr_task
    from worker.processors import ocr_processor
    from worker.utils import result_publisher

    @contextmanager
    def trace(*_args, **_kwargs):
        yield SimpleNamespace(set_attribute=lambda *_: None)

    def image_error(*_args, **_kwargs):
        raise RuntimeError("GPU memory allocation failed")

    def process(**_kwargs):
        vision.OcrVisionProcessor().process_images([Image.new("RGB", (4, 4))], ocr_mode="portray")

    def retry(*_args, **_kwargs):
        pytest.fail("permanent resource errors must not be retried")

    failures = []
    task = ocr_task.process_ocr_task
    monkeypatch.setattr(ocr_task, "trace_celery_task", trace)
    monkeypatch.setattr(vision.OcrVisionProcessor, "process_image", image_error)
    monkeypatch.setattr(ocr_processor, "process_ocr_job", process)
    monkeypatch.setattr(result_publisher, "publish_ocr_failed", lambda file_id, **kwargs: failures.append((file_id, kwargs)))
    monkeypatch.setattr(task, "retry", retry)
    task.push_request(retries=0, id="test-ocr")
    try:
        result = task.run(file_id=1, image_s3_keys=["page.jpg"])
    finally:
        task.pop_request()
    assert result["status"] == "failed"
    assert result["error"] == "OCR failed for first page: GPU memory allocation failed"
    assert failures == [(1, {"error": result["error"]})]
