#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from smoke_auth_feature_flags import (
    Client,
    install_fake_supabase_auth,
    prepare_fixture_store,
    restore_requests,
    run_contract,
    start_fixture_server,
)

import app


def chrome_binary() -> str:
    candidates = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        shutil.which('google-chrome'),
        shutil.which('google-chrome-stable'),
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise RuntimeError('Chrome or Chromium is required for rendered auth smoke verification')


def rendered_dom(chrome: str, url: str, screenshot_path: Path) -> str:
    cmd = [
        chrome,
        '--headless=new',
        '--disable-gpu',
        '--no-first-run',
        '--no-default-browser-check',
        '--hide-scrollbars',
        '--window-size=1280,900',
        '--virtual-time-budget=5000',
        f'--screenshot={screenshot_path}',
        '--dump-dom',
        url,
    ]
    result = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f'Chrome render failed ({result.returncode}): {result.stderr[-800:]}')
    return result.stdout


def main() -> int:
    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        prepare_fixture_store(tmp_path)
        original_post, original_get = install_fake_supabase_auth()
        server, thread, _port = start_fixture_server()
        client = Client(app.APP_BASE_URL)
        screenshot_path = tmp_path / 'auth-feature-flags.png'
        try:
            run_contract(client)
            url = f'{app.APP_BASE_URL}/auth?next=%2F'
            # Give the fixture server one tick after the API contract logs out.
            time.sleep(0.05)
            dom = rendered_dom(chrome_binary(), url, screenshot_path)
            markers = [
                'Create your TimeGrid account',
                'student@example.edu',
                'Create account with email',
                'Continue with Google',
                'Continue with Apple',
                'Continue with Mastodon',
                'Choose one sign-in method',
            ]
            missing = [marker for marker in markers if marker not in dom]
            if missing:
                raise AssertionError(f'missing rendered auth markers: {missing}')
            if not screenshot_path.exists() or screenshot_path.stat().st_size < 1000:
                raise AssertionError('rendered auth screenshot was not created')
            print(json.dumps({
                'ok': True,
                'url': url,
                'markers': markers,
                'screenshot_bytes': screenshot_path.stat().st_size,
                'email_example': 'student@example.edu',
                'google_example': 'google.student@example.edu',
                'apple_example': 'apple.student@example.edu',
            }, indent=2))
        finally:
            restore_requests(original_post, original_get)
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
