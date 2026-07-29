import asyncio
import shlex
from backend.core.safe_subprocess import run_capture, split_argv

c = 'python -c "print(123)"'
print("split", split_argv(c))
print("shlex", shlex.split(c, posix=False))
r = asyncio.run(run_capture(c, timeout=30))
print(r)
