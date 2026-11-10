#!/usr/bin/env python3
"""Shared vault sync and Git size checks. Python 3 stdlib; macOS/POSIX.

Exit codes: 0 complete, 1 failed, 2 deferred/paused/partial.
UI hooks deliberately translate these to {} without claiming delivery.
"""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

LIMIT = 95 * 1024 * 1024
WARN = 50 * 1024 * 1024
REL = Path('2. БИЗНЕС/08_Системное/Скрипты_vault/git')
ROOT = Path.cwd()
LOG = ROOT / REL / 'logs'
LOCK_FD = None

class Failure(Exception):
    pass

class Deferred(Exception):
    pass

def git(*args, data=None, network=False, allowed=(0,)):
    env = dict(os.environ, GIT_SKIP_AUTO_PUSH='1', GIT_TERMINAL_PROMPT='0')
    env.setdefault('GIT_SSH_COMMAND', 'ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3')
    if LOCK_FD is not None:
        env['VAULT_SYNC_OWNER'] = str(os.getpid())
    p = subprocess.Popen(['git', *args], stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                         start_new_session=network,
                         pass_fds=(() if LOCK_FD is None else (LOCK_FD,)))
    try:
        out, err = p.communicate(data, timeout=300 if network else None)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
        raise Failure('network timeout: git ' + args[0])
    if p.returncode not in allowed:
        # Never copy arbitrary remote output, credentials or commit bodies into logs.
        raise Failure('git ' + args[0] + ' failed, exit=' + str(p.returncode))
    return out

def stamp(name):
    (LOG / name).write_text(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '\n')

def log(message, alert=False):
    LOG.mkdir(parents=True, exist_ok=True)
    with (LOG / 'vault-sync.log').open('a') as f:
        f.write(datetime.datetime.now().isoformat(timespec='seconds') + ' ' + message + '\n')
    print(message, file=sys.stderr)
    if alert:
        digest = hashlib.sha256(message.encode()).hexdigest()
        state = LOG / ('.notice-' + digest)
        if state.exists() and state.read_text() == digest and time.time() - state.stat().st_mtime < 21600:
            return
        state.write_text(digest)
        script = 'on run argv\ndisplay notification (item 1 of argv) with title "Vault sync"\nend run'
        try:
            subprocess.run(['osascript', '-e', script, message], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

def pending():
    LOG.mkdir(parents=True, exist_ok=True)
    (LOG / '.sync-pending').touch()

def paused():
    if (ROOT / '.vault-sync-off').exists():
        raise Deferred('paused: .vault-sync-off')

@contextlib.contextmanager
def lock():
    global LOCK_FD
    LOG.mkdir(parents=True, exist_ok=True)
    # A legacy writer cannot participate in flock: never remove its lock by age.
    if (LOG / '.sync.lock').exists():
        raise Deferred('locked: legacy sync; inspect owner before recovery')
    with (LOG / '.sync.flock').open('a+') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Deferred('locked: another sync is running')
        LOCK_FD = f.fileno()
        f.seek(0); f.truncate(); f.write(str(os.getpid()) + '\n'); f.flush()
        try:
            yield
        finally:
            LOCK_FD = None
            # Closing releases the kernel lock, including after a crash. Never unlink it.

def entries():
    result = []
    for record in git('ls-files', '--stage', '-z').split(b'\0'):
        if not record:
            continue
        meta, path = record.split(b'\t', 1)
        mode, oid, stage = meta.split()
        if stage != b'0':
            raise Failure('unmerged index')
        if mode != b'160000':
            result.append((path, oid))
    return result

def sizes(oids):
    oids = list(dict.fromkeys(oids))
    if not oids:
        return {}
    lines = git('cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)',
                data=b'\n'.join(oids) + b'\n').splitlines()
    if len(lines) != len(oids):
        raise Failure('incomplete object size check')
    result = {}
    for oid, line in zip(oids, lines):
        parts = line.split()
        if len(parts) != 3 or parts[0] != oid or parts[1] not in (b'blob', b'tree', b'commit', b'tag'):
            raise Failure('cannot verify Git object')
        result[oid] = (parts[1], int(parts[2]))
    return result

def index_check(prune=False):
    items = entries(); stats = sizes([oid for _, oid in items]); blocked = []
    for path, oid in items:
        kind, size = stats[oid]
        if kind != b'blob':
            raise Failure('unexpected index object type')
        if size > LIMIT:
            blocked.append(path)
            log('excluded oversized blob: ' + repr(os.fsdecode(path)) + ' bytes=' + str(size), alert=True)
        elif size > WARN:
            log('warning large blob: ' + repr(os.fsdecode(path)) + ' bytes=' + str(size))
    if blocked and not prune:
        raise Failure('oversized staged blobs; commit blocked')
    if blocked:
        # Restore only index entries, never the working files. NUL paths handle all filenames.
        git('restore', '--staged', '--source=HEAD', '--pathspec-from-file=-', '--pathspec-file-nul',
            data=b'\0'.join(blocked) + b'\0')
        (LOG / 'excluded-files.json').write_text(json.dumps([os.fsdecode(x) for x in blocked], ensure_ascii=True))
        pending()
    elif prune and (LOG / 'excluded-files.json').exists():
        (LOG / 'excluded-files.json').unlink()
    return bool(blocked)

def history_check(refs):
    # Each ref is checked against its actual advertised remote OID. For a new ref
    # or an unknown remote OID, check all reachable objects (conservative).
    ids = set()
    for local, remote in refs:
        if set(local) == {'0'}:
            continue
        args = ['rev-list', '--objects', '--no-object-names', local]
        if set(remote) != {'0'}:
            try:
                git('cat-file', '-e', remote)
                args += ['^' + remote]
            except Failure:
                pass
        ids.update(git(*args).splitlines())
    stats = sizes(sorted(ids))
    bad = [(oid, size) for oid, (kind, size) in stats.items() if kind == b'blob' and size > LIMIT]
    for oid, size in bad:
        log('push blocked: historical blob ' + oid.decode() + ' bytes=' + str(size), alert=True)
    if bad:
        raise Failure('oversized objects in outgoing history')

def dirty():
    return bool(git('status', '--porcelain', '-z'))

def state_check():
    gd = Path(os.fsdecode(git('rev-parse', '--git-dir')).strip())
    for marker in ['rebase-merge', 'rebase-apply', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'index.lock']:
        if (gd / marker).exists():
            raise Deferred('unfinished Git operation: ' + marker)
    if git('symbolic-ref', '--short', 'HEAD').strip() != b'main':
        raise Deferred('only main is synchronized')

def ancestor(a, b):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', a, b],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def save_changes():
    if not dirty():
        return False
    git('add', '-A')
    partial = index_check(prune=True)
    if git('diff', '--cached', '--name-only', '-z'):
        git('commit', '-m', 'vault sync ' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
        log('commit: ' + git('rev-parse', '--short', 'HEAD').decode().strip())
        pending()
    return partial

def sync(mode):
    paused()
    with lock():
        paused(); state_check()
        heartbeat = LOG / 'last-success'
        if heartbeat.exists() and time.time() - heartbeat.stat().st_mtime > 86400:
            log('warning: no fully synchronized state for over a day', alert=True)
        # Keep debounce for unattended timer, but only inspect Git-visible files.
        if mode == 'auto' and os.environ.get('VAULT_SYNC_NOW') != '1' and not (LOG / '.sync-pending').exists() and dirty():
            names = git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')
            recent = any((ROOT / os.fsdecode(n)).exists() and (ROOT / os.fsdecode(n)).stat().st_mtime > time.time()-90 for n in names if n)
            d = LOG / '.debounce-since'
            if recent:
                if not d.exists(): d.write_text(str(time.time()))
                if time.time()-float(d.read_text()) < 1800:
                    raise Deferred('debounce: files are still changing')
            if d.exists(): d.unlink()
        partial = False
        if mode in ('auto', 'stop', 'snapshot'):
            partial = save_changes()
        if mode == 'snapshot':
            raise Deferred('snapshot: saved locally; push pending')
        if mode == 'stop':
            last = LOG / '.last-event-sync'
            if last.exists() and time.time()-float(last.read_text()) < 300:
                raise Deferred('stop: saved locally; push throttled')
        git('fetch', 'origin', network=True)
        remote = git('rev-parse', 'origin/main').decode().strip()
        head = git('rev-parse', 'HEAD').decode().strip()
        if not ancestor(remote, head):
            if dirty():
                raise Deferred('integration required: working tree dirty; no autostash')
            try:
                if ancestor(head, remote):
                    git('merge', '--ff-only', remote)
                else:
                    git('rebase', '--rebase-merges', remote)
            except Failure:
                git('rebase', '--abort', allowed=(0, 128, 1))
                (ROOT / '.vault-sync-conflict').touch()
                raise Failure('integration failed; changes preserved')
            head = git('rev-parse', 'HEAD').decode().strip()
            log('integrated: remote main')
        if mode in ('pull', 'wake'):
            log('pull: checked remote main')
            return
        paused()
        sent = head != remote
        if sent:
            history_check([(head, remote)])
            git('push', 'origin', 'main', network=True)
        advertised = git('ls-remote', 'origin', 'refs/heads/main', network=True).split()
        if len(advertised) != 2 or advertised[0].decode() != head or git('rev-parse','HEAD').decode().strip() != head:
            raise Failure('remote/local SHA changed; verification incomplete')
        if sent:
            stamp('last-push'); log('push verified: ' + head)
            (LOG / '.last-event-sync').write_text(str(time.time()))
        if partial or dirty():
            raise Deferred('partial: committed changes delivered; excluded/local files remain unsaved')
        stamp('last-success')
        for p in [LOG / '.sync-pending', ROOT / '.vault-sync-conflict']:
            if p.exists(): p.unlink()
        log('synchronized: ' + head + (' (push)' if sent else ' (no changes)'))

def main():
    global ROOT, LOG
    ROOT = Path(os.fsdecode(git('rev-parse', '--show-toplevel')).strip()); os.chdir(ROOT)
    LOG = ROOT / REL / 'logs'
    mode = sys.argv[1]
    if mode == 'pre-push':
        refs = []
        for line in sys.stdin:
            parts = line.split()
            if len(parts) != 4: raise Failure('invalid pre-push input')
            refs.append((parts[1], parts[3]))
        history_check(refs)
    elif mode == 'pre-commit':
        # Parent sync owns the lock through commit. Other manual commits check it.
        owner = os.environ.get('VAULT_SYNC_OWNER')
        parents = set(); pid = os.getppid()
        while pid > 1:
            parents.add(str(pid))
            out = subprocess.check_output(['ps', '-p', str(pid), '-o', 'ppid=']).strip()
            if not out: break
            pid = int(out)
        if owner in parents:
            index_check()
        else:
            with lock(): index_check()
    else:
        sync(mode)

if __name__ == '__main__':
    try:
        main()
    except Deferred as e:
        pending(); log(str(e), alert='partial:' in str(e) or 'integration required' in str(e)); sys.exit(2)
    except (Failure, OSError, ValueError, subprocess.SubprocessError) as e:
        pending(); log('error: ' + str(e), alert=True); sys.exit(1)
