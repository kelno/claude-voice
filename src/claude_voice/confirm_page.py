"""The owner's confirm page and passkey registration page: plain, minimal, a stopgap (ADR-CV-011).

Both pages talk to the bridge with relative addresses, so they work under any path prefix the
proxy publishes them at. The passkey itself does the security: see docs/owner-confirmation.md.
"""

from __future__ import annotations

import html
from typing import Any

STYLE = (
    "body{font-family:sans-serif;max-width:34em;margin:1.5em auto;padding:0 1em;line-height:1.4}"
    "button{font-size:1.1em;padding:.6em 1.2em;margin:.3em .5em .3em 0}"
    ".summary{border:1px solid #888;padding:.8em;margin:1em 0;white-space:pre-wrap}"
    ".aside{margin-top:2.5em;padding-top:.6em;border-top:1px solid #ccc;font-size:.85em;color:#666}"
)

# Turning the bridge's JSON options into what the browser's passkey API takes, and back.
SCRIPT = """
const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
  .replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
const unb64 = (s) => Uint8Array.from(
  atob(s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - s.length % 4) % 4)),
  (c) => c.charCodeAt(0));
const here = location.pathname.replace(/\\/+$/, '');
const say = (t) => { document.getElementById('status').textContent = t; };
// After an answer, say so at once and try to close; a browser usually refuses to close a tab it
// didn't open (seen on Android, from the Claude app), so the text never promises it will.
function closeSoon(done) {
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  say(done + ' You can close this tab and go back to the chat.');
  // Tested, and refused in at least one case: on Android, in a tab opened from a link in the Claude
  // app, the browser won't close a tab no script opened. Kept in case a browser allows it.
  window.close();
}
async function post(path, body) {
  const r = await fetch(here + '/' + path, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const out = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(out.error || ('HTTP ' + r.status));
  return out;
}
function asJson(c) {
  const r = c.response;
  const out = {id: c.id, rawId: b64(c.rawId), type: c.type,
    response: {clientDataJSON: b64(r.clientDataJSON)}, clientExtensionResults: c.getClientExtensionResults()};
  if (r.attestationObject) out.response.attestationObject = b64(r.attestationObject);
  if (r.authenticatorData) {
    out.response.authenticatorData = b64(r.authenticatorData);
    out.response.signature = b64(r.signature);
  }
  if (r.userHandle) out.response.userHandle = b64(r.userHandle);
  return out;
}
"""


def _page(title: str, body: str, script: str = "") -> str:
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head><body>"
        f"<h2>{html.escape(title)}</h2>{body}{'' if "id='status'" in body else "<p id='status'></p>"}"
        + (f"<script>{SCRIPT}{script}</script>" if script else "")
        + "</body></html>"
    )


def message(title: str, text: str) -> str:
    return _page(title, f"<p>{html.escape(text)}</p>")


def request_page(req: dict[str, Any], state: str) -> str:
    """One request, as the bridge stored it, with Confirm (passkey) and Decline."""
    who = "a folder to open" if req["kind"] == "folder" else f"session {req['asker']}"
    body = (
        f"<p>Request <b>{html.escape(req['code'])}</b>, from {html.escape(who)}:</p>"
        f"<div class='summary'>{html.escape(req['summary'])}</div>"
    )
    if state != "pending":
        return _page("Owner confirmation", body + f"<p>This request is <b>{html.escape(state)}</b>.</p>")
    if req["kind"] == "folder":
        source, effect = "named in a request_access call", "opens only this folder, once"
    else:
        source, effect = f"written by session {req['asker']}", "approves only this request, once, for that session"
    body += (
        "<button id='yes'>Confirm with passkey</button><button id='no'>Decline</button><p id='status'></p>"
        f"<div class='aside'><b>Why you can trust this page:</b> the request above was {html.escape(source)} and"
        " stored by the bridge; this page shows it as stored, and the relay can't change it. Confirming with your"
        f" passkey {html.escape(effect)}. Declining, or doing nothing for 15 minutes, approves nothing.</div>"
    )
    script = """
document.getElementById('yes').onclick = async () => {
  try {
    say('Waiting for your passkey...');
    const o = await post('challenge');
    o.challenge = unb64(o.challenge);
    (o.allowCredentials || []).forEach(c => { c.id = unb64(c.id); });
    const c = await navigator.credentials.get({publicKey: o});
    await post('confirm', asJson(c));
    closeSoon('Confirmed.');
  } catch (e) { say('Not confirmed: ' + e.message); }
};
document.getElementById('no').onclick = async () => {
  try { await post('decline'); closeSoon('Declined.'); }
  catch (e) { say('Not declined: ' + e.message); }
};
"""
    return _page("Owner confirmation", body, script)


def registration_page() -> str:
    body = (
        "<p>Add a passkey for confirming requests to the claude-voice bridge. Your device will ask for "
        "your fingerprint, face or PIN.</p>"
        "<p><label>Name for this passkey: <input id='name' value='phone'></label></p>"
        "<button id='add'>Add passkey</button>"
    )
    script = """
document.getElementById('add').onclick = async () => {
  try {
    say('Waiting for your device...');
    const o = await post('options');
    o.challenge = unb64(o.challenge);
    o.user.id = unb64(o.user.id);
    (o.excludeCredentials || []).forEach(c => { c.id = unb64(c.id); });
    const c = await navigator.credentials.create({publicKey: o});
    await post('register', {name: document.getElementById('name').value, credential: asJson(c)});
    say('Passkey added. You can close this page.');
    document.getElementById('add').disabled = true;
  } catch (e) { say('Not added: ' + e.message); }
};
"""
    return _page("Add a passkey", body, script)
