"""
Run every test suite, each in its own process (they patch the driver's
register functions, so they must not share an interpreter).

    python3 tests/run_all.py

These run on a desktop CPython using fake `machine`/`utime` modules
(tests/mocks). They check the driver/example LOGIC -- register values, SPI
header bytes, callback ordering, interrupt dispatch. They cannot catch
electrical, wiring or RF problems; only real hardware can.
"""
import os, subprocess, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
paths = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
failed = []
for path in paths:
    name = os.path.basename(path)
    r = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=120)
    last = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
    print("%-24s %s  %s" % (name, "ok  " if r.returncode == 0 else "FAIL", last))
    if r.returncode != 0:
        failed.append(name)
        print(r.stdout[-1500:], r.stderr[-1500:])
print("\n%d suites, %d failed" % (len(paths), len(failed)))
sys.exit(1 if failed else 0)
