"""Local pipe fixture; never invokes a model, microphone, or screen capture."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def event(text):
    return {"type": "stream_event", "event": {"type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text}}}


def emit(value, newline=True):
    os.write(1, json.dumps(value, ensure_ascii=False).encode() + (b"\n" if newline else b""))


scenario = sys.argv[1]
if scenario == "stdin_backpressure":
    time.sleep(1.5)
    sys.exit(0)
if scenario == "reject_stdin":
    os.close(0)
    emit({"type": "result", "result": "answer without the question", "subtype": "success"})
    time.sleep(0.05)
    sys.exit(0)
sys.stdin.read()
if scenario == "batched":
    data = (json.dumps(event("Привет "), ensure_ascii=False) + "\n" +
            json.dumps(event("мир"), ensure_ascii=False) + "\n" +
            json.dumps({"type": "result", "result": "Привет мир", "subtype": "success"}) + "\n").encode()
    split = data.index("Привет".encode()) + 1
    os.write(1, data[:split])
    os.write(1, data[split:])
elif scenario == "large_stderr":
    os.write(2, b"private diagnostic never returned\n" * 65536)
    emit({"type": "result", "result": "answer", "subtype": "success"})
elif scenario == "incomplete":
    emit(event("partial"))
    os.write(1, b'{"type": "result", "result": "unfinished')
    time.sleep(1.5)
elif scenario == "timeout":
    emit(event("partial"))
    time.sleep(1.5)
elif scenario == "exit_error":
    emit(event("partial"))
    sys.exit(7)
elif scenario == "result_error":
    emit(event("partial"))
    emit({"type": "result", "is_error": True, "result": "private diagnostic"})
elif scenario == "delta_only":
    emit(event("unfinished answer"))
elif scenario == "corrected_final":
    emit(event("incorrect partial"))
    emit({"type": "result", "result": "correct final", "subtype": "success"})
elif scenario == "malformed_tail":
    os.write(1, b'{"type": "result"')
elif scenario == "oversized_line":
    os.write(1, b"{" + b"x" * (2 * 1024 * 1024))
elif scenario in ("descendant", "plain_descendant"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    Path(sys.argv[2]).write_text(str(child.pid))
    if scenario == "descendant":
        emit(event("partial"))
    else:
        print("partial", flush=True)
elif scenario == "plain":
    print("answer")
elif scenario == "plain_error":
    print("partial")
    sys.exit(7)
elif scenario == "plain_timeout":
    print("partial", flush=True)
    time.sleep(1.5)
elif scenario == "stderr_only":
    os.write(2, b"private diagnostic")
elif scenario == "empty":
    pass
else:
    emit({"type": "result", "result": "answer", "subtype": "success"}, newline=False)
