"""Owned native AppContainer/WFP broker and bounded private CDP integration.

Activation requires a source-reviewed accepted binary pair. The allowlist is
empty until Windows acceptance; no environment switch or receipt can grant it.
No TCP debugging, browser download, user profile, uploads or unrestricted launch.
"""

import asyncio
import json
import os
import re
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from services.agent_api.app.network_security import NetworkDestinationPolicy, PinnedHTTPProxy
from services.agent_api.app.session_security import (
    execution_authorized,
    execution_dispatch_authorized,
    execution_session,
)
from services.agent_api.app.studio_commands import locked_executable

# Populate only from a later independent Windows acceptance/release review.
# This is a code gate, not an untrusted configuration/attestation file.
ACCEPTED_BROWSER_BUILDS: frozenset[tuple[str, str]] = frozenset()


async def release_broker(process, proxy, stack):
    """Finish owned-resource cleanup even if the caller is cancelled again.

    Closing the native broker closes its kill-on-close browser job. This does
    not remove persistent deny filters or claim to undo accepted page effects.
    """
    async def release():
        try:
            if process is not None:
                try:
                    if process.stdin:
                        process.stdin.close()
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except asyncio.TimeoutError:
                        pass
                finally:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        await asyncio.wait_for(process.wait(), 2)
        finally:
            try:
                await proxy.close()
            finally:
                stack.close()

    cleanup = asyncio.create_task(release())
    interrupted = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            interrupted = True
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
    # Retrieve failures; never leave a detached cleanup task with lost errors.
    cleanup.result()
    if interrupted:
        raise asyncio.CancelledError


class NativeBrowserRuntime:
    def __init__(self, broker, browser):
        self.broker, self.browser = Path(broker), Path(browser)

    @property
    def available(self):
        return os.name == 'nt' and bool(ACCEPTED_BROWSER_BUILDS)

    async def create(self, domains, policy):
        if not self.available or policy.allow_private_addresses:
            raise ValueError('OS-level browser isolation has no accepted native build.')
        stack = ExitStack()
        proxy = PinnedHTTPProxy(NetworkDestinationPolicy(tuple(domains), allowed_ports={80, 443}))
        process = None
        try:
            if not self.broker.is_absolute() or not self.browser.is_absolute():
                raise ValueError('Specify absolute native browser executable paths.')
            broker = self.broker.resolve(strict=True)
            browser = self.browser.resolve(strict=True)
            if not broker.is_absolute() or broker.suffix.lower() != '.exe' or browser.suffix.lower() != '.exe':
                raise ValueError('Native browser executables are unavailable.')
            pair = (stack.enter_context(locked_executable(broker)), stack.enter_context(locked_executable(browser)))
            if pair not in ACCEPTED_BROWSER_BUILDS:
                raise ValueError('Broker/browser build changed; independent acceptance is required.')
            proxy_url = await proxy.start()
            port = urlsplit(proxy_url).port
            if not execution_authorized.get()():
                raise ValueError('Browser owner session expired.')
            environment = {key: value for key, value in os.environ.items() if key.lower() in {'systemroot', 'windir', 'temp', 'tmp', 'localappdata', 'userprofile'}}
            process = await asyncio.create_subprocess_exec(str(broker), '--request-managed-browser', str(port), str(browser),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=environment, cwd=str(broker.parent), limit=1024 * 1024 + 1)
            # Includes explicit Windows UAC interaction and private-pipe pairing.
            # Backend stays non-elevated; cancellation closes the relay owner.
            line = await asyncio.wait_for(process.stdout.readline(), 75)
            if len(line) > 1024:
                raise ValueError('Native isolation receipt exceeds its bound.')
            receipt = json.loads(line)
            if (not isinstance(receipt, dict) or type(receipt.get('protocol')) is not int or receipt.get('protocol') != 1
                    or type(receipt.get('proxy_port')) is not int or receipt.get('proxy_port') != port or type(receipt.get('pid')) is not int
                    or receipt['pid'] <= 0 or not re.fullmatch(r'S-1-15-2-(?:[0-9]+-){6}[0-9]+', str(receipt.get('package_sid', '')))):
                raise ValueError('Native AppContainer isolation receipt is invalid.')
            channel = CDPChannel(process, proxy, stack)
            adapter = IsolatedBrowserAdapter(channel, tuple(domains), policy)
            await asyncio.wait_for(adapter.initialize(), 15)
            return adapter
        except BaseException:
            await release_broker(process, proxy, stack)
            raise


class CDPChannel:
    def __init__(self, process, proxy, stack):
        self.process, self.proxy, self.stack = process, proxy, stack
        self.owner, self.owner_active = execution_session.get(), execution_authorized.get()
        self.lock = asyncio.Lock()
        self.sequence = 0
        self.closed = False

    def authorize(self):
        execution_dispatch_authorized.get()()
        if self.closed or self.process.returncode is not None or execution_session.get() != self.owner or not self.owner_active():
            raise ValueError('Browser broker stopped or its owner session is unavailable.')

    async def call(self, method, params=None, session_id=None):
        async with self.lock:
            try:
                self.authorize()
                self.sequence += 1
                message = {'id': self.sequence, 'method': method, 'params': params or {}}
                if session_id:
                    message['sessionId'] = session_id
                encoded = json.dumps(message, ensure_ascii=False).encode() + b'\0'
                if len(encoded) > 32000:
                    raise ValueError('Browser command exceeds its bound.')
                self.authorize()
                self.process.stdin.write(encoded)
                await asyncio.wait_for(self.process.stdin.drain(), 8)
                async with asyncio.timeout(8):
                    for _ in range(128):
                        frame = await self.process.stdout.readuntil(b'\0')
                        if len(frame) > 1024 * 1024:
                            raise ValueError('Browser result exceeds its bound.')
                        result = json.loads(frame[:-1])
                        if not isinstance(result, dict):
                            raise ValueError('Malformed browser result.')
                        if 'id' in result and (type(result['id']) is not int or result['id'] != self.sequence):
                            raise ValueError('Browser response does not match the pending command.')
                        if result.get('id') == self.sequence:
                            self.authorize()
                            if 'error' in result or not isinstance(result.get('result'), dict):
                                raise ValueError('Browser rejected this operation.')
                            return result['result']
                    raise ValueError('Browser event stream exceeded its bound.')
            except BaseException:
                await self.close()
                raise

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await release_broker(self.process, self.proxy, self.stack)


class IsolatedBrowserAdapter:
    def __init__(self, channel, domains, policy):
        self.channel, self.domains, self.policy = channel, domains, policy
        self.session_id = None
        self.operations = asyncio.Lock()

    async def initialize(self):
        await self.channel.call('Browser.setDownloadBehavior', {'behavior': 'deny'})
        result = await self.channel.call('Target.createTarget', {'url': 'about:blank'})
        self.target = result['targetId']
        attached = await self.channel.call('Target.attachToTarget', {'targetId': self.target, 'flatten': True})
        self.session_id = attached['sessionId']
        await self.channel.call('Page.enable', session_id=self.session_id)
        await self.channel.call('Runtime.enable', session_id=self.session_id)

    async def evaluate(self, expression):
        result = await self.channel.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': False}, self.session_id)
        if result.get('exceptionDetails') or 'value' not in result.get('result', {}):
            raise ValueError('Page changed or the requested element is unavailable.')
        return result['result']['value']

    async def location(self):
        url = await self.evaluate('location.href')
        if not isinstance(url, str):
            raise ValueError('Browser location is unavailable.')
        await self.policy.validate(url, self.domains)
        return url

    async def open_url(self, url):
        async with self.operations:
            await self.policy.validate(url, self.domains)
            result = await self.channel.call('Page.navigate', {'url': url}, self.session_id)
            if result.get('errorText'):
                raise ValueError('Browser navigation was blocked or failed.')
            try:
                async with asyncio.timeout(8):
                    while True:
                        tree = await self.channel.call('Page.getFrameTree', session_id=self.session_id)
                        frame = tree.get('frameTree', {}).get('frame', {})
                        if result.get('loaderId') and frame.get('loaderId') == result['loaderId'] or not result.get('loaderId') and frame.get('url') == url:
                            final_url = frame.get('url')
                            await self.policy.validate(final_url, self.domains)
                            return {'url': final_url}
                        await asyncio.sleep(0.05)
            except BaseException:
                await self.close()
                raise

    async def inspect_page(self):
        async with self.operations:
            url = await self.location()
            text = await self.evaluate('(document.body?.innerText ?? "").slice(0,20000)')
            if not isinstance(text, str) or len(text) > 20000:
                raise ValueError('Page text exceeded its bound.')
            return {'url': url, 'text': text}

    async def act(self, action, target, value=''):
        async with self.operations:
            await self.location()
            if action in {'upload', 'download'}:
                raise ValueError('Browser file transfer is unavailable; use separately approved account/media workflows.')
            if action == 'refresh':
                await self.channel.call('Page.reload', {'ignoreCache': False}, self.session_id)
            elif action in {'go_back', 'go_forward'}:
                history = await self.channel.call('Page.getNavigationHistory', session_id=self.session_id)
                index = history['currentIndex'] + (-1 if action == 'go_back' else 1)
                if not 0 <= index < len(history['entries']):
                    raise ValueError('Requested history entry is unavailable.')
                entry = history['entries'][index]
                await self.policy.validate(entry['url'], self.domains)
                await self.channel.call('Page.navigateToHistoryEntry', {'entryId': entry['id']}, self.session_id)
            elif action == 'wait':
                await self.channel.call('Runtime.evaluate', {'expression': 'document.readyState', 'returnByValue': True}, self.session_id)
            elif action in {'click', 'type', 'select', 'scroll'}:
                # Model strings are JSON data inside a fixed program, never code.
                expression = '''(() => {
                  const [selector, value, action] = %s;
                  const matches = document.querySelectorAll(selector);
                  if(matches.length !== 1) throw Error('ambiguous element');
                  const element = matches[0], box = element.getBoundingClientRect();
                  if(!box.width || !box.height || element.disabled || getComputedStyle(element).visibility === 'hidden') throw Error('unavailable element');
                  if(action === 'click') element.click();
                  else if(action === 'scroll') element.scrollIntoView({block:'center'});
                  else {
                    if(element.type === 'password' || element.type === 'file') throw Error('protected field');
                    if(action === 'select' && !(element instanceof HTMLSelectElement)) throw Error('not select');
                    const proto = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : element instanceof HTMLInputElement ? HTMLInputElement.prototype : null;
                    const setter = proto && Object.getOwnPropertyDescriptor(proto,'value')?.set;
                    if(!setter) throw Error('unsupported input');
                    setter.call(element,value); element.dispatchEvent(new Event('input',{bubbles:true})); element.dispatchEvent(new Event('change',{bubbles:true}));
                  }
                  return true;
                })()''' % json.dumps([target, value, action])
                await self.evaluate(expression)
            else:
                raise ValueError('Unsupported browser action.')
            return {'url': await self.location(), 'action': action, 'verification': 'browser_call_returned; downstream effects are not verified'}

    async def close(self):
        await self.channel.close()
