"""Renderer + template contents for all mode combinations."""

from teddycloudhelper import render

DIRECT = {
    "deployment_mode": "direct",
    "teddycloud_image_tag": "latest",
    "webui_port_mode": "separate",
    "webui_hostname": "tc.example.com",
    "webui_port": 8443,
    "http_port": 80,
    "webui_client_cert_auth": False,
    "basic_auth_enabled": False,
    "ip_allowlist": [],
    "webui_tls_mode": "selfsigned",
    "letsencrypt_enabled": False,
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


def test_compose_image_tag():
    text = render.render_template("docker-compose.yml.j2", DIRECT)
    assert "image: ghcr.io/toniebox-reverse-engineering/teddycloud:latest" in text
    text = render.render_template(
        "docker-compose.yml.j2", DIRECT | {"teddycloud_image_tag": "develop"}
    )
    assert "image: ghcr.io/toniebox-reverse-engineering/teddycloud:develop" in text


def test_compose_mounts_all_upstream_data_dirs():
    # Container paths must match upstream's compose example: content and
    # library live under /teddycloud/data/, NOT /teddycloud/ — a wrong path
    # means TeddyCloud writes into the container layer and loses everything
    # on the next image update.
    text = render.render_template("docker-compose.yml.j2", DIRECT)
    for mount in (
        "- ./certs:/teddycloud/certs",
        "- ./config:/teddycloud/config",
        "- ./content:/teddycloud/data/content",
        "- ./library:/teddycloud/data/library",
        "- ./custom_img:/teddycloud/data/www/custom_img",
        "- ./firmware:/teddycloud/data/firmware",
        "- ./cache:/teddycloud/data/cache",
        "- ./plugins:/teddycloud/data/www/plugins",
    ):
        assert mount in text
    assert ":/teddycloud/content" not in text
    assert ":/teddycloud/library" not in text


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


def test_nginx_slow_upload_and_sse_friendly():
    # TeddyCloud needs minutes for big uploads (ESP32 dumps); the 60s
    # defaults produced 502/504. SSE needs unbuffered responses.
    for context in (NGINX_SEPARATE, NGINX_SHARED):
        text = render.render_template("nginx.conf.j2", context)
        assert "proxy_read_timeout 600s;" in text
        assert "proxy_send_timeout 600s;" in text
        assert "proxy_buffering off;" in text


def test_nginx_streams_request_bodies():
    # TeddyCloud drops body bytes that arrive in one burst with the
    # headers (verified 2026-07: uploads through a buffering proxy stall
    # ~120s and die with 502). The body MUST be streamed.
    for context in (NGINX_SEPARATE, NGINX_SHARED):
        text = render.render_template("nginx.conf.j2", context)
        assert "proxy_request_buffering off;" in text


def test_nginx_client_cert_auth_off_by_default():
    text = render.render_template("nginx.conf.j2", NGINX_SEPARATE)
    assert "ssl_verify_client" not in text


def test_nginx_client_cert_auth_on():
    context = NGINX_SEPARATE | {"webui_client_cert_auth": True}
    text = render.render_template("nginx.conf.j2", context)
    assert "ssl_verify_client on;" in text
    assert "ssl_client_certificate /etc/teddycloudhelper/webui-pki/ca/ca.crt;" in text
    assert "ssl_crl" in text


def test_nginx_security_off_by_default():
    text = render.render_template("nginx.conf.j2", NGINX_SEPARATE)
    assert "auth_basic_user_file" not in text
    assert "deny all;" not in text


def test_nginx_basic_auth_guards_webui():
    context = NGINX_SEPARATE | {"basic_auth_enabled": True}
    text = render.render_template("nginx.conf.j2", context)
    assert text.count('auth_basic "TeddyCloud WebUI";') == 1  # the TLS WebUI server
    assert text.count("auth_basic_user_file /etc/teddycloudhelper/security/htpasswd;") == 1


def test_nginx_allowlist_guards_webui():
    context = NGINX_SEPARATE | {"ip_allowlist": ["192.168.0.0/24", "10.0.0.5"]}
    text = render.render_template("nginx.conf.j2", context)
    assert text.count("allow 192.168.0.0/24;") == 1
    assert text.count("allow 10.0.0.5;") == 1
    assert text.count("deny all;") == 1
    # the box path (stream block) must never be restricted
    stream_block = text.split("http {")[0]
    assert "deny" not in stream_block


def test_nginx_acme_challenge_always_served():
    for context in (NGINX_SEPARATE, NGINX_SHARED):
        text = render.render_template("nginx.conf.j2", context)
        assert "location /.well-known/acme-challenge/" in text


def test_nginx_port80_never_reaches_teddycloud():
    # Unauthenticated internet noise on port 80 (crawlers, /.env scanners)
    # reaching TeddyCloud trips its security-mitigation lock, which also
    # cuts off the boxes (seen in prod 2026-07). Port 80 must only serve
    # ACME and redirect — even with all security features enabled, the
    # challenge stays reachable because the block carries no auth at all.
    context = NGINX_SEPARATE | {
        "basic_auth_enabled": True,
        "ip_allowlist": ["192.168.0.0/24"],
    }
    text = render.render_template("nginx.conf.j2", context)
    port80 = text.split("listen 80;", 1)[1].split("server {", 1)[0]
    assert "auth_basic " not in port80
    assert "deny" not in port80
    assert "location /.well-known/acme-challenge/" in port80
    assert "return 301 https://$host:8443$request_uri;" in port80
    # Only the box API may be proxied on port 80 — never the web GUI.
    assert port80.count("proxy_pass") == 1
    assert "location /v1/" in port80


def test_nginx_port80_serves_box_time_endpoint():
    # The box fetches /v1/time over plain HTTP before its first TLS
    # handshake (cert validation needs a correct clock) — a redirect
    # bricks the box boot sequence (seen in prod 2026-07).
    for context in (NGINX_SEPARATE, NGINX_SHARED):
        text = render.render_template("nginx.conf.j2", context)
        port80 = text.split("listen 80;", 1)[1].split("server {", 1)[0]
        assert "location /v1/" in port80
        assert "proxy_pass http://teddycloud:80;" in port80


def test_nginx_port80_redirect_targets_webui_port():
    text = render.render_template("nginx.conf.j2", NGINX_SHARED)
    assert "return 301 https://$host$request_uri;" in text  # shared: WebUI on 443
    text = render.render_template("nginx.conf.j2", NGINX_SEPARATE | {"webui_port": 9443})
    assert "return 301 https://$host:9443$request_uri;" in text


def test_nginx_letsencrypt_cert_paths():
    context = NGINX_SEPARATE | {"webui_tls_mode": "letsencrypt"}
    text = render.render_template("nginx.conf.j2", context)
    assert "ssl_certificate     /etc/letsencrypt/live/tc.example.com/fullchain.pem;" in text
    assert "ssl_certificate_key /etc/letsencrypt/live/tc.example.com/privkey.pem;" in text
    assert "webui-pki/server" not in text


def test_nginx_selfsigned_cert_paths_by_default():
    text = render.render_template("nginx.conf.j2", NGINX_SEPARATE)
    assert "ssl_certificate     /etc/teddycloudhelper/webui-pki/server/server.crt;" in text
    assert "letsencrypt" not in text


def test_compose_certbot_only_when_enabled():
    text = render.render_template("docker-compose.yml.j2", NGINX_SEPARATE)
    assert "image: certbot/certbot" not in text
    assert "letsencrypt" not in text
    assert "- ./certbot-www:/var/www/certbot:ro" in text  # webroot mount is always there

    context = NGINX_SEPARATE | {"letsencrypt_enabled": True}
    text = render.render_template("docker-compose.yml.j2", context)
    assert "image: certbot/certbot" in text
    assert "certbot renew" in text
    assert "- ./letsencrypt:/etc/letsencrypt:ro" in text
    assert "nginx -s reload" in text  # nginx picks up renewed certs


def test_compose_mounts_htpasswd_only_when_enabled():
    assert "./security" not in render.render_template("docker-compose.yml.j2", NGINX_SEPARATE)
    context = NGINX_SEPARATE | {"basic_auth_enabled": True}
    text = render.render_template("docker-compose.yml.j2", context)
    assert "- ./security:/etc/teddycloudhelper/security:ro" in text
