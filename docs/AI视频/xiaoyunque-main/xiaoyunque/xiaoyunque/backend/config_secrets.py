"""Bounded, non-executable local secret storage; no dotenv dependency or logging."""
import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

SECRET_ENV = {
    'api_providers.openai.api_key': 'OPENAI_API_KEY',
    'api_providers.gemini.api_key': 'GEMINI_API_KEY',
    'api_providers.deepseek.api_key': 'DEEPSEEK_API_KEY',
    'api_providers.dashscope.api_key': 'DASHSCOPE_API_KEY',
    'api_providers.ark.api_key': 'ARK_API_KEY',
    'api_providers.kling.access_key': 'KLING_ACCESS_KEY',
    'api_providers.kling.secret_key': 'KLING_SECRET_KEY',
}
LIMIT = 128 * 1024


def secret_value(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError('Secret configuration is invalid.')
    return value


def read_bytes(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError('Secret configuration cannot be read.')
        data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError('Secret configuration cannot be read.')
        return data


def read_env(path: Path):
    raw = read_bytes(path)
    if raw is None:
        return [], {}
    try:
        lines = raw.decode('utf-8').splitlines(keepends=True)
    except UnicodeError:
        raise ValueError('Secret configuration cannot be read.') from None
    values = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', stripped)
        if match is None:
            # Fail closed for malformed known secrets; unrelated application lines stay unchanged.
            if any(re.match(r'(?:export\s+)?' + key + r'\b', stripped) for key in SECRET_ENV.values()):
                raise ValueError('Secret configuration cannot be read.')
            continue
        key, value = match.groups()
        if key not in SECRET_ENV.values():
            continue
        if key in values:
            raise ValueError('Duplicate secret configuration.')
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except Exception:
                raise ValueError('Secret configuration cannot be read.') from None
        elif value.startswith("'"):
            if len(value) < 2 or not value.endswith("'"):
                raise ValueError('Secret configuration cannot be read.')
            value = value[1:-1]
        else:
            value = value.split(' #', 1)[0].rstrip()
        values[key] = secret_value(value)
    return lines, values


@contextmanager
def secret_lock(directory: Path):
    fd = os.open(directory / '.env.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def overlay(values, path, get, put):
    _, local = read_env(path)
    for field, key in SECRET_ENV.items():
        if key in os.environ:
            put(values, field, secret_value(os.environ[key]))
        elif key in local:
            put(values, field, local[key])
    return values


def stage_bytes(path: Path, data: bytes):
    if path.is_symlink() or len(data) > LIMIT:
        raise ValueError('Configuration store is unavailable.')
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(name)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def save_pair(config_path, clean, get, put, dump):
    env_path = config_path.parent / '.env'
    with secret_lock(config_path.parent):
        original = read_bytes(env_path)
        lines, previous = read_env(env_path)
        retained = []
        for line in lines:
            match = re.match(r'\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
            if match is None or match[1] not in SECRET_ENV.values():
                retained.append(line)
        if retained and not retained[-1].endswith('\n'):
            retained[-1] += '\n'
        import copy
        public = copy.deepcopy(clean)
        for field, key in SECRET_ENV.items():
            value = secret_value(get(clean, field, ''))
            if key in os.environ:
                if value != secret_value(os.environ[key]):
                    raise ValueError('Environment-managed credentials cannot be changed here.')
                # Platform Secrets remain platform-managed, not copied into local files.
                if key in previous:
                    retained.append(key + '=' + json.dumps(previous[key]) + '\n')
            else:
                retained.append(key + '=' + json.dumps(value) + '\n')
            put(public, field, '')
        env_temp = stage_bytes(env_path, ''.join(retained).encode())
        config_temp = None
        changed = False
        try:
            config_temp = stage_bytes(config_path, dump(public).encode('utf-8'))
            os.replace(env_temp, env_path)
            changed = True
            os.replace(config_temp, config_path)
            fd = os.open(config_path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException:
            if changed:
                if original is None:
                    env_path.unlink(missing_ok=True)
                else:
                    rollback = stage_bytes(env_path, original)
                    try:
                        os.replace(rollback, env_path)
                    finally:
                        rollback.unlink(missing_ok=True)
            raise
        finally:
            env_temp.unlink(missing_ok=True)
            if config_temp:
                config_temp.unlink(missing_ok=True)
    return clean
