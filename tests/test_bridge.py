import os
import pathlib
import socket
import socketserver
import sys
import tempfile
import threading
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "pc"))

from sailfish_sensors import SensorClient, SensorError
from sailfish_sensors.bridge import LocalServer, PhoneWorker, SensorState
from sailfish_sensors.iio import IioError, RECORD, parse_mount_matrix, record_for_reading
from sailfish_sensors.protocol import JsonLineSocket, ProtocolError, decode_message, encode_message
from sailfish_gpsd_feed import LocationWorker, NmeaError, NmeaServer, NmeaState, rmc_sentence


READING = {
    "type": "reading",
    "sensor": "accelerometer",
    "timestamp_us": 123456,
    "values": {"x": 1.0, "y": -2.0, "z": 9.8},
}

LOCATION_READING = {
    "type": "reading",
    "sensor": "location",
    "timestamp_utc_ms": 1704164645678,
    "values": {
        "latitude": 12.345678,
        "longitude": -98.765432,
        "speed_mps": 5.0,
        "bearing_degrees": 123.4,
    },
}


class ProtocolTests(unittest.TestCase):
    def test_json_lines_handles_fragmented_messages(self):
        left, right = socket.socketpair()
        try:
            receiver = JsonLineSocket(left)
            right.sendall(b'{"type":"reading"')
            right.sendall(b',"sensor":"light"}\n')
            self.assertEqual(receiver.receive(), {"type": "reading", "sensor": "light"})
        finally:
            left.close()
            right.close()

    def test_protocol_rejects_non_object_and_oversized_lines(self):
        with self.assertRaises(ProtocolError):
            decode_message(b"[]")
        with self.assertRaises(ProtocolError):
            decode_message(b"x" * (64 * 1024 + 1))
        with self.assertRaises(ProtocolError):
            encode_message({"payload": "x" * (64 * 1024)})


class IioRecordTests(unittest.TestCase):
    def test_accelerometer_record_uses_local_timestamp_and_mount_matrix(self):
        payload = record_for_reading({
            "sensor": "accelerometer",
            "received_ns": 5000,
            "values": {"x": 1.0, "y": -2.0, "z": 9.8},
        }, parse_mount_matrix("0,1,0,-1,0,0,0,0,1"))
        self.assertEqual(len(payload), 28)
        version, sensor, count, reserved, timestamp, x, y, z = RECORD.unpack(payload)
        self.assertEqual((version, sensor, count, reserved, timestamp), (1, 1, 3, 0, 5000))
        self.assertEqual((x, y, z), (-2000000, -1000000, 9800000))

    def test_scalar_and_invalid_readings_are_checked(self):
        payload = record_for_reading({
            "sensor": "proximity",
            "received_ns": 1,
            "values": {"close": True},
        })
        self.assertEqual(RECORD.unpack(payload)[2], 1)
        with self.assertRaises(IioError):
            record_for_reading({"sensor": "light", "received_ns": 1, "values": {"lux": "NaN"}})
        with self.assertRaises(IioError):
            parse_mount_matrix("1,2,3")


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "sensors.sock")
        self.state = SensorState()
        self.server = LocalServer(self.path, self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.close()
        self.thread.join(2.0)
        self.directory.cleanup()

    def test_client_reads_current_value_and_rejects_stale_values(self):
        self.state.set_phone_sensors(["accelerometer"])
        self.state.update(READING)
        client = SensorClient(self.path)

        self.assertEqual(client.sensors(), ["accelerometer"])
        reading = client.get("accelerometer", max_age_ms=10000)
        self.assertEqual(reading["values"], READING["values"])
        self.assertEqual(reading["timestamp_us"], 123456)

        with self.assertRaises(SensorError):
            client.get("gyroscope", max_age_ms=10000)

    def test_refuses_to_replace_regular_file(self):
        regular_path = os.path.join(self.directory.name, "do-not-replace")
        with open(regular_path, "w") as file_handle:
            file_handle.write("preserve me")
        with self.assertRaises(RuntimeError):
            LocalServer(regular_path, self.state)


class FakePhoneHandler(socketserver.BaseRequestHandler):
    def handle(self):
        stream = JsonLineSocket(self.request)
        hello = stream.receive()
        if hello != {"type": "hello", "protocol": 1, "token": self.server.token}:
            return
        stream.send({"type": "hello", "protocol": 1, "sensors": ["accelerometer"]})
        subscribe = stream.receive()
        if subscribe != {"type": "subscribe", "sensors": ["accelerometer"]}:
            return
        if self.server.reading_before_subscribed:
            stream.send(READING)
        stream.send({"type": "subscribed", "sensors": ["accelerometer"]})
        if not self.server.reading_before_subscribed:
            stream.send(READING)
        self.server.published.set()


class PhoneWorkerTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakePhoneHandler)
        self.server.daemon_threads = True
        self.server.token = "deterministic-test-token"
        self.server.published = threading.Event()
        self.server.reading_before_subscribed = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)

    def test_worker_authenticates_subscribes_and_caches_reading(self):
        state = SensorState()
        worker = PhoneWorker(
            "127.0.0.1",
            self.server.server_address[1],
            self.server.token,
            ["accelerometer"],
            state,
            reconnect_seconds=60.0,
        )
        waiting = threading.Event()
        result = {}

        def wait_for_event():
            waiting.set()
            result["value"] = state.wait_for_update(0, ["accelerometer"], threading.Event())

        waiter = threading.Thread(target=wait_for_event, daemon=True)
        waiter.start()
        self.assertTrue(waiting.wait(2.0))
        worker.start()
        self.assertTrue(self.server.published.wait(2.0))
        waiter.join(2.0)
        worker.stop()

        self.assertFalse(waiter.is_alive(), "worker did not publish a reading")
        sequence, reading = result["value"]
        self.assertEqual(sequence, 1)
        self.assertEqual(reading["sensor"], "accelerometer")
        self.assertEqual(reading["values"]["z"], 9.8)

    def test_worker_accepts_reading_sent_before_subscription_acknowledgement(self):
        self.server.reading_before_subscribed = True
        state = SensorState()
        worker = PhoneWorker(
            "127.0.0.1",
            self.server.server_address[1],
            self.server.token,
            ["accelerometer"],
            state,
            reconnect_seconds=60.0,
        )
        worker.start()
        self.assertTrue(self.server.published.wait(2.0))
        for unused_attempt in range(20):
            reading, error = state.get("accelerometer", max_age_ms=10000)
            if reading is not None:
                break
            threading.Event().wait(0.05)
        worker.stop()

        self.assertIsNone(error)
        self.assertEqual(reading["timestamp_us"], 123456)


class GpsdFeedTests(unittest.TestCase):
    def test_rmc_sentence_is_deterministic_and_does_not_claim_satellites(self):
        self.assertEqual(
            rmc_sentence(LOCATION_READING),
            b"$GPRMC,030405.67,A,1220.7407,N,09845.9259,W,9.72,123.4,020124,,,A*70\r\n",
        )

    def test_rmc_rejects_missing_or_untruthful_location_fields(self):
        invalid_timestamp = dict(LOCATION_READING)
        invalid_timestamp["timestamp_utc_ms"] = 0
        with self.assertRaises(NmeaError):
            rmc_sentence(invalid_timestamp)

        invalid_coordinate = dict(LOCATION_READING)
        invalid_coordinate["values"] = dict(LOCATION_READING["values"], latitude=91.0)
        with self.assertRaises(NmeaError):
            rmc_sentence(invalid_coordinate)

        invalid_speed = dict(LOCATION_READING)
        invalid_speed["values"] = dict(LOCATION_READING["values"], speed_mps=-1.0)
        with self.assertRaises(NmeaError):
            rmc_sentence(invalid_speed)

    def test_loopback_server_replays_the_latest_complete_nmea_update(self):
        state = NmeaState()
        server = NmeaServer("127.0.0.1", 0, state)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = socket.create_connection(server.server_address, timeout=2.0)
        try:
            payload = rmc_sentence(LOCATION_READING)
            server.publish(payload)
            client.settimeout(2.0)
            self.assertEqual(client.recv(len(payload)), payload)
        finally:
            client.close()
            server.close()
            thread.join(2.0)


class FakeLocationHandler(socketserver.BaseRequestHandler):
    def handle(self):
        stream = JsonLineSocket(self.request)
        hello = stream.receive()
        if hello != {"type": "hello", "protocol": 1, "token": self.server.token}:
            return
        stream.send({"type": "hello", "protocol": 1, "sensors": ["location"]})
        subscribe = stream.receive()
        if subscribe != {"type": "subscribe", "sensors": ["location"]}:
            return
        stream.send({"type": "subscribed", "sensors": ["location"]})
        stream.send(LOCATION_READING)
        self.server.published.set()


class LocationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeLocationHandler)
        self.server.daemon_threads = True
        self.server.token = "deterministic-test-token"
        self.server.published = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2.0)

    def test_worker_authenticates_and_emits_rmc_only_for_a_valid_location(self):
        updates = []
        published = threading.Event()

        def receive(payload):
            updates.append(payload)
            published.set()

        worker = LocationWorker(
            "127.0.0.1",
            self.server.server_address[1],
            self.server.token,
            receive,
            reconnect_seconds=60.0,
        )
        worker.start()
        self.assertTrue(self.server.published.wait(2.0))
        self.assertTrue(published.wait(2.0))
        worker.stop()

        self.assertEqual(updates, [rmc_sentence(LOCATION_READING)])


if __name__ == "__main__":
    unittest.main()
