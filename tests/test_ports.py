import socket

from teddycloudhelper import ports
from teddycloudhelper.state import AppState


def test_required_ports_direct():
    state = AppState()  # direct mode, defaults
    assert ports.required_ports(state) == [80, 443, 8443]


def test_required_ports_nginx_separate():
    state = AppState(deployment_mode="nginx", webui_port_mode="separate", webui_port=9443)
    assert ports.required_ports(state) == [80, 443, 9443]


def test_required_ports_nginx_shared():
    state = AppState(deployment_mode="nginx", webui_port_mode="shared")
    assert ports.required_ports(state) == [80, 443]


def test_required_ports_dedupes():
    state = AppState(deployment_mode="direct", http_port=8443, webui_port=8443)
    assert ports.required_ports(state) == [8443, 443]


def test_check_ports_detects_listener():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert ports.check_ports([port]) == [port]
    # closed again → free
    assert ports.check_ports([port]) == []
