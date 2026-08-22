from worker.utils import event_publisher


def test_file_progress_reaches_file_and_authenticated_global_channels(monkeypatch):
    published = []
    monkeypatch.setattr(
        event_publisher,
        "_publish",
        lambda channel, event: published.append((channel, event)),
    )

    event_publisher.publish_file_progress("file-1", "PROCESSING", "asr", 50, "half")

    assert [channel for channel, _ in published] == [
        "events:file_progress:file-1",
        "events:file_progress:global",
    ]
