"""The published security topology must follow the operator firewall sources."""
import importlib.util
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_renderer():
    spec = importlib.util.spec_from_file_location('network_topology', ROOT / 'tools/render_network_topology.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_published_topology_is_reproducible_and_current():
    renderer = load_renderer()
    actual = renderer.render(ROOT)
    assert actual == renderer.render(ROOT)
    assert actual == (ROOT / 'docs/radon-network-topology.svg').read_text()
    ET.fromstring(actual)
    assert not (ROOT / 'docs/radon-network-topology.png').exists()


def test_firewall_change_changes_the_rendered_public_boundary(tmp_path):
    renderer = load_renderer()
    target = tmp_path / 'cloud/hetzner/firewalls'
    target.mkdir(parents=True)
    for name in ('app', 'broker', 'demo'):
        shutil.copy(ROOT / f'cloud/hetzner/firewalls/fw-radon-{name}.json', target)
    before = renderer.render(tmp_path)
    path = target / 'fw-radon-demo.json'
    rules = json.loads(path.read_text())
    rules.append({'direction': 'in', 'protocol': 'tcp', 'port': '8080', 'source_ips': ['0.0.0.0/0', '::/0'], 'description': 'synthetic drift'})
    path.write_text(json.dumps(rules))
    app_path = target / 'fw-radon-app.json'
    app_rules = json.loads(app_path.read_text())
    app_rules.extend([
        {'direction': 'in', 'protocol': 'tcp', 'port': '22', 'source_ips': ['198.51.100.9/32']},
        {'direction': 'out', 'protocol': 'udp', 'port': '9999', 'source_ips': ['0.0.0.0/0', '::/0']},
    ])
    app_path.write_text(json.dumps(app_rules))
    after = renderer.render(tmp_path)
    assert before != after
    assert 'TCP 8080' not in before and 'TCP 8080' in after
    assert 'TCP 22 · 198.51.100.9/32' in after
    assert 'Public SSH: blocked' in before and 'Public SSH: blocked' not in after
    assert 'UDP 9999' not in after


def test_check_refuses_a_stale_artifact(tmp_path, capsys):
    renderer = load_renderer()
    path = tmp_path / 'network.svg'
    assert renderer.main(['--check', '--output', str(path)]) == 1
    assert renderer.main(['--output', str(path)]) == 0
    assert renderer.main(['--check', '--output', str(path)]) == 0
    path.write_text('<svg/>')
    assert renderer.main(['--check', '--output', str(path)]) == 1
    assert 'stale' in capsys.readouterr().err.lower()


def test_operator_docs_do_not_offer_blocked_public_app_ssh_fallbacks():
    readme = (ROOT / 'cloud/README.md').read_text()
    media = (ROOT / 'docs/cloud-services.md').read_text()
    recovery = (ROOT / 'docs/ib-gateway-recovery.md').read_text()
    assert 'Public 22/tcp is open' not in readme
    assert 'To bypass Tailscale and push over the Hetzner public IP' not in media
    assert 'ssh -J radon@5.78.148.38' not in recovery
    for path in ('README.md', 'docs/README.md', 'docs/spof-host-split.md'):
        assert 'radon-network-topology.svg' in (ROOT / path).read_text()
        assert 'radon-network-topology.png' not in (ROOT / path).read_text()
