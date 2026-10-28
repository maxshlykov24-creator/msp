#!/usr/bin/python3
"""Дорожный режим. start / stop / status / watch. No root Python execution."""
import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
import time

BASE = Path.home() / '.local/share/road-mode'
STATE = BASE / 'state.json'

def install():
    """Install local entry point and watcher; grant only four literal pmset calls."""
    if sys.platform != 'darwin' or os.getuid() == 0:
        raise RuntimeError('Установка выполняется обычным пользователем на Mac')
    closed()  # Verify that this really is a MacBook with a readable lid sensor.
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    grant = Path('/etc/sudoers.d/msp-road-mode')
    if not grant.exists():
        username = pwd.getpwuid(os.getuid()).pw_name
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', username):
            raise RuntimeError('Неподдерживаемое имя пользователя для sudoers')
        fd, source = tempfile.mkstemp(prefix='msp-road-mode-', suffix='.sudoers')
        with os.fdopen(fd, 'w') as f:
            for key in ['disablesleep', 'lowpowermode']:
                for value in [0, 1]:
                    f.write(f'{username} ALL=(root) NOPASSWD: /usr/bin/pmset -a {key} {value}\n')
        run(['/usr/sbin/visudo', '-cf', source])
        cmd = ('/usr/sbin/visudo -cf ' + shlex.quote(source)
               + ' && if /bin/test -e /etc/sudoers.d/msp-road-mode; then '
               + '/usr/sbin/visudo -cf /etc/sudoers.d/msp-road-mode; else '
               + '/usr/bin/install -o root -g wheel -m 0440 ' + shlex.quote(source)
               + ' /etc/sudoers.d/msp-road-mode; fi')
        script = ('do shell script ' + json.dumps(cmd)
                  + ' with administrator privileges with prompt "Установить дорожный режим и возврат настроек при открытии крышки"')
        # Human authentication may take longer than the normal command timeout.
        subprocess.run(['/usr/bin/osascript', '-e', script], check=True)
    if not grant.is_file() or grant.stat().st_uid != 0:
        raise RuntimeError('Системное разрешение не установлено')
    source = Path(__file__).resolve()
    wrapper = Path.home() / '.local/bin/road-mode'
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/python3 ' + shlex.quote(str(source)) + ' "$@"\n')
    wrapper.chmod(0o755)
    agent = Path.home() / 'Library/LaunchAgents/com.msp.road-mode.plist'
    agent.parent.mkdir(parents=True, exist_ok=True)
    domain = 'gui/' + str(os.getuid())
    subprocess.run(['/bin/launchctl', 'bootout', domain + '/com.msp.road-mode'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    agent.write_bytes(plistlib.dumps({
        'Label': 'com.msp.road-mode',
        'ProgramArguments': ['/usr/bin/python3', str(source), 'watch'],
        'RunAtLoad': True, 'KeepAlive': True, 'ProcessType': 'Background',
        'StandardOutPath': str(BASE / 'watcher.log'),
        'StandardErrorPath': str(BASE / 'watcher.log'),
    }))
    run(['/bin/launchctl', 'bootstrap', domain, str(agent)])
    print('Дорожный режим установлен. Включение: road-mode start')

def run(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=25).strip()

def log(message):
    with (BASE / 'events.log').open('a') as f:
        f.write(datetime.datetime.now().astimezone().isoformat() + ' ' + message + '\n')

@contextlib.contextmanager
def locked():
    with (BASE / 'lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield

def read():
    return json.loads(STATE.read_text()) if STATE.exists() else {'active': False}

def save(state):
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)

def closed():
    value = run(['/usr/sbin/ioreg', '-r', '-k', 'AppleClamshellState', '-d', '4'])
    match = re.search(r'"AppleClamshellState" = (Yes|No)', value)
    if not match:
        raise RuntimeError('Не удалось прочитать датчик крышки')
    return match[1] == 'Yes'

def should_restore(state, lid_closed):
    return state.get('active', False) and state.get('seen_closed', False) and not lid_closed

def power(key, value):
    run(['/usr/bin/sudo', '-n', '/usr/bin/pmset', '-a', key, str(value)])

def set_muted(muted):
    value = 'true' if muted else 'false'
    for _ in range(3):
        run(['/usr/bin/osascript', '-e', 'set volume output muted ' + value])
        time.sleep(0.5)
        settings = run(['/usr/bin/osascript', '-e', 'get volume settings'])
        if 'outputmuted:' + value in settings.replace(' ', ''):
            return
    raise RuntimeError('Не удалось подтвердить состояние звука: ' + settings)

def verify(expected):
    settings = run(['/usr/bin/pmset', '-g', 'custom'])
    modes = re.findall(r'lowpowermode\s+(\d+)', settings)
    if len(modes) != 2 or any(int(n) != expected for n in modes):
        raise RuntimeError('Энергосбережжение не соответствует режиму: ' + settings)
    registry = run(['/usr/sbin/ioreg', '-r', '-k', 'AppleClamshellState', '-d', '4'])
    if '"SleepDisabled" = ' + ('Yes' if expected else 'No') not in registry:
        raise RuntimeError('Запрет сна не соответствует режиму')

def restore(state, reason):
    # Persist active until ALL restoration operations succeed; watcher retries errors.
    power('lowpowermode', 0)
    power('disablesleep', 0)
    # Wake display through a short user-activity assertion; do not alter brightness.
    run(['/usr/bin/caffeinate', '-u', '-t', '1'])
    # Apply to the output device after waking; a wake can change the audio route.
    set_muted(False)
    verify(0)
    state.update(active=False, restored_at=datetime.datetime.now().astimezone().isoformat(), reason=reason)
    save(state)
    log('RESTORED ' + reason)

def start():
    with locked():
        state = read()
        if state.get('active'):
            print('Дорожный режим уже включён. Повторное включение не меняет состояние.')
            return
        # Check exact, limited admin grants BEFORE changing apps or settings.
        grant = Path('/etc/sudoers.d/msp-road-mode')
        if not grant.is_file() or grant.stat().st_uid != 0:
            raise RuntimeError('Сначала нужно подтвердить системное окно установки режима в дорогу')
        for key in ['disablesleep', 'lowpowermode']:
            for value in ['0', '1']:
                run(['/usr/bin/sudo', '-n', '-l', '/usr/bin/pmset', '-a', key, value])
        lid_closed = closed()
        # Conditional quit avoids launching Telegram if it is already closed.
        run(['/usr/bin/osascript', '-e', 'if application id "ru.keepcoder.Telegram" is running then tell application id "ru.keepcoder.Telegram" to quit'])
        for _ in range(20):
            if subprocess.run(['/usr/bin/pgrep', '-x', 'Telegram'], stdout=subprocess.DEVNULL).returncode != 0:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Telegram не завершился. Режим не включён.')
        state = {'active': True, 'seen_closed': lid_closed, 'started_at': datetime.datetime.now().astimezone().isoformat()}
        save(state)
        try:
            # Low Power Mode may update power policy: set disablesleep AFTER it.
            power('lowpowermode', 1)
            power('disablesleep', 1)
            set_muted(True)
            verify(1)
            run(['/usr/bin/pmset', 'displaysleepnow'])
            set_muted(True)
            log('START lid_closed=' + str(lid_closed))
        except Exception:
            restore(state, 'start failed')
            raise
        print('Дорожный режим включён. После закрытия и открытия крышки настройки восстановятся автоматически.')

def watch():
    log('WATCHER started')
    while True:
        try:
            with locked():
                state = read()
                if state.get('active'):
                    lid_closed = closed()
                    if should_restore(state, lid_closed):
                        restore(state, 'lid opened')
                    elif lid_closed and not state.get('seen_closed'):
                        state['seen_closed'] = True
                        save(state)
                        log('LID closed')
        except Exception as exc:
            log('ERROR ' + str(exc))
        time.sleep(2)

def main():
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    action = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if action == 'install':
        install()
    elif action == 'watch':
        watch()
    elif action == 'start':
        # Fail before settings change if automatic restoration is not running.
        run(['/bin/launchctl', 'print', 'gui/' + str(os.getuid()) + '/com.msp.road-mode'])
        start()
    elif action == 'stop':
        with locked():
            restore(read(), 'manual stop')
        print('Обычные настройки возвращены. Telegram не запускался.')
    elif action == 'status':
        with locked():
            print(json.dumps(read(), ensure_ascii=False, indent=2))
        print(run(['/usr/bin/pmset', '-g']))
        print(run(['/usr/bin/osascript', '-e', 'get volume settings']))
        print('Крышка закрыта:', closed())
    else:
        raise SystemExit('Использование: road-mode [install|start|stop|status]')

if __name__ == '__main__':
    main()
