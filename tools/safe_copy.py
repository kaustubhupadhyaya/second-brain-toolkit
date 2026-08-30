"""Rate-capped, choke-aware recursive copy for flaky USB enclosures.

v2 (2026-08-30) - rewritten after an adversarial review found real defects
in v1, several confirmed live on this machine:
  - v1 timed a chunk AFTER write()+fsync() returned, so a write that never
    returns (the bridge's actual failure mode: "20-200+ seconds, possibly
    indefinite") never triggered choke detection at all. Fixed by running
    each write in a watcher thread and giving up after WRITE_TIMEOUT even
    if the thread never returns (CPython releases the GIL for blocking
    file I/O, so the main thread's join(timeout) reliably returns on
    schedule regardless of what the OS is doing).
  - v1 caught bare `OSError`, so a locked/unreadable SOURCE file on C:
    triggered a real USB device power-cycle and could exhaust the reset
    budget on a perfectly healthy bridge. Fixed by only treating
    destination-side failures as bridge chokes; source-side errors are
    logged and the file is skipped.
  - v1 never checked the schtasks return code, so a missing/disabled task
    silently burned the full poll window. Fixed: checked immediately.
  - v1's poll deadline (150s) was shorter than the reset script's own
    worst case at the time. ssd_reset.ps1 is now bounded to ~160s worst
    case; RESET_POLL_DEADLINE below is set with margin above that.
  - v1 had no defense against a stale leftover verdict in reset_log.txt
    (e.g. from a dropped/overlapping trigger). Fixed: the reset script's
    log now starts with a timestamped run marker; a verdict is trusted
    only if that marker is newer than this run's trigger time.

Empirical facts about the hardware (stress-tested 2026-08-30): a fresh
bridge sustains ~80 MB/s; ~120 MB/s chokes it within ~1.4 GB; NO rate has
been proven safe for multi-GB jobs (a 20GB job choked at the "validated"
30 MB/s cap after ~3.2GB). The rate cap reduces how often chokes happen;
it is not a safety guarantee. The actual safety net is choke detection +
bounded auto-recovery + per-file resume, which is why this script must
never give up silently.

Usage:
  python safe_copy.py <src-dir-or-file> <dst-dir> [rate_mb]
"""
import datetime, os, re, shutil, subprocess, sys, threading, time
from pathlib import Path

DEFAULT_RATE = 20          # MB/s - a starting point, not a safety proof (see docstring)
CHUNK = 8 * 1024 * 1024
WRITE_TIMEOUT = 15.0       # s - a chunk not done by then is a choke, not "slow"
RESET_TASK = "SSD-Bridge-Reset"
RESET_LOG = Path(r"C:\Users\Admin\SSDTools\reset_log.txt")
RESET_POLL_DEADLINE = 220  # s - above ssd_reset.ps1's ~160s worst case, below its 300s task limit
MAX_RESETS = 2             # per run

RUN_MARKER_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) RESET RUN START")


class DestChoke(Exception):
    """Destination-side failure: bridge/device suspect, safe to reset+retry."""


class SourceError(Exception):
    """Source-side failure: nothing wrong with E:, skip this file."""


def _write_chunk(fout, buf, box):
    try:
        fout.write(buf)
        os.fsync(fout.fileno())
        box["ok"] = True
    except OSError as e:
        box["ok"] = False
        box["err"] = e


def write_with_timeout(fout, buf, timeout):
    """Returns normally on success. Raises DestChoke on error OR block.
    On a genuine block, the writer thread is abandoned (daemon) rather than
    joined again - Windows will tear it down when the process exits, and
    the destination file is never trusted again this attempt."""
    box = {}
    t = threading.Thread(target=_write_chunk, args=(fout, buf, box), daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise DestChoke(f"write blocked past {timeout}s (bridge likely choked)")
    if not box.get("ok"):
        raise DestChoke(f"write error: {box.get('err')}")


def software_reset() -> bool:
    """Trigger the elevated reset task; True if it reports a fresh, healthy verdict."""
    trigger_time = datetime.datetime.now()
    print(f"  Triggering scheduled task '{RESET_TASK}' (driver-level replug)...", flush=True)
    try:
        r = subprocess.run(["schtasks", "/run", "/tn", RESET_TASK],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"  could not invoke schtasks: {e}", flush=True)
        return False
    if r.returncode != 0:
        print(f"  schtasks /run failed (rc={r.returncode}): {r.stderr.strip()}", flush=True)
        return False

    deadline = time.monotonic() + RESET_POLL_DEADLINE
    started = False
    while time.monotonic() < deadline:
        time.sleep(4)
        try:
            text = RESET_LOG.read_text(encoding="ascii", errors="replace")
        except OSError:
            continue
        m = RUN_MARKER_RE.search(text)
        if not m:
            continue
        try:
            run_start = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            continue
        if run_start < trigger_time - datetime.timedelta(seconds=2):
            continue                                        # stale leftover run, keep waiting
        started = True
        if "RESET SUCCESS" in text:
            print("  bridge reset OK - resuming", flush=True)
            return True
        if "RESET FAILED" in text or "not present" in text or "LETTER-CHANGED" in text:
            print("  reset task reports the bridge is not recoverable right now", flush=True)
            return False
    print(f"  reset task gave no fresh verdict within {RESET_POLL_DEADLINE}s"
          f"{'' if started else ' (may not have started - task busy or disabled)'}", flush=True)
    return False


def copy_file(src: Path, dst: Path, rate_mb: float, attempt: int) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return                                              # resume: already done
    tmp = dst.with_suffix(dst.suffix + f".part{attempt}")
    try:
        fin = open(src, "rb")
    except OSError as e:
        raise SourceError(str(e)) from e
    with fin:
        try:
            fout = open(tmp, "wb", buffering=0)
        except OSError as e:
            raise DestChoke(f"cannot open destination: {e}") from e
        with fout:
            while True:
                try:
                    buf = fin.read(CHUNK)
                except OSError as e:
                    raise SourceError(str(e)) from e
                if not buf:
                    break
                write_with_timeout(fout, buf, WRITE_TIMEOUT)
                period = len(buf) / (rate_mb * 1024 * 1024)
                time.sleep(max(0.0, period))
    if tmp.stat().st_size != src.stat().st_size:
        raise DestChoke(f"size mismatch after copy: {src}")
    os.replace(tmp, dst)
    shutil.copystat(src, dst, follow_symlinks=False)


def main(src, dst, rate_mb):
    src, dst = Path(src), Path(dst)
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    total = sum(f.stat().st_size for f in files)
    print(f"{len(files)} files, {total/1e9:.2f} GB -> {dst} at <= {rate_mb} MB/s")
    print("NOTE: this rate is a starting point, not a proven-safe ceiling - "
          "large jobs can still choke; recovery is what makes this safe, not the cap.")
    done = 0
    skipped = 0
    resets = 0
    t_start = time.monotonic()
    i = 0
    attempt = 0
    while i < len(files):
        f = files[i]
        target = dst / (f.name if src.is_file() else f.relative_to(src))
        attempt += 1
        try:
            copy_file(f, target, rate_mb, attempt)
        except SourceError as e:
            print(f"  SOURCE-SKIP (not a bridge issue, skipping file): {f}: {e}", flush=True)
            skipped += 1
            i += 1
            continue
        except DestChoke as e:
            print(f"  CHOKE: {e}", flush=True)
            if resets < MAX_RESETS and software_reset():
                resets += 1
                rate_mb = max(5, rate_mb / 2)
                print(f"  continuing at {rate_mb:.0f} MB/s (reset {resets}/{MAX_RESETS} used)", flush=True)
                continue                                    # retry same file, fresh .partN
            print("AUTOMATIC RECOVERY EXHAUSTED.\nUNPLUG the SSD, wait 10s, replug, "
                  "then rerun this exact command - it resumes where it left off.", flush=True)
            sys.exit(2)
        done += f.stat().st_size
        i += 1
        if i % 200 == 0 or done == total:
            el = time.monotonic() - t_start
            print(f"  [{i}/{len(files)}] {done/1e9:.2f}/{total/1e9:.2f} GB "
                  f"({done/1e6/max(el,0.001):.0f} MB/s avg, {skipped} skipped)", flush=True)
    print(f"COPY COMPLETE ({skipped} files skipped - see SOURCE-SKIP lines above)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
    else:
        main(sys.argv[1], sys.argv[2],
             float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_RATE)
