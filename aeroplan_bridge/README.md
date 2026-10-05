# Aeroplan bridge

Runs Aeroplan award searches inside a signed-in browser on the Windows PC, so [`aeroplan_value.py`](../aeroplan_value.py) can price flights in points from this host. Air Canada's bot protection ([Akamai Bot Manager](https://www.akamai.com/products/bot-manager)) rejects headless and automated sign-ins (`accounts.login` answers HTTP 403, and repeated attempts get the network address blocked), but it does not interfere with the site's own page running in an ordinary browser window.

## How it fits together

- `extension/` is a Chrome extension (Manifest V3). `page_hook.js` runs in the page's own JavaScript world at document start and copies the award-search responses the page receives from `dbaas.aircanada.com`; `relay.js` passes them to `background.js`. For each search, `background.js` loads the award search page for that route and date in a pinned tab and returns the captured responses. It can also sign the tab in: it types the login, and then the one-time code Aeroplan sends, through the `chrome.debugger` API, which produces the real key and mouse events the login form requires. Credentials pass through and are never stored.
- `host/aeroplan_bridge_host.py` is a [Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) host. The browser starts it when the extension loads and stops it with the browser. It serves an HTTP API on `127.0.0.1:47820`, runs one search at a time at least 15 seconds apart, and logs to `%LOCALAPPDATA%\aeroplan_bridge\host.log` (stderr to `host.err`):
  - `GET /status`: extension connection and the bridge tab's page
  - `POST /search` `{"origin", "destination", "date", "adults"}`: award search results
  - `POST /signin` `{"user", "password"}` or `{"code"}`: sign in, then enter the one-time code
  - `POST /reload`: reload the extension from disk after an update
- `aeroplan_value.py` reaches that port through `ssh -L` to the `windows` host.

The browser is [Chrome for Testing](https://developer.chrome.com/blog/chrome-for-testing), installed in `browser\` with its own data folder `chrome-data\`. It still honours `--load-extension`, which branded Chrome removed, so the extension loads without any clicking; it is otherwise a normal, visible browser with no automation attached.

## Setup on the Windows PC

1. Copy this folder to `C:\Users\Mihai\aeroplan_bridge` and install the browser there: `npx @puppeteer/browsers install chrome@stable --path C:\Users\Mihai\aeroplan_bridge\browser`.
2. Run `powershell -File install.ps1`. It registers the host for the current user under `HKCU\Software\{Google\Chrome, Google\Chrome for Testing, Chromium}\NativeMessagingHosts`, writes `launch_browser.bat`, and adds an "Aeroplan bridge" shortcut to the Startup folder so the browser opens minimised at logon.
3. Start the browser in the interactive desktop session (`launch_browser.bat`). From this host, run `aeroplan_value.py --sign-in`, then `aeroplan_value.py --code NNNNNN` with the code Aeroplan texts.

The extension ID is fixed by the key in `manifest.json`: `jeajfjhfhihlkjmbojgepccpjdolejfc`.

## Maintenance notes

- Chrome keeps running a cached copy of the extension's background script across restarts. After changing `background.js`, bump `version` in `manifest.json` and call `POST /reload` (or delete `chrome-data\Default\Service Worker` while the browser is closed).
- The host runs Python with `-I` so it ignores `PYTHONHOME`/`PYTHONPATH` inherited from whatever started the browser; a mismatched standard library otherwise crashes it on start.
- `install.ps1` writes the host manifest without a byte-order mark, which Chrome rejects.
- If the session expires, searches fail with `signed_out`; sign in again with `--sign-in` and `--code`.
