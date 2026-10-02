"""Let benchmark containers reach the Ollama on this machine.

    tbench/ollama_bridge.py [port] [pid to stop with]

Ollama listens on 127.0.0.1 only, which a Docker container can't reach. This listens on the given
port (default 11435) on every address and passes each connection on to 127.0.0.1:11434, but only
from Docker's container addresses (172.16.0.0/12): nothing else on the network gets in. With a pid
it stops when that process does (tbench/local.sh passes Harbor's). Plain Python (no packages).
"""

import asyncio
import ipaddress
import os
import sys

OLLAMA = ("127.0.0.1", 11434)
DOCKER = ipaddress.ip_network("172.16.0.0/12")


def allowed(peer):
    try:
        return ipaddress.ip_address(peer) in DOCKER
    except ValueError:
        return False


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(reader, writer):
    peer = (writer.get_extra_info("peername") or ("",))[0]
    if not allowed(peer):
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.open_connection(*OLLAMA)
    except OSError:
        writer.close()
        return
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def watch(pid):
    while True:
        await asyncio.sleep(5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass


async def main(argv):
    port = int(argv[0]) if argv else 11435
    server = await asyncio.start_server(handle, "0.0.0.0", port)
    async with server:
        if len(argv) > 1:
            await watch(int(argv[1]))
        else:
            await server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
