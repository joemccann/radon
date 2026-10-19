#!/usr/bin/env python3
"""Copy bounded raster leaves from staging into the edge's protected tree.

The installed entry point has fixed paths, takes no arguments or credentials,
and uses only the standard library. Tests call publish with isolated directories.
"""
from __future__ import annotations

import fcntl
import binascii
import grp
import json
import os
from pathlib import Path
import pwd
import re
import stat
import struct
import sys
import uuid

SOURCE = Path('/var/lib/radon/media')
ANCHOR = Path('/var/lib/radon-public')
MAX_BYTES = 80 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_FILES = 5000
MAX_ENTRIES = 20000
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,179}\.png', re.I)
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('An absolute directory without parent traversal is required')
    fd = os.open('/', DIR_FLAGS)
    try:
        for component in path.parts[1:]:
            following = os.open(component, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd
    except BaseException:
        os.close(fd)
        raise


def _signature(st):
    return [st.st_dev, st.st_ino, st.st_uid, st.st_gid, st.st_size,
            st.st_mtime_ns, st.st_ctime_ns, st.st_nlink, st.st_mode]


def _regular(st, uid):
    return stat.S_ISREG(st.st_mode) and st.st_uid == uid and st.st_nlink == 1


def _raster(name, data):
    """Check bounded canonical PNG structure, without decoding pixels as root."""
    if not name.lower().endswith('.png') or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return False
    offset = 8
    first = True
    image_data = False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], 'big')
        end = offset + 12 + length
        if end > len(data): return False
        kind = data[offset + 4:offset + 8]
        payload = memoryview(data)[offset + 8:end - 4]
        if not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in kind): return False
        if binascii.crc32(memoryview(data)[offset + 4:end - 4]) != int.from_bytes(data[end - 4:end], 'big'):
            return False
        if first:
            if kind != b'IHDR' or length != 13: return False
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', payload)
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if (not width or not height or width * height > MAX_PIXELS
                    or depth not in depths.get(color, set()) or compression or filtering
                    or interlace not in {0, 1}):
                return False
            first = False
        elif kind == b'IHDR': return False
        elif kind == b'IDAT': image_data = True
        elif kind == b'IEND': return length == 0 and image_data and end == len(data)
        elif kind[0] < 97 and kind != b'PLTE': return False
        offset = end
    return False


def _read(fd, limit):
    chunks = []
    size = 0
    while True:
        chunk = os.read(fd, min(1024 * 1024, limit + 1 - size))
        if not chunk: break
        chunks.append(chunk)
        size += len(chunk)
        if size > limit: raise ValueError('Media exceeds publication size bound')
    return b''.join(chunks)


def _stat(fd, name):
    try: return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError: return None


def _public_file(fd, name, uid, gid=None):
    st = _stat(fd, name)
    if st is not None and (not _regular(st, uid) or st.st_mode & 0o022 or (gid is not None and st.st_gid != gid)):
        raise ValueError('Public leaf has untrusted ownership, mode, or type')
    return st


def _write(fd, name, data, uid, gid, mtime_ns=None):
    temporary = '.pending-' + uuid.uuid4().hex
    out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                  0o600, dir_fd=fd)
    try:
        with os.fdopen(out, 'wb', closefd=False) as handle:
            handle.write(data)
            handle.flush()
        os.fchown(out, uid, gid)
        os.fchmod(out, 0o640)
        if mtime_ns is not None: os.utime(out, ns=(mtime_ns, mtime_ns))
        os.fsync(out)
        os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(out)
        try: os.unlink(temporary, dir_fd=fd)
        except FileNotFoundError: pass


def publish(source, destination, *, source_uid, public_uid, public_gid,
            max_bytes=MAX_BYTES, max_files=MAX_FILES, max_entries=MAX_ENTRIES,
            cache=None, max_total_bytes=256 * 1024 * 1024):
    """Publish a fixed bounded snapshot; retain valid bytes on rejected updates."""
    if min(max_bytes, max_files, max_entries, max_total_bytes) <= 0:
        raise ValueError('Publication bounds must be positive')
    source_fd = _directory(source)
    try:
        dest_fd = _directory(destination)
        try:
            dest_st = os.fstat(dest_fd)
            if dest_st.st_uid != public_uid or dest_st.st_mode & 0o022:
                raise ValueError('Public directory is writable outside its owner')
            source_st = os.fstat(source_fd)
            # Enumerate completely before any copies or removals. No recursive walk.
            names = []
            with os.scandir(source_fd) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) > max_entries:
                        raise ValueError('Staging enumeration exceeds publication bound')
            candidates = []
            rejected = 0
            for name in names:
                if name.startswith('.') or name.lower().endswith(('.tmp', '.part', '.download')):
                    continue
                info = _stat(source_fd, name)
                if not NAME.fullmatch(name) or info is None or not _regular(info, source_uid):
                    rejected += 1
                    continue
                candidates.append((info.st_mtime_ns, name, info))
            candidates.sort(reverse=True)
            if len(candidates) > max_files:
                raise ValueError("Supported source leaf count exceeds publication bound")
            selected = candidates
            keep = set(names)  # Invalid replacements must not delete last valid bytes.
            copied = unchanged = 0
            previous = cache or {}
            following = {name: record for name, record in previous.items() if name in keep}
            reserved_bytes = 0
            deferred = 0
            for position, (_, name, initial) in enumerate(selected):
                published = _public_file(dest_fd, name, public_uid, public_gid)
                before = _signature(initial)
                if initial.st_size > max_bytes:
                    rejected += 1
                    continue
                record = previous.get(name)
                if (published is not None and record == [before, _signature(published)]):
                    following[name] = record
                    unchanged += 1
                    continue
                # Reserve the entire bounded read attempt, including the one-byte
                # growth sentinel and possible destination comparison. Failures,
                # races and rejected content consume this budget too.
                read_budget = initial.st_size + 1
                if published is not None and published.st_size == initial.st_size:
                    read_budget += published.st_size + 1
                if reserved_bytes + read_budget > max_total_bytes:
                    deferred = len(selected) - position
                    break
                reserved_bytes += read_budget
                read_fd = None
                try:
                    read_fd = os.open(name, FILE_FLAGS, dir_fd=source_fd)
                    st = os.fstat(read_fd)
                    if not _regular(st, source_uid) or _signature(st) != before or st.st_size > max_bytes:
                        raise ValueError('Staging leaf changed or exceeds publication bounds')
                    data = _read(read_fd, initial.st_size)
                    named = _stat(source_fd, name)
                    if (_signature(os.fstat(read_fd)) != before or named is None
                            or _signature(named) != before or len(data) != st.st_size
                            or not _raster(name, data)):
                        raise ValueError('Staging leaf changed or is not an approved raster')
                    same = False
                    if published is not None and published.st_size == len(data):
                        old_fd = os.open(name, FILE_FLAGS, dir_fd=dest_fd)
                        try: same = _read(old_fd, published.st_size) == data
                        finally: os.close(old_fd)
                    if not same or published.st_mtime_ns != st.st_mtime_ns:
                        current_fd = _directory(source)
                        try:
                            current = os.fstat(current_fd)
                            if (current.st_dev, current.st_ino) != (source_st.st_dev, source_st.st_ino):
                                raise ValueError('Staging directory changed during publication')
                        finally: os.close(current_fd)
                        _write(dest_fd, name, data, public_uid, public_gid, st.st_mtime_ns)
                        copied += 1
                    else: unchanged += 1
                    following[name] = [before, _signature(_public_file(dest_fd, name, public_uid, public_gid))]
                except (OSError, ValueError):
                    rejected += 1
                finally:
                    if read_fd is not None: os.close(read_fd)
            # Refuse removals when staging has been replaced or renamed mid-scan.
            current_fd = _directory(source)
            try:
                current = os.fstat(current_fd)
                if (current.st_dev, current.st_ino) != (source_st.st_dev, source_st.st_ino):
                    raise ValueError('Staging directory changed during publication')
            finally: os.close(current_fd)
            removed = 0
            with os.scandir(dest_fd) as entries:
                public_names = [entry.name for entry in entries]
            if len(public_names) > max_entries:
                raise ValueError('Public enumeration exceeds publication bound')
            for name in public_names:
                if not NAME.fullmatch(name): continue
                _public_file(dest_fd, name, public_uid, public_gid)
                if name not in keep:
                    os.unlink(name, dir_fd=dest_fd)
                    following.pop(name, None)
                    removed += 1
            if removed: os.fsync(dest_fd)
            if cache is not None:
                cache.clear()
                cache.update(following)
            return {'copied': copied, 'unchanged': unchanged, 'rejected': rejected, 'removed': removed, 'deferred': deferred}
        finally: os.close(dest_fd)
    finally: os.close(source_fd)


def _prepare_public(gid):
    parent = _directory(ANCHOR.parent)
    try:
        st = os.fstat(parent)
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('Public parent is not root controlled')
        try: os.mkdir(ANCHOR.name, 0o755, dir_fd=parent)
        except FileExistsError: pass
        anchor = os.open(ANCHOR.name, DIR_FLAGS, dir_fd=parent)
    finally: os.close(parent)
    try:
        st = os.fstat(anchor)
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('Public anchor is not root controlled')
        try: os.mkdir('media', 0o750, dir_fd=anchor)
        except FileExistsError: pass
        media = os.open('media', DIR_FLAGS, dir_fd=anchor)
        try:
            st = os.fstat(media)
            if st.st_uid != 0 or st.st_mode & 0o022:
                raise ValueError('Public media is not root controlled')
            os.fchown(media, 0, gid)
            os.fchmod(media, 0o750)
        finally: os.close(media)
        return anchor
    except BaseException:
        os.close(anchor)
        raise


def main():
    if len(sys.argv) != 1 or os.geteuid() != 0:
        print('Media publisher requires its fixed root-owned service entry point', file=sys.stderr)
        return 64
    anchor = None
    lock = None
    try:
        gid = grp.getgrnam('caddy').gr_gid
        uid = pwd.getpwnam('radon').pw_uid
        anchor = _prepare_public(gid)
        lock = os.open('publish.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                       0o600, dir_fd=anchor)
        lock_stat = os.fstat(lock)
        if not _regular(lock_stat, 0) or lock_stat.st_mode & 0o077:
            raise ValueError('Untrusted publisher lock')
        fcntl.flock(lock, fcntl.LOCK_EX)
        cache = {}
        info = _public_file(anchor, 'index.json', 0, 0)
        if info is not None:
            index = os.open('index.json', FILE_FLAGS, dir_fd=anchor)
            try: cache = json.loads(_read(index, 8 * 1024 * 1024))
            finally: os.close(index)
            if not isinstance(cache, dict): raise ValueError('Untrusted publisher index')
        result = publish(SOURCE, ANCHOR / 'media', source_uid=uid, public_uid=0,
                         public_gid=gid, cache=cache)
        _write(anchor, 'index.json', json.dumps(cache, separators=(',', ':')).encode(), 0, 0)
        print(json.dumps(result, sort_keys=True))
        if result['rejected']: return 1
        return 75 if result['deferred'] else 0
    except (OSError, ValueError, KeyError):
        print('Media publication failed; prior published assets are retained', file=sys.stderr)
        return 1
    finally:
        if lock is not None: os.close(lock)
        if anchor is not None: os.close(anchor)


if __name__ == '__main__':
    raise SystemExit(main())
