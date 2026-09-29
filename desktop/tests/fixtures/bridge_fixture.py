import json
from pathlib import Path
import subprocess
import sys
import time


CAPABILITIES = {
    "bridgeProtocolVersion": 1,
    "personalMarkSchemaVersions": [1, 2],
    "renderProfiles": [{"id": "shirushi-typed", "version": 1, "state": "ASSETS_UNAVAILABLE"}],
    "capabilities": {
        "personalMarkRead": True,
        "personalMarkWrite": False,
        "nativeTargetSelection": False,
        "coreAdd": False,
        "coreVerify": False,
        "coreReadback": False,
        "c2paPersonalMarkEmbedding": False,
        "explorerIntegration": False,
    },
}


def send(request_id, result):
    print(json.dumps({"protocolVersion": 1, "requestId": request_id, "ok": True, "result": result}, separators=(",", ":")), flush=True)


mode = sys.argv[1]
for index, line in enumerate(sys.stdin.buffer):
    request = json.loads(line)
    request_id = request["requestId"]
    if index == 0:
        if mode == "timeout":
            time.sleep(30)
        if mode == "timeout_descendant":
            creation_flags = 0x08000000 if sys.platform == "win32" else 0
            descendant = subprocess.Popen(
                [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                creationflags=creation_flags,
            )
            Path(sys.argv[2]).write_text(str(descendant.pid), encoding="ascii")
            time.sleep(30)
        if mode == "crash":
            raise SystemExit(7)
        if mode == "stderr":
            print("unexpected", file=sys.stderr, flush=True)
            time.sleep(1)
            continue
        if mode == "wrong_id":
            send("wrong", CAPABILITIES)
            continue
        if mode == "wrong_version":
            print(json.dumps({"protocolVersion": 2, "requestId": request_id, "ok": True, "result": CAPABILITIES}), flush=True)
            continue
        if mode == "duplicate_key":
            print('{"protocolVersion":1,"requestId":"%s","requestId":"%s","ok":true,"result":{}}' % (request_id, request_id), flush=True)
            continue
        if mode == "depth":
            print(json.dumps({"protocolVersion": 1, "requestId": request_id, "ok": True, "result": [[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]}), flush=True)
            continue
        if mode == "oversize":
            sys.stdout.write("x" * (4194304 + 1))
            sys.stdout.flush()
            continue
        send(request_id, CAPABILITIES)
        if mode == "extra_stdout":
            send("late", CAPABILITIES)
        continue
    if mode == "slow_after_handshake":
        time.sleep(0.5)
    send(request_id, {"contract": "shirushi-personal-mark-read", "contractVersion": 1, "state": "absent"})
