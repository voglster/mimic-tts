// Thin wrapper over the mimic-tts HTTP API. Token is held in localStorage and
// attached as a bearer header on every call.

const TOKEN_KEY = 'mimic-token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(t) {
  localStorage.setItem(TOKEN_KEY, t)
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(extra = {}) {
  const t = getToken()
  return { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...extra }
}

function extFromMime(mime) {
  if (!mime) return 'bin'
  if (mime.includes('webm')) return 'webm'
  if (mime.includes('mp4') || mime.includes('aac')) return 'm4a'
  if (mime.includes('ogg')) return 'ogg'
  if (mime.includes('wav')) return 'wav'
  return 'bin'
}

async function readError(resp) {
  // FastAPI errors look like {"detail": "..."} but other responses may be
  // plain text — handle both.
  try {
    const j = await resp.json()
    return j.detail || JSON.stringify(j)
  } catch {
    return (await resp.text()) || `HTTP ${resp.status}`
  }
}

export class AuthError extends Error {
  constructor() {
    super('unauthorized')
    this.name = 'AuthError'
  }
}

// Any 401/403 from the API is a signal that the stored token is no longer
// valid (rotated server-side, typo, etc). Wipe it and surface a typed error
// so the UI can bounce back to the token gate.
async function request(input, init = {}) {
  const r = await fetch(input, init)
  if (r.status === 401 || r.status === 403) {
    clearToken()
    throw new AuthError()
  }
  return r
}

export async function checkAuth() {
  // /voices requires auth — use it as the canary so we know the token is good.
  // We DON'T route through `request()` here because TokenGate needs the bool
  // result, not a thrown AuthError that triggers re-gating mid-gate.
  const r = await fetch('/voices', { headers: authHeaders() })
  if (r.status === 401 || r.status === 403) {
    clearToken()
    return false
  }
  if (!r.ok) throw new Error(await readError(r))
  return true
}

export async function listVoices() {
  const [builtinResp, cloneResp] = await Promise.all([
    request('/voices', { headers: authHeaders() }),
    request('/clone/voices', { headers: authHeaders() }),
  ])
  if (!builtinResp.ok) throw new Error(await readError(builtinResp))
  if (!cloneResp.ok) throw new Error(await readError(cloneResp))
  const builtin = await builtinResp.json()
  const clones = await cloneResp.json()
  const built = (builtin.voices || []).map((v) => ({ name: v.name, kind: 'builtin' }))
  const cloned = (clones.voices || []).map((n) => ({ name: n, kind: 'clone' }))
  return [...built, ...cloned]
}

export async function speak(text, voice) {
  const form = new FormData()
  form.append('text', text)
  // mp3 plays on every browser including iOS < 17, and the 64 kbps the server
  // emits is ~6x smaller than the raw WAV — much better for phone playback.
  form.append('format', 'mp3')
  let endpoint
  if (voice.kind === 'builtin') {
    form.append('speaker', voice.name)
    endpoint = '/tts'
  } else {
    form.append('name', voice.name)
    endpoint = '/clone/tts'
  }
  const r = await request(endpoint, {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  if (!r.ok) throw new Error(await readError(r))
  return r.blob()
}

export async function transcribe(audioBlob) {
  const form = new FormData()
  const ext = extFromMime(audioBlob.type)
  form.append('audio', audioBlob, `clip.${ext}`)
  const r = await request('/stt', { method: 'POST', headers: authHeaders(), body: form })
  if (!r.ok) throw new Error(await readError(r))
  const j = await r.json()
  return j.text
}

export async function deleteClone(name) {
  const r = await request(`/clone/voices/${encodeURIComponent(name)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

export async function getHealth() {
  // /health is unauthenticated — used for feature-flag discovery (stt_enabled).
  const r = await fetch('/health')
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}

export async function registerClone(name, audioBlob, refText) {
  const form = new FormData()
  form.append('name', name)
  // Extension picked from the blob's MIME so the server's ffmpeg gets the
  // strongest possible hint about the container format.
  const ext = extFromMime(audioBlob.type)
  form.append('ref_audio', audioBlob, `${name}.${ext}`)
  form.append('ref_text', refText)
  const r = await request('/clone/register', {
    method: 'POST',
    headers: authHeaders(),
    body: form,
  })
  if (!r.ok) throw new Error(await readError(r))
  return r.json()
}
