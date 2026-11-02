"""One bounded private-pipe UIA operation. Never launched automatically.

Runs in a kill-on-close, two-process-bounded Windows job (frozen bootloader plus
interpreter) supervised by the backend.
No coordinate clicks, global keyboard injection, shell, files or network APIs.
UIA providers and already accepted external effects remain outside the job.
"""

import ctypes
import hashlib
import json
import os
import re
import sys
from collections import deque
from ctypes import wintypes

INPUT_LIMIT = 8192
OUTPUT_LIMIT = 60000
NODE_LIMIT = 80
MAX_WINDOWS = 3


def _libraries():
    if os.name != 'nt':
        raise ValueError('UIA workers require Windows.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.GetLengthSid.argtypes = [ctypes.c_void_p]
    security.GetLengthSid.restype = wintypes.DWORD
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    return kernel, security, user


def process_identity(pid):
    kernel, security, _ = _libraries()
    process = kernel.OpenProcess(0x1000, False, pid)
    if not process:
        raise ValueError('Target process identity is unavailable.')
    token = wintypes.HANDLE()
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(process, *(ctypes.byref(value) for value in times)):
            raise ValueError('Cannot verify target process creation time.')
        session = wintypes.DWORD()
        if not kernel.ProcessIdToSessionId(pid, ctypes.byref(session)) or not security.OpenProcessToken(process, 8, ctypes.byref(token)):
            raise ValueError('Cannot inspect target process session/token.')
        size = wintypes.DWORD()
        elevated = wintypes.DWORD()
        if not security.GetTokenInformation(token, 20, ctypes.byref(elevated), ctypes.sizeof(elevated), ctypes.byref(size)) or elevated.value:
            raise ValueError('Elevated or unverifiable target processes are unsupported.')
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if not 1 <= size.value <= 65536:
            raise ValueError('Invalid target token size.')
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise ValueError('Cannot verify target token owner.')
        class TokenUser(ctypes.Structure):
            _fields_ = [('sid', ctypes.c_void_p), ('attributes', wintypes.DWORD)]
        sid = TokenUser.from_buffer(buffer).sid
        length = security.GetLengthSid(sid)
        if not 1 <= length <= 1024:
            raise ValueError('Invalid target token owner.')
        return {'pid': pid, 'created': (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
                'session': session.value, 'owner_sha256': hashlib.sha256(ctypes.string_at(sid, length)).hexdigest()}
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def _title(user, handle):
    if user.GetWindowTextLengthW(handle) > 200:
        raise ValueError('Window title exceeds the supported bound.')
    text = ctypes.create_unicode_buffer(201)
    user.GetWindowTextW(handle, text, len(text))
    return text.value


def verify_window(expected, own_identity=None):
    _, _, user = _libraries()
    handle = expected['hwnd']
    if type(handle) is not int or not 0 < handle < 2 ** 64 or not user.IsWindow(handle) or not user.IsWindowVisible(handle):
        raise ValueError('Selected window is no longer visible.')
    if user.GetAncestor(handle, 2) != handle or _title(user, handle) != expected['title']:
        raise ValueError('Selected window identity or title changed.')
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    identity = process_identity(pid.value)
    own = own_identity or process_identity(os.getpid())
    if identity['owner_sha256'] != own['owner_sha256'] or identity['session'] != own['session']:
        raise ValueError('Cross-user or cross-session windows are unsupported.')
    if expected.get('process') is not None and expected['process'] != identity:
        raise ValueError('Target process was replaced; select a fresh session.')
    return {'hwnd': handle, 'title': expected['title'], 'process': identity}


def attach(titles):
    _, _, user = _libraries()
    found = {title: [] for title in titles}
    scanned, failed = 0, False
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enumerate_window(handle, _):
        nonlocal scanned, failed
        try:
            scanned += 1
            if scanned > 2048:
                failed = True
                return False
            if user.IsWindowVisible(handle) and user.GetWindowTextLengthW(handle) <= 200:
                title = _title(user, handle)
                if title in found:
                    found[title].append(int(handle))
            return True
        except Exception:
            failed = True
            return False
    callback = callback_type(enumerate_window)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    if not user.EnumWindows(callback, 0) or failed or any(len(handles) != 1 for handles in found.values()):
        raise ValueError('Selected windows are absent, ambiguous or exceed enumeration limits.')
    own = process_identity(os.getpid())
    return [verify_window({'hwnd': handles[0], 'title': title}, own) for title, handles in found.items()]


def control_identity(control, expected):
    info = control.element_info
    if info.process_id != expected['process']['pid'] or int(control.top_level_parent().handle) != expected['hwnd']:
        raise ValueError('Control is outside the exact selected window/process.')
    if not control.is_visible() or not control.is_enabled() or info.element.CurrentIsPassword:
        raise ValueError('Hidden, disabled or password controls are excluded.')
    runtime = list(info.runtime_id)
    if not 1 <= len(runtime) <= 32 or any(type(item) is not int for item in runtime):
        raise ValueError('Control runtime identity is unsupported.')
    kind = str(info.control_type)
    # Avoid extracting editable/document values; display only bounded labels.
    name = str(info.name)[:120] if kind not in {'Edit', 'Document'} else ''
    rectangle = control.rectangle()
    identity = {'runtime': runtime, 'type': kind, 'name': name,
                'automation_id': str(info.automation_id)[:120],
                'bounds': [rectangle.left, rectangle.top, rectangle.right, rectangle.bottom]}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return fingerprint, identity


def nodes(expected):
    from pywinauto import Desktop
    root = Desktop(backend='uia').window(handle=expected['hwnd']).wrapper_object()
    queue = deque([(root, 0)])
    count = 0
    while queue and count < NODE_LIMIT:
        node, depth = queue.popleft()
        count += 1
        try:
            fingerprint, identity = control_identity(node, expected)
        except ValueError:
            # Excluded parents are not traversed into other processes or
            # password/document subtrees.
            continue
        yield node, fingerprint, identity
        if depth < 3 and identity['type'] not in {'Edit', 'Document'}:
            children = node.children()
            if len(children) + len(queue) > NODE_LIMIT:
                children = children[:max(0, NODE_LIMIT - len(queue))]
            queue.extend((child, depth + 1) for child in children)


def execute(request):
    if not isinstance(request, dict) or type(request.get('version')) is not int or request.get('version') != 1:
        raise ValueError('Invalid UIA request version.')
    action = request.get('action')
    if action == 'attach':
        if set(request) != {'version', 'action', 'titles'}:
            raise ValueError('Unsupported attachment fields.')
        titles = request['titles']
        if not isinstance(titles, list) or not 1 <= len(titles) <= MAX_WINDOWS or len(set(titles)) != len(titles) or any(not isinstance(title, str) or not title or len(title) > 200 or '::' in title for title in titles):
            raise ValueError('Select one to three distinct exact visible window titles.')
        return {'windows': attach(titles)}
    if action not in {'observe', 'locate', 'invoke', 'set_value'} or set(request) != {'version', 'action', 'windows', 'target', 'value'}:
        raise ValueError('Unsupported UIA action or request fields.')
    windows = request['windows']
    if not isinstance(windows, list) or not 1 <= len(windows) <= MAX_WINDOWS:
        raise ValueError('Invalid selected window scope.')
    for window in windows:
        if not isinstance(window, dict) or set(window) != {'hwnd', 'title', 'process'} or not isinstance(window['title'], str):
            raise ValueError('Invalid window identity contract.')
        verify_window(window)
    if action == 'observe':
        if request['target'] or request['value']:
            raise ValueError('Observation cannot carry an action payload.')
        lines = []
        for window in windows:
            for _, fingerprint, identity in nodes(window):
                entry = {'target': window['title'] + '::' + fingerprint, **identity}
                if len(json.dumps(lines + [entry], ensure_ascii=False).encode()) > 10000:
                    break
                lines.append(entry)
            verify_window(window)
        return {'text': json.dumps(lines, ensure_ascii=False), 'truncated_to': '80 nodes/window, depth 3, 10KB total', 'trust': 'untrusted_data'}
    target, value = request['target'], request['value']
    if not isinstance(target, str) or '::' not in target or not isinstance(value, str) or len(value.encode()) > 2000:
        raise ValueError('Invalid UIA control target or value.')
    title, fingerprint = target.split('::', 1)
    if not re.fullmatch(r'[a-f0-9]{64}', fingerprint) or any(ord(character) < 32 and character not in '\t\r\n' for character in value):
        raise ValueError('Use an observed exact control fingerprint and bounded text.')
    selected = next((window for window in windows if window['title'] == title), None)
    if selected is None:
        raise ValueError('Target window is outside this session.')
    matched = [(node, identity) for node, key, identity in nodes(selected) if key == fingerprint]
    if len(matched) != 1:
        raise ValueError('Control identity is stale or ambiguous; observe again.')
    control, identity = matched[0]
    verify_window(selected)
    if control_identity(control, selected)[0] != fingerprint:
        raise ValueError('Control changed before action dispatch.')
    if action == 'locate':
        if value:
            raise ValueError('Locate does not accept a text payload.')
        return {'target': target, **identity, 'effect': 'none'}
    if action == 'invoke':
        if value or identity['type'] not in {'Button', 'Hyperlink', 'MenuItem'}:
            raise ValueError('Invoke supports only observed buttons, links and menu items without text payloads.')
        interface = control.iface_invoke
        verify_window(selected)
        if control_identity(control, selected)[0] != fingerprint:
            raise ValueError('Control changed before Invoke.')
        interface.Invoke()
    else:
        if identity['type'] != 'Edit' or not value:
            raise ValueError('SetValue supports only non-password edit controls with explicit text.')
        interface = control.iface_value
        if interface.CurrentIsReadOnly:
            raise ValueError('Edit control is read-only.')
        verify_window(selected)
        if control_identity(control, selected)[0] != fingerprint:
            raise ValueError('Control changed before SetValue.')
        interface.SetValue(value)
    return {'target': target, 'action': action, 'outcome': 'provider_call_returned',
            'limitation': 'Not proof of application-level success or reversibility; reconcile before retrying.'}


def main():
    # stdout carries one bounded JSON result; libraries cannot inject protocol
    # frames through ordinary print calls. No raw provider exception is returned.
    output = sys.stdout
    sys.stdout = sys.stderr
    try:
        raw = sys.stdin.buffer.readline(INPUT_LIMIT + 1)
        if not raw.endswith(b'\n') or len(raw) > INPUT_LIMIT:
            raise ValueError('Invalid UIA input frame.')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate UIA request field.')
                result[key] = value
            return result
        request = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        response = {'version': 1, 'status': 'completed', 'result': execute(request)}
        encoded = json.dumps(response, ensure_ascii=False).encode()
        if len(encoded) > OUTPUT_LIMIT:
            raise ValueError('UIA output exceeds its bound.')
    except Exception:
        encoded = b'{"version":1,"status":"unverified","detail":"UIA operation failed or its outcome is uncertain. Observe and reconcile before retrying."}'
    output.buffer.write(encoded + b'\n')
    output.buffer.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
