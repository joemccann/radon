"""The client export stream never reaches journald (2026-11-14).

`podman run ... tar -cf -` with the host's default journald log driver
copied the whole export tar into the journal as container log lines:
`radon-model-client-export` wrote ~79 MB of `[blob data]` records in 8h,
~92% of all journal bytes, so journald sat at its 1G SystemMaxUse cap, kept
only hours of history, and the disk-cleanup headroom reclaim found nothing
but that journal to vacuum. stdout still reaches the caller with
`--log-driver=none` (verified on podman 4.9.3 on radon-app).
"""
import io
import sys

from test_model_clients_install import module


def _recording_engine(tmp_path):
    record = tmp_path / "argv"
    engine = tmp_path / "engine"
    engine.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if sys.argv[1] != 'run': raise SystemExit(0)\n"
        f"open({str(record)!r}, 'w').write('\\0'.join(sys.argv[1:]))\n"
        "sys.stdout.buffer.write(b'payload')\n"
    )
    engine.chmod(0o755)
    return str(engine), record


def test_export_container_disables_its_log_driver(tmp_path):
    api = module()
    engine, record = _recording_engine(tmp_path)
    target = io.BytesIO()
    api.export_from_image(engine, "ghcr.io/joemccann/radon-model-gateway:" + "a" * 40, target)
    assert target.getvalue() == b"payload"
    argv = record.read_text().split("\0")
    image_at = argv.index("ghcr.io/joemccann/radon-model-gateway:" + "a" * 40)
    run_flags = argv[:image_at]
    assert "--log-driver=none" in run_flags
