import socket
import time

UNROUTABLE = {"10.255.255.1"}
_connect = socket.socket.connect


def _connect_like_offline_network(self, address):
    if isinstance(address, tuple) and address and address[0] in UNROUTABLE:
        timeout = self.gettimeout()
        time.sleep(timeout if timeout is not None else 75)
        raise TimeoutError("timed out")
    return _connect(self, address)


socket.socket.connect = _connect_like_offline_network
