"""游戏网关状态后备查询的无网络协议测试。"""

import unittest
from unittest.mock import patch

from module.server_status import (
    GatewayServer,
    MSG_SC_10019,
    ServerStatusProtocolError,
    _build_packet,
    _encode_varint,
    _encode_varint_field,
    _parse_http_body,
    _parse_tcp_response,
    query_server,
)


def encode_bytes_field(field_number: int, value: bytes) -> bytes:
    """构造测试所需的 protobuf bytes 字段。"""
    return _encode_varint((field_number << 3) | 2) + _encode_varint(len(value)) + value


class TestGatewayProtocol(unittest.TestCase):
    """覆盖 TCP protobuf 与 HTTP JSON 两种网关协议。"""

    def test_tcp_server_list_decodes_status_and_gbk_name(self):
        server = b''.join((
            _encode_varint_field(1, 7),
            _encode_varint_field(4, 1),
            encode_bytes_field(5, '莱茵演习'.encode('gbk')),
            _encode_varint_field(6, 2),
            _encode_varint_field(7, 4),
        ))
        body = encode_bytes_field(1, server)
        response = _build_packet(MSG_SC_10019, body)

        servers = _parse_tcp_response(response)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0], GatewayServer(7, '莱茵演习', 1, 2, 4))
        self.assertEqual(servers[0].status, 'maintenance')

    def test_http_server_list_decodes_json(self):
        response = (
            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n'
            b'[{"id": 3, "name": "Sandy", "state": 3, "flag": 1, "sort": 2}]'
        )

        servers = _parse_http_body(response)

        self.assertEqual(servers, [GatewayServer(3, 'Sandy', 3, 1, 2)])
        self.assertEqual(servers[0].status, 'reg_full')

    def test_invalid_tcp_frame_is_rejected(self):
        with self.assertRaises(ServerStatusProtocolError):
            _parse_tcp_response(b'\x00\x04\x00\x27\x23\x00\x00')

    def test_query_server_selects_requested_id(self):
        servers = [
            GatewayServer(1, 'Avrora', 0, 0, 1),
            GatewayServer(2, 'Lexington', 2, 0, 2),
        ]
        with patch('module.server_status.query_region', return_value=servers):
            server = query_server('en', 2)

        self.assertEqual(server.name, 'Lexington')
        self.assertEqual(server.status, 'full')


if __name__ == '__main__':
    unittest.main()
