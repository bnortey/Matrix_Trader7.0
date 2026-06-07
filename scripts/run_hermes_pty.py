#!/usr/bin/env python3
"""Run Hermes with a pseudo-TTY and a short file-based prompt request.

Hermes chat's -q flag takes the query as an argv value. The MT7 context plus
research packet can exceed OS argv limits, so this wrapper passes only a short
instruction and asks Hermes to read the full prompt file itself.
"""

import os
import pty
import select
import subprocess
import sys


prompt_file = os.path.abspath(sys.argv[1])
memo_file = os.path.abspath(sys.argv[2])
query = (
    "Read the complete advisory prompt from this local file and answer it. "
    "Do not summarize the file path; produce the requested Hermes memo only. "
    f"Prompt file: {prompt_file}"
)

master_fd, slave_fd = pty.openpty()
stdout = open(memo_file, "w", encoding="utf-8")
proc = None
try:
    proc = subprocess.Popen(
        ["hermes", "chat", "-Q", "-q", query],
        stdin=slave_fd,
        stdout=stdout,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)
    while proc.poll() is None:
        readable, _, _ = select.select([master_fd], [], [], 0.5)
        if readable:
            try:
                os.read(master_fd, 4096)
            except OSError:
                break
    proc.wait()
finally:
    stdout.close()
    try:
        os.close(master_fd)
    except OSError:
        pass

sys.exit(proc.returncode if proc is not None else 1)
