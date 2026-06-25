# One-time iOS capture — find Strong's API host

Everything about Strong's API is already known (see `strong-api-contract.md`) **except
the base hostname**, which the reference tool withholds. This is a ~10-minute, one-time
step on your iPhone + Mac. You're only reading the host of your own traffic — no
credentials need to leave your machine.

## What we need to come away with
1. `STRONG_BACKEND` — the base URL, e.g. `https://api.example.com/` (note the trailing `/`).
2. Confirm the `x-client-build` header value (reference tool used `600013` — may be newer).
3. Confirm the **weight unit** Strong sends (kg vs lb) — see step 7.

## Steps

### 1. Install mitmproxy on the Mac
```bash
brew install mitmproxy
mitmweb           # web UI at http://127.0.0.1:8081, proxy listens on :8080
```
Leave it running. Note your Mac's LAN IP: `ipconfig getifaddr en0`.

### 2. Point the iPhone at the proxy
iPhone must be on the **same Wi-Fi** as the Mac.
Settings → Wi-Fi → (i) on your network → **Configure Proxy → Manual**
- Server: `<Mac LAN IP>`
- Port: `8080`
Save.

### 3. Install + trust the mitmproxy CA on the iPhone
- In Safari, open `http://mitm.it` → tap the **Apple** icon → **Get Certificate** (downloads a profile).
- Settings → General → VPN & Device Management → tap the **mitmproxy** profile → **Install**.
- Then enable FULL trust: Settings → General → About → **Certificate Trust Settings** →
  toggle **mitmproxy** ON. (Without this, TLS interception fails.)

### 4. Trigger a Strong sync
Open the Strong app. Force a sync: pull-to-refresh on history, or log/edit a set, or
just background→foreground the app. Strong should talk to its backend.

### 5. Read the host in mitmweb
In the mitmweb flow list, look for requests to a non-Apple host, specifically:
- `POST …/auth/login`  (only if it re-authenticates), or
- `GET …/api/users/<your-user-id>?…&includes=log…`

The **host** of those requests is your `STRONG_BACKEND`. Also open one request and
confirm the request headers — note the actual `x-client-build` and `x-client-platform`.

> Tip: filter the flow list with `~d` + part of the host, or `~u /api/users` once you
> spot it. The path will be `auth/login`, `auth/login/refresh`, or `api/users/{id}`.

### 6. Record the values
Create `.env` in the project root (copy from `.env.example`) and set:
```
STRONG_BACKEND=https://<host>/
STRONG_USER=<your strong email/username>
STRONG_PASS=<your strong password>
STRONG_CLIENT_BUILD=<value seen in step 5, e.g. 600013>
```

### 7. Confirm the weight unit (important — wrong unit = wrong weights on Garmin)
Pick a workout where you remember the exact weight (say 100 kg / 225 lb). In mitmweb,
open the `GET …/api/users/{id}?…includes=log` response, find that set's weight `value`
in the JSON cells. If it reads `100` you're in kg; `225` → lb; a long decimal → it's
stored in the *other* unit and converted. Set `STRONG_WEIGHT_UNIT=kg` or `lb` in `.env`
accordingly. (We can also auto-detect later, but confirming once removes all doubt.)

### 8. Clean up
- iPhone: Settings → Wi-Fi → (i) → Configure Proxy → **Off**.
- Optionally remove cert trust (About → Certificate Trust Settings) and the profile.
- Stop `mitmweb` (Ctrl-C).

## If interception fails (TLS errors, app won't sync) = certificate pinning
The reference tool shows no pinning issues, so this is unlikely. But if Strong refuses
to talk through mitmproxy, it's pinning the cert. Options then:
- A jailbroken device + SSL Kill Switch / Frida to bypass pinning, or
- Pull the base URL by statically analysing the Strong IPA.
Tell me if you hit this and I'll guide the fallback. Most likely you won't.
