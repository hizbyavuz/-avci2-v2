"""Run TA tests with external sockets prohibited; local WS fixtures are allowed."""
import ipaddress
import socket
import unittest


def main():
    original=socket.socket.connect;original_ex=socket.socket.connect_ex
    def allowed(address):
        try:return ipaddress.ip_address(address[0]).is_loopback
        except (ValueError,TypeError):return False
    def connect(sock,address):
        if not allowed(address):raise RuntimeError('TEST_EXTERNAL_NETWORK_DISABLED')
        return original(sock,address)
    def connect_ex(sock,address):
        if not allowed(address):raise RuntimeError('TEST_EXTERNAL_NETWORK_DISABLED')
        return original_ex(sock,address)
    socket.socket.connect=connect;socket.socket.connect_ex=connect_ex
    try:
        suite=unittest.defaultTestLoader.discover('tests',pattern='test_ta_paper*.py')
        result=unittest.TextTestRunner(verbosity=1).run(suite)
    finally:
        socket.socket.connect=original;socket.socket.connect_ex=original_ex
    return not result.wasSuccessful()


if __name__=='__main__':raise SystemExit(main())
