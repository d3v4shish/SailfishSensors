"""The small newline-delimited JSON protocol shared by bridge components."""

import json


MAX_MESSAGE_BYTES = 64 * 1024


class ProtocolError(ValueError):
    """A peer sent an invalid protocol message."""


def encode_message(message):
    """Return one compact JSON protocol message, including its newline."""
    if not isinstance(message, dict):
        raise ProtocolError("a message must be an object")
    try:
        encoded = json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ProtocolError("message cannot be encoded as JSON: {}".format(error))
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message exceeds 64 KiB")
    return encoded + b"\n"


def decode_message(line):
    """Decode and validate a single JSON object without its trailing newline."""
    if not isinstance(line, bytes):
        raise ProtocolError("message must be bytes")
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message exceeds 64 KiB")
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("invalid JSON: {}".format(error))
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    return message


class JsonLineSocket(object):
    """Buffered JSON-lines I/O for a connected socket."""

    def __init__(self, sock):
        self.sock = sock
        self._buffer = bytearray()

    def send(self, message):
        self.sock.sendall(encode_message(message))

    def receive(self):
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[:newline + 1]
                if not line:
                    continue
                return decode_message(line)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("peer closed the connection")
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise ProtocolError("message exceeds 64 KiB")
