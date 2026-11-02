"""Verify packaged cloud selection without keys, billing or user persistence."""

import argparse
import json
import socket
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path

from scripts.release_integrity import checksum, inspect_portable
from scripts.verify_portable_desktop import desktop, locked_file, owned_listener
from services.agent_api.app.cloud_models import ANTHROPIC_ENDPOINT, GEMINI_ENDPOINT


def verify(archive, screenshot):
    import _winapi

    from playwright.sync_api import expect, sync_playwright

    selections = {
        'gemini': GEMINI_ENDPOINT,
        'anthropic': ANTHROPIC_ENDPOINT,
        'openai_compatible': 'https://compatible.invalid/v1/chat/completions',
    }
    checks = []
    with locked_file(archive) as archive:
        inspect_portable(archive)
        digest = checksum(archive)
        for provider, endpoint in selections.items():
            with tempfile.TemporaryDirectory(prefix='jarvis-cloud-ui-fixture-') as temporary:
                root = Path(temporary)
                with zipfile.ZipFile(archive) as bundle:
                    bundle.extractall(root)
                data = root / 'data'
                data.mkdir()
                configuration = {'provider': provider, 'model': 'fixture-not-a-real-model',
                                 'endpoint': endpoint, 'credential_reference': None,
                                 'voice_enabled': False, 'voice_endpoint': None}
                (data / 'desktop-model.json').write_text(json.dumps(configuration), encoding='utf-8')
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0))
                    port = probe.getsockname()[1]
                with desktop(root / 'USMAN-JARVIS.exe', port) as (kernel, job, process):
                    deadline = time.monotonic() + 120
                    while not owned_listener(kernel, job, port):
                        if _winapi.WaitForSingleObject(process, 0) == 0 or time.monotonic() >= deadline:
                            raise RuntimeError('Cloud fixture failed to launch its owned WebView.')
                        time.sleep(0.2)
                    with sync_playwright() as driver:
                        browser = driver.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
                        page = browser.contexts[0].pages[0]
                        page.set_default_timeout(60000)
                        health = []

                        def response_seen(response):
                            if '/api/v1/health' in response.url and response.status == 200:
                                health.append(response.json())

                        page.on('response', response_seen)
                        page.reload()
                        expect(page.get_by_text('Connected', exact=True)).to_be_visible()
                        expect(page.locator('.top-mode')).to_have_text('Live unavailable')
                        page.get_by_role('button', name='Provider settings', exact=True).click()
                        panel = page.locator('section.surface').filter(
                            has=page.get_by_role('heading', name='Desktop model providers', exact=True))
                        selector = panel.locator('select')
                        expect(selector).to_have_value(provider, timeout=60000)
                        expect(page.get_by_label('Model endpoint', exact=True)).to_have_value(endpoint)
                        expect(page.get_by_label('API key (optional for now)')).to_have_value('')
                        options = selector.locator('option').evaluate_all(
                            '(items) => items.map(item => item.value)')
                        if not set(selections).issubset(options):
                            raise RuntimeError('A packaged cloud provider option is missing.')
                        page.get_by_role('button', name='Save for next desktop launch').click()
                        expect(page.get_by_text('Settings saved.', exact=False)).to_be_visible()
                        commits = list(data.glob('desktop-model-*.commit'))
                        if len(commits) != 1:
                            raise RuntimeError('Native cloud configuration save did not commit exactly once.')
                        saved = json.loads(commits[0].with_suffix('.json').read_text(encoding='utf-8'))
                        if saved != configuration:
                            raise RuntimeError('Native configuration differs from the selected keyless provider.')
                        page.get_by_role('button', name='Chat', exact=True).click()
                        page.get_by_label('Message JARVIS').fill('Keyless fixture: do not use a fallback')
                        with page.expect_response(lambda response: '/api/v1/chat' in response.url) as outgoing:
                            page.get_by_role('button', name='Send', exact=True).click()
                        response = outgoing.value
                        response_body = response.text()
                        if not health or health[-1].get('provider') != provider or health[-1].get('provider_configured'):
                            raise RuntimeError('Packaged health falsely reports a configured cloud model.')
                        if response.status < 400 or 'not configured' not in response_body:
                            raise RuntimeError('Keyless chat did not fail explicitly without fallback.')
                        if page.get_by_text('JARVIS development provider is online.', exact=False).count():
                            raise RuntimeError('Keyless cloud selection silently fell back to mock.')
                        if provider == 'anthropic':
                            page.screenshot(path=str(screenshot), full_page=True)
                database = data / 'jarvis.db'
                if not database.is_file():
                    raise RuntimeError('Expected one disposable cloud fixture database.')
                with closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as connection:
                    if connection.execute('SELECT COUNT(*) FROM model_reservations').fetchone()[0]:
                        raise RuntimeError('Keyless fixture unexpectedly reserved paid usage.')
                checks.append(f'{provider}: real native keyless save/startup; unavailable chat, no mock fallback or budget reservation')
        if checksum(archive) != digest:
            raise RuntimeError('Cloud archive changed during verification.')
    return {'format': 'jarvis-cloud-desktop-keyless-v1', 'archive_sha256': digest,
            'checks': checks, 'persistence': 'temporary, removed', 'live_vendor_calls': False,
            'limitations': ['No real API keys, vendor model access, billing or live responses verified.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--screenshot', type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.evidence.exists() or arguments.screenshot.exists():
        raise SystemExit('Select absent evidence and screenshot paths.')
    result = verify(arguments.archive, arguments.screenshot)
    with arguments.evidence.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
    print('Actual packaged cloud keyless acceptance passed; no live vendor calls.')


if __name__ == '__main__':
    main()
