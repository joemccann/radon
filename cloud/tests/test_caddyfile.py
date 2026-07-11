"""Tests for caddy/Caddyfile configuration."""

import re


def read_caddyfile(caddy_dir):
    return (caddy_dir / "Caddyfile").read_text()


class TestCaddyfileExists:
    def test_file_exists_and_nonempty(self, caddy_dir):
        caddyfile = caddy_dir / "Caddyfile"
        assert caddyfile.exists(), "Caddyfile must exist"
        assert caddyfile.stat().st_size > 0, "Caddyfile must not be empty"


class TestSiteAddress:
    def test_uses_domain_env_var_with_default(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "{$DOMAIN:app.radon.run}" in content


class TestCompression:
    def test_has_encode_directive(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"^\s*encode\b", content, re.MULTILINE)


class TestSecurityHeaders:
    def test_x_content_type_options(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "X-Content-Type-Options" in content

    def test_x_frame_options(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "X-Frame-Options" in content

    def test_referrer_policy(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "Referrer-Policy" in content

    def test_removes_server_header(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert "-Server" in content


class TestLogging:
    def test_has_log_block(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"^\s*log\s*\{", content, re.MULTILINE)

    def test_json_format(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"^\s*format\s+json\b", content, re.MULTILINE)


class TestRouting:
    def test_websocket_route_to_8765(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"handle\s+/ws\*", content)
        assert "localhost:8765" in content

    def test_api_ib_route_to_8321(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"handle_path\s+/api/ib/\*", content)
        assert "localhost:8321" in content

    def test_default_route_to_3000(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert re.search(r"handle\s*\{", content, re.MULTILINE)
        assert "localhost:3000" in content

    def test_handle_path_used_for_api_ib(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        match = re.search(r"(handle_path|handle)\s+/api/ib/\*", content)
        assert match is not None, "/api/ib/* route must exist"
        assert match.group(1) == "handle_path", (
            "/api/ib/* must use handle_path (not handle) for prefix stripping"
        )

    def test_websocket_route_before_api_route(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        ws_pos = content.find("handle /ws")
        api_pos = content.find("handle_path /api/ib")
        assert ws_pos < api_pos, "WebSocket route must come before API route"


class TestTLS:
    def test_no_explicit_tls_config(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        assert not re.search(r"^\s*tls\b", content, re.MULTILINE), (
            "No explicit TLS config needed; Caddy auto-provisions certificates"
        )


class TestReverseProxyTargets:
    def test_all_reverse_proxy_targets_use_loopback(self, caddy_dir):
        content = read_caddyfile(caddy_dir)
        active_config = "\n".join(
            line for line in content.splitlines()
            if not line.lstrip().startswith("#")
        )
        targets = re.findall(r"reverse_proxy\s+(\S+)", active_config)
        assert len(targets) > 0, "Must have at least one reverse_proxy directive"
        for target in targets:
            assert target.startswith(("localhost:", "127.0.0.1:")), (
                f"reverse_proxy target '{target}' must use loopback, not a public bind"
            )
