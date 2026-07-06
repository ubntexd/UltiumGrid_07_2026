#!/usr/bin/env python3
"""Beacon HYPER — expose 8082/8002 → stack docker 18280/18200."""

from __future__ import annotations

import select
import socket
import threading

UPSTREAM = {
    8082: ("127.0.0.1", 18280),
    8002: ("127.0.0.1", 18200),
}


def pipe(a: socket.socket, b: socket.socket) -> None:
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r:
                break
            for src in r:
                dst = b if src is a else a
                data = src.recv(65536)
                if not data:
                    return
                dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def serve(listen_port: int, upstream_host: str, upstream_port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", listen_port))
    srv.listen(64)
    print(f"UltiumGrid HYPER beacon http://127.0.0.1:{listen_port}", flush=True)
    while True:
        client, _ = srv.accept()
        try:
            upstream = socket.create_connection((upstream_host, upstream_port), timeout=5)
        except OSError:
            client.close()
            continue
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()


def main() -> None:
    threads = []
    for public_port, upstream in UPSTREAM.items():
        t = threading.Thread(target=serve, args=(public_port, *upstream), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
