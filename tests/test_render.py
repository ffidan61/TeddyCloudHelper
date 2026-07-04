"""Renderer + template contents for all mode combinations."""

from teddycloudhelper import render

DIRECT = {
    "deployment_mode": "direct",
    "webui_port_mode": "separate",
    "webui_hostname": "tc.example.com",
    "webui_port": 8443,
    "http_port": 80,
    "webui_client_cert_auth": False,
}
NGINX_SEPARATE = DIRECT | {"deployment_mode": "nginx"}
NGINX_SHARED = NGINX_SEPARATE | {"webui_port_mode": "shared"}


# --- render_to_file mechanics -------------------------------------------------


def test_render_to_file_writes_and_creates_parents(tmp_path):
    dest = tmp_path / "sub" / "docker-compose.yml"
    path = render.render_to_file("docker-compose.yml.j2", dest, DIRECT)
    assert path == dest
    assert "teddycloud" in dest.read_text()


def test_render_to_file_backs_up_existing(tmp_path):
    dest = tmp_path / "docker-compose.yml"
    dest.write_text("old contents")

    render.render_to_file("docker-compose.yml.j2", dest, DIRECT)

    backups = list(tmp_path.glob("docker-compose.yml.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "old contents"


def test_render_to_file_no_backup_on_first_write(tmp_path):
    dest = tmp_path / "docker-compose.yml"
    render.render_to_file("docker-compose.yml.j2", dest, DIRECT)
    assert list(tmp_path.glob("*.bak")) == []


# --- docker-compose.yml.j2 ----------------------------------------------------


def test_compose_direct_publishes_teddycloud_ports():
    text = render.render_template("docker-compose.yml.j2", DIRECT)
    assert '"80:80"' in text
    assert '"443:443"' in text
    assert '"8443:8443"' in text
    assert "nginx" not in text


def test_compose_nginx_moves_ports_to_nginx():
    text = render.render_template("docker-compose.yml.j2", NGINX_SEPARATE)
    assert "nginx:stable-alpine" in text
    assert "./webui-pki:/etc/teddycloudhelper/webui-pki:ro" in text
    # teddycloud service publishes nothing; ports appear only under nginx
    teddycloud_part = text.split("nginx:")[0]
    assert "ports:" not in teddycloud_part


def test_compose_nginx_separate_publishes_webui_port():
    text = render.render_template("docker-compose.yml.j2", NGINX_SEPARATE | {"webui_port": 9443})
    assert '"9443:9443"' in text


def test_compose_nginx_shared_has_no_extra_port():
    text = render.render_template("docker-compose.yml.j2", NGINX_SHARED)
    assert "8443" not in text


# --- nginx.conf.j2 ------------------------------------------------------------


def test_nginx_shared_uses_sni_split():
    text = render.render_template("nginx.conf.j2", NGINX_SHARED)
    assert "ssl_preread on;" in text
    assert "map $ssl_preread_server_name" in text
    assert "tc.example.com 127.0.0.1:8444;" in text
    assert "default teddycloud:443;" in text
    assert "listen 127.0.0.1:8444 ssl;" in text


def test_nginx_separate_passes_443_through():
    text = render.render_template("nginx.conf.j2", NGINX_SEPARATE)
    assert "proxy_pass teddycloud:443;" in text
    assert "ssl_preread" not in text
    assert "listen 8443 ssl;" in text


def test_nginx_box_path_is_never_terminated():
    for context in (NGINX_SEPARATE, NGINX_SHARED):
        text = render.render_template("nginx.conf.j2", context)
        stream_block = text.split("http {")[0]
        assert "ssl_certificate" not in stream_block  # passthrough only


def test_nginx_allows_large_uploads_and_websockets():
    for context in (NGINX_SEPARATE, NGINX_SHARED):
        text = render.render_template("nginx.conf.j2", context)
        assert "client_max_body_size 0;" in text  # TAF uploads are huge
        assert "proxy_set_header Upgrade $http_upgrade;" in text


def test_nginx_client_cert_auth_off_by_default():
    text = render.render_template("nginx.conf.j2", NGINX_SEPARATE)
    assert "ssl_verify_client" not in text


def test_nginx_client_cert_auth_on():
    context = NGINX_SEPARATE | {"webui_client_cert_auth": True}
    text = render.render_template("nginx.conf.j2", context)
    assert "ssl_verify_client on;" in text
    assert "ssl_client_certificate /etc/teddycloudhelper/webui-pki/ca/ca.crt;" in text
    assert "ssl_crl" in text
