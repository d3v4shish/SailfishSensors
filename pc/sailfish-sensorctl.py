#!/usr/bin/env python3
"""Small command-line client for the local Sailfish sensor API."""

import argparse
import json
import os

from sailfish_sensors import SensorClient


def default_socket_path():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        return os.path.join(runtime_dir, "sailfish-sensors.sock")
    return "/tmp/sailfish-sensors-{}.sock".format(os.getuid())


def main():
    parser = argparse.ArgumentParser(description="Read a local Sailfish phone sensor.")
    parser.add_argument("--socket", default=default_socket_path())
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("sensors")
    get = subcommands.add_parser("get")
    get.add_argument("sensor")
    get.add_argument("--max-age-ms", type=int, default=1000)
    watch = subcommands.add_parser("watch")
    watch.add_argument("sensors", nargs="*")
    arguments = parser.parse_args()

    client = SensorClient(arguments.socket)
    if arguments.command == "sensors":
        print(json.dumps(client.sensors(), indent=2, sort_keys=True))
    elif arguments.command == "get":
        print(json.dumps(client.get(arguments.sensor, arguments.max_age_ms), indent=2, sort_keys=True))
    else:
        for reading in client.watch(arguments.sensors or None):
            print(json.dumps(reading, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
