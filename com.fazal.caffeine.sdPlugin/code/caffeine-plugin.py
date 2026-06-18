#!/usr/bin/env python3
"""OpenDeck plugin: toggle GNOME Caffeine and keep the key icon in sync with
Caffeine's real state (including changes made from the GNOME top-bar or after a
reboot).

It reads/toggles Caffeine through dconf. When OpenDeck runs as a Flatpak the
plugin executes inside the sandbox, so dconf calls are routed to the host via
`flatpak-spawn --host`; on a native install they run directly. State changes are
picked up event-driven via `dconf watch` (no polling).

State mapping: state 0 = ON icon (steaming cup), state 1 = OFF icon (grey cup).
"""

import sys
import os
import json
import socket
import base64
import struct
import threading
import subprocess
import time

CAF = "/org/gnome/shell/extensions/caffeine"
WATCH_RESTART_SECONDS = 3
# Cap inbound WebSocket frames so a malformed/huge declared length can't grow
# our read buffer without bound (memory-exhaustion guard).
MAX_FRAME_BYTES = 1 << 20  # 1 MiB

# OpenDeck plugins run inside the Flatpak sandbox; reach the host with
# flatpak-spawn. On a native install we call dconf directly.
IN_FLATPAK = os.path.exists("/.flatpak-info")


def log(msg):
    sys.stderr.write(f"[caffeine-plugin] {msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# dconf helpers (routed to the host when sandboxed)
# ---------------------------------------------------------------------------
def host_cmd(*args):
    prefix = ["flatpak-spawn", "--host"] if IN_FLATPAK else []
    return prefix + list(args)


def host(*args):
    return subprocess.run(host_cmd(*args), capture_output=True, text=True)


def caffeine_on():
    """True if Caffeine is currently active. Raises on a failed read so callers
    never mistake an error for 'off'."""
    r = host("dconf", "read", f"{CAF}/user-enabled")
    if r.returncode != 0:
        raise RuntimeError(f"dconf read failed: {r.stderr.strip()}")
    return r.stdout.strip() == "true"


def caffeine_toggle():
    """Flip Caffeine via its dedicated cli-toggle key (changing the value is
    what triggers the extension to toggle)."""
    # Best-effort read: if it fails, cur defaults to False and the next
    # `dconf watch` reconcile corrects any wrong guess, so we don't raise here.
    r = host("dconf", "read", f"{CAF}/cli-toggle")
    cur = r.stdout.strip() == "true"
    host("dconf", "write", f"{CAF}/cli-toggle", "false" if cur else "true")


# ---------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455, client side, stdlib only)
#
# Assumes the server (OpenDeck) sends small, unfragmented text frames, which is
# how it behaves in practice; continuation frames (opcode 0x0) are not handled.
# ---------------------------------------------------------------------------
class WS:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port))
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET / HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("handshake closed")
            buf += chunk
        status = buf.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise ConnectionError(f"handshake failed: {status!r}")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.send_lock = threading.Lock()

    def _frame(self, opcode, data):
        hdr = bytearray([0x80 | opcode])
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        hdr += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return bytes(hdr) + masked

    def send_json(self, obj):
        frame = self._frame(0x1, json.dumps(obj).encode())
        with self.send_lock:
            self.sock.sendall(frame)

    def send_pong(self, payload):
        frame = self._frame(0xA, payload)
        with self.send_lock:
            self.sock.sendall(frame)

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        """Return (opcode, payload_bytes) for one frame."""
        b0, b1 = self._recv_exact(2)
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._recv_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._recv_exact(8))[0]
        if ln > MAX_FRAME_BYTES:
            raise ConnectionError(f"frame too large: {ln}")
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(ln) if ln else b""
        if masked:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return opcode, payload


# ---------------------------------------------------------------------------
# Plugin main loop
# ---------------------------------------------------------------------------
def parse_args(argv):
    d = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("-") and i + 1 < len(argv):
            d[a[1:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return d


def main():
    args = parse_args(sys.argv[1:])
    port = int(args["port"])
    register_event = args.get("registerEvent", "registerPlugin")
    plugin_uuid = args["pluginUUID"]

    ws = WS(port)
    ws.send_json({"event": register_event, "uuid": plugin_uuid})
    log(f"registered as {plugin_uuid} on port {port} (flatpak={IN_FLATPAK})")

    contexts = set()
    lock = threading.Lock()
    last = {"on": None}  # mutable cell so the nested closures can update state

    def push(on, ctxs):
        state = 0 if on else 1  # state 0 = ON icon, state 1 = OFF icon
        for c in ctxs:
            try:
                ws.send_json(
                    {"event": "setState", "context": c, "payload": {"state": state}}
                )
            except Exception as e:
                log(f"setState failed: {e}")

    def reconcile():
        """Read the real state and update any visible keys (skip on read error
        so a transient failure never flips the icon to a wrong value)."""
        try:
            on = caffeine_on()
        except Exception as e:
            log(f"state read failed: {e}")
            return
        with lock:
            ctxs = list(contexts)
            changed = on != last["on"]
            last["on"] = on
        if ctxs and changed:
            push(on, ctxs)

    def watcher():
        """Event-driven sync: react to any change under the Caffeine dconf path.
        Restarts the watch subprocess if it ever exits."""
        while True:
            proc = None
            try:
                proc = subprocess.Popen(
                    host_cmd("dconf", "watch", CAF + "/"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                reconcile()  # catch up on (re)start
                for line in proc.stdout:
                    if line.startswith(CAF):
                        reconcile()
            except Exception as e:
                log(f"watch error: {e}")
            finally:
                if proc is not None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
            time.sleep(WATCH_RESTART_SECONDS)

    threading.Thread(target=watcher, daemon=True).start()

    while True:
        try:
            opcode, payload = ws.recv()
        except Exception as e:
            log(f"recv error: {e}")
            break
        if opcode == 0x8:  # close
            log("server closed connection")
            break
        if opcode == 0x9:  # ping
            ws.send_pong(payload)
            continue
        if opcode != 0x1:  # only care about text
            continue
        try:
            msg = json.loads(payload.decode())
        except Exception:
            continue

        event = msg.get("event")
        ctx = msg.get("context")

        # A failure handling one event must never take down the plugin.
        try:
            if event == "willAppear" and ctx:
                with lock:
                    contexts.add(ctx)
                on = caffeine_on()
                with lock:
                    last["on"] = on
                push(on, [ctx])
            elif event == "willDisappear" and ctx:
                with lock:
                    contexts.discard(ctx)
            elif event == "keyDown" and ctx:
                # Use cached state; fall back to a live read if not synced yet.
                with lock:
                    before = last["on"]
                if before is None:
                    before = caffeine_on()
                caffeine_toggle()
                predicted = not before  # instant feedback; watcher confirms
                with lock:
                    last["on"] = predicted
                    ctxs = list(contexts)
                push(predicted, ctxs)
        except Exception as e:
            log(f"error handling {event}: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"fatal: {e}")
        sys.exit(1)
