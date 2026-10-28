"""Watch the local source; debounce changes and sync only this paper repository."""
from __future__ import annotations
import argparse
import hashlib
import json
import logging
import logging.handlers
import os
from pathlib import Path
import sys
import time
import traceback

from sync_library import REPO, load_json, now, publish, write_changed, dump_json


def source_signature(config):
    source = Path(config['source_dir'])
    work = Path(config['work_dir'])
    files = list(source.rglob('*'))
    files += [work / name for name in ('library.json', 'verified_metadata.json', 'downloads.sqlite', 'downloads.sqlite-wal')]
    data = []
    for p in files:
        if p.is_file() and not p.name.startswith('~$') and not p.name.endswith(('.tmp','.crdownload')):
            s = p.stat()
            data.append((str(p),s.st_size,s.st_mtime_ns))
    return hashlib.sha256(json.dumps(sorted(data),ensure_ascii=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=REPO / '.local' / 'config.json')
    args = parser.parse_args()
    config = load_json(args.config)
    if not config:
        raise SystemExit('Missing configuration')
    local = REPO / '.local'
    local.mkdir(exist_ok=True)
    # The OS releases this lock even if a previous watcher was terminated.
    lock = (local / 'watch.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0');lock.flush()
    lock.seek(0)
    if os.name == 'nt':
        import msvcrt
        try:
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise SystemExit('Watcher already running')
    else:
        import fcntl
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise SystemExit('Watcher already running')
    handler = logging.handlers.RotatingFileHandler(local/'watch.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
    logging.basicConfig(handlers=[handler],level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('pypdf').setLevel(logging.ERROR)
    write_changed(local/'watch.pid',str(os.getpid()))
    last_success = load_json(local/'sync_state.json',{}).get('signature')
    observed = None
    changed_at = time.monotonic()
    next_attempt = 0.0
    interval = config.get('poll_seconds',5)
    debounce = config.get('debounce_seconds',10)
    retry = config.get('retry_seconds',120)
    logging.info('Watcher started; source=%s',config['source_dir'])
    while True:
        try:
            signature = source_signature(config)
            if signature != observed:
                observed = signature
                changed_at = time.monotonic()
                logging.info('Source change detected; waiting for stable files')
            ready = time.monotonic()-changed_at >= debounce
            if ready and signature != last_success and time.monotonic() >= next_attempt:
                write_changed(local/'runtime_status.json',dump_json({'status':'syncing','time':now(),'pid':os.getpid()}))
                result = publish(config)
                next_attempt = 0.0
                last_success = signature
                state = {'status':'synced','time':now(),'signature':signature,'pid':os.getpid(),**result}
                write_changed(local/'sync_state.json',dump_json(state))
                write_changed(local/'runtime_status.json',dump_json(state))
                logging.info('Remote verified at %s; public PDFs=%s',result.get('verified_commit'),result['public_pdfs'])
            elif signature == last_success:
                # A heartbeat is local only; it never creates periodic Git commits.
                prior = load_json(local/'runtime_status.json',{})
                prior.update(status='watching',heartbeat=now(),pid=os.getpid())
                write_changed(local/'runtime_status.json',dump_json(prior))
        except Exception as error:
            next_attempt = time.monotonic() + retry
            logging.error('Sync not completed: %s',error)
            write_changed(local/'runtime_status.json',dump_json({'status':'error','time':now(),'pid':os.getpid(),'message':str(error)[:1500]}))
        time.sleep(interval)


if __name__ == '__main__':
    main()
