# Aeroplan bridge

Runs Aeroplan award searches inside a signed-in browser on the Windows PC, so [`aeroplan_value.py`](../aeroplan_value.py) can price flights in points from this host. Air Canada's bot protection ([Akamai Bot Manager](https://www.akamai.com/products/bot-manager)) rejects headless and automated sign-ins (`accounts.login` answers HTTP 403, and repeated attempts get the network address blocked), but it does not interfere with the site's own page running in an ordinary browser window.

## How it fits together

- `extension/` is a Chrome extension (Manifest V3). `page_hook.js` runs in the page's own JavaScript world at document start and copies the award-search responses the page receives from `dbaas.aircanada.com`; `relay.js` passes them to `background.js`. For each search, `background.js` loads the award search page for that route and date in a pinned tab and returns the captured responses. It can also sign the tab in through the `chrome.debugger` API, which produces the real clicks and key events the login form requires: it submits the login Chrome has saved in this profile, asks Aeroplan to email the one-time code, and enters the code `aeroplan_value.py` reads from Gmail. Clicks go only to the visible, topmost matching element, because the page keeps hidden copies of its login screens.
- `host/aeroplan_bridge_host.py` is a [Chrome native messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) host. The browser starts it when the extension loads and stops it with the browser. It serves an HTTP API on `127.0.0.1:47820`, runs one search at a time at least 15 seconds apart, and logs to `%LOCALAPPDATA%\aeroplan_bridge\host.log` (stderr to `host.err`):
  - `GET /status`: extension connection and the bridge tab's page
  - `POST /search` `{"origin", "destination", "date", "adults"}`: award search results
  - `POST /signin` `{"emailCode": true}`: submit the saved login and request the code by email; `{"code"}`: enter the code
- `aeroplan_value.py` reaches that port through `ssh -L` to the `windows` host.

The browser is [Chrome for Testing](https://developer.chrome.com/blog/chrome-for-testing), installed in `browser\` with its own data folder `chrome-data\`. It still honours `--load-extension`, which branded Chrome removed, so the extension loads without any clicking; it is otherwise a normal, visible browser with no automation attached.

## Setup on the Windows PC

1. Copy this folder to `C:\Users\Mihai\aeroplan_bridge` and install the browser there: `npx @puppeteer/browsers install chrome@stable --path C:\Users\Mihai\aeroplan_bridge\browser`.
2. Run `powershell -File install.ps1`. It registers the host for the current user under `HKCU\Software\{Google\Chrome, Google\Chrome for Testing, Chromium}\NativeMessagingHosts`, writes `launch_browser.bat`, and adds an "Aeroplan bridge" shortcut to the Startup folder so the browser opens minimised at logon.
3. Start the browser in the interactive desktop session (`launch_browser.bat`), sign in to Aeroplan once by hand and let Chrome save the login. After that, `aeroplan_value.py` signs in again by itself whenever a search finds the session expired (Aeroplan sessions last a few hours): it uses the saved login, requests the code by email, and reads it from Gmail (`info@communications.aeroplan.com`) with the app password `git send-email` already uses.

The extension ID is fixed by the key in `manifest.json`: `jeajfjhfhihlkjmbojgepccpjdolejfc`.

## Maintenance notes

- Chrome keeps running a cached copy of the extension's background script across restarts, and `chrome.runtime.reload()` does not bring back an extension loaded with `--load-extension`. After changing `background.js`, bump `SCRIPT_VERSION` and `version` in `manifest.json`, close the bridge browser, delete `chrome-data\Default\Service Worker`, and start it again. The host log's `extension connected version=manifest/script` line confirms the new script is running.
- The host runs Python with `-I` so it ignores `PYTHONHOME`/`PYTHONPATH` inherited from whatever started the browser; a mismatched standard library otherwise crashes it on start.
- `install.ps1` writes the host manifest without a byte-order mark, which Chrome rejects.
- `--sign-in` runs the sign-in on demand; `--code NNNNNN` enters a code by hand, for example one sent by text.
