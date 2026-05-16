import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  AuthError,
  checkAuth,
  clearToken,
  getToken,
  listVoices,
  registerClone,
  setToken,
  speak,
} from './api.js'
import { startRecording } from './recorder.js'

const DEFAULT_PHRASE =
  "The quick brown fox jumps over the lazy dog while the moon hangs low in the sky."

export default function App() {
  const [authed, setAuthed] = useState(false)
  const [voices, setVoices] = useState([])
  const [error, setError] = useState('')

  const handleError = useCallback((e) => {
    if (e instanceof AuthError) {
      setAuthed(false)
      setError('')
      return
    }
    setError(String(e.message || e))
  }, [])

  const refreshVoices = useCallback(async () => {
    try {
      setVoices(await listVoices())
    } catch (e) {
      handleError(e)
    }
  }, [handleError])

  useEffect(() => {
    if (!getToken()) {
      setAuthed(false)
      return
    }
    checkAuth()
      .then((ok) => {
        setAuthed(ok)
        if (ok) refreshVoices()
      })
      .catch((e) => handleError(e))
  }, [refreshVoices, handleError])

  if (!authed) {
    return <TokenGate onAuthed={() => { setAuthed(true); refreshVoices() }} />
  }

  return (
    <div className="app">
      <header>
        <div>
          <h1>mimic-tts</h1>
          <div className="sub">clone voices · play tts · party trick mode</div>
        </div>
        <div className="token-bar">
          <button onClick={() => { clearToken(); setAuthed(false) }}>sign out</button>
        </div>
      </header>

      {error && <div className="card" style={{ borderColor: 'var(--danger)' }}>{error}</div>}

      <CloneCard onCloned={refreshVoices} onError={handleError} />
      <SpeakCard voices={voices} onRefresh={refreshVoices} onError={handleError} />
    </div>
  )
}

function TokenGate({ onAuthed }) {
  const [token, setTokenInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr('')
    setToken(token.trim())
    try {
      const ok = await checkAuth()
      if (ok) onAuthed()
      else setErr('Token rejected.')
    } catch (e) {
      setErr(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app">
      <header>
        <h1>mimic-tts</h1>
      </header>
      <form className="card" onSubmit={submit}>
        <h2>Enter access token</h2>
        <div className="hint">Paste the bearer token to use this UI.</div>
        <label>Token</label>
        <input
          type="password"
          value={token}
          onChange={(e) => setTokenInput(e.target.value)}
          autoFocus
          placeholder="MIMIC_API_TOKEN"
        />
        <div style={{ marginTop: 14 }}>
          <button disabled={busy || !token.trim()} type="submit">
            {busy ? 'Checking…' : 'Continue'}
          </button>
        </div>
        {err && <div className="status error">{err}</div>}
      </form>
    </div>
  )
}

function CloneCard({ onCloned, onError }) {
  const [phrase, setPhrase] = useState(DEFAULT_PHRASE)
  const [name, setName] = useState('')
  const [recording, setRecording] = useState(false)
  const [previewUrl, setPreviewUrl] = useState('')
  const [wavBlob, setWavBlob] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const recRef = useRef(null)

  const toggleRecord = async () => {
    setStatus('')
    if (recording) {
      const blob = await recRef.current.stop()
      recRef.current = null
      setRecording(false)
      setWavBlob(blob)
      if (previewUrl) URL.revokeObjectURL(previewUrl)
      setPreviewUrl(URL.createObjectURL(blob))
    } else {
      try {
        recRef.current = await startRecording()
        setRecording(true)
      } catch (e) {
        setStatus('Microphone permission denied.')
      }
    }
  }

  const submit = async () => {
    if (!wavBlob || !name.trim() || !phrase.trim()) return
    setBusy(true)
    setStatus('')
    try {
      await registerClone(name.trim(), wavBlob, phrase.trim())
      setStatus(`Voice "${name.trim()}" saved.`)
      setName('')
      setWavBlob(null)
      if (previewUrl) URL.revokeObjectURL(previewUrl)
      setPreviewUrl('')
      onCloned()
    } catch (e) {
      if (e instanceof AuthError) onError(e)
      else setStatus(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Clone a voice</h2>
      <div className="hint">
        Read the phrase aloud, name your voice, save it. ~10–20 seconds of clear
        audio works best.
      </div>

      <label>Phrase to read</label>
      <textarea value={phrase} onChange={(e) => setPhrase(e.target.value)} />

      <label>Recording</label>
      <div className="row">
        <button className={recording ? 'danger' : ''} onClick={toggleRecord}>
          {recording ? <><span className="rec-dot" />Stop</> : 'Record'}
        </button>
        {previewUrl && <audio src={previewUrl} controls />}
      </div>

      <label>Voice name</label>
      <div className="row">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value.replace(/[^a-zA-Z0-9_-]/g, ''))}
          placeholder="e.g. alice"
        />
        <button onClick={submit} disabled={busy || !wavBlob || !name.trim()}>
          {busy ? 'Saving…' : 'Save voice'}
        </button>
      </div>
      <div className={`status ${status.startsWith('Voice') ? 'ok' : status ? 'error' : ''}`}>
        {status}
      </div>
    </div>
  )
}

function SpeakCard({ voices, onRefresh, onError }) {
  const [text, setText] = useState('Hello from a cloned voice!')
  const [voiceKey, setVoiceKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [audioUrl, setAudioUrl] = useState('')
  const [status, setStatus] = useState('')
  const audioRef = useRef(null)

  useEffect(() => {
    if (!voiceKey && voices.length) {
      // Prefer first clone voice if any, else first builtin.
      const firstClone = voices.find((v) => v.kind === 'clone')
      setVoiceKey(toKey(firstClone || voices[0]))
    }
  }, [voices, voiceKey])

  const submit = async () => {
    const voice = voices.find((v) => toKey(v) === voiceKey)
    if (!voice || !text.trim()) return
    setBusy(true)
    setStatus('Synthesizing…')
    try {
      const blob = await speak(text.trim(), voice)
      if (audioUrl) URL.revokeObjectURL(audioUrl)
      const url = URL.createObjectURL(blob)
      setAudioUrl(url)
      setStatus('')
      // Auto-play once ready.
      setTimeout(() => audioRef.current?.play().catch(() => {}), 50)
    } catch (e) {
      if (e instanceof AuthError) onError(e)
      else setStatus(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Speak</h2>
      <div className="hint">Pick a voice and hit play.</div>

      <label>Text</label>
      <textarea value={text} onChange={(e) => setText(e.target.value)} />

      <label>Voice</label>
      <div className="row">
        <select value={voiceKey} onChange={(e) => setVoiceKey(e.target.value)}>
          {voices.length === 0 && <option value="">(no voices)</option>}
          {voices.map((v) => (
            <option key={toKey(v)} value={toKey(v)}>
              {v.kind === 'clone' ? '🎙 ' : '🔊 '}{v.name}
            </option>
          ))}
        </select>
        <button onClick={submit} disabled={busy || !voiceKey || !text.trim()}>
          {busy ? 'Working…' : 'Play'}
        </button>
        <button className="secondary" onClick={onRefresh} title="Refresh voices">↻</button>
      </div>
      {audioUrl && <audio ref={audioRef} src={audioUrl} controls />}
      <div className={`status ${status && status !== 'Synthesizing…' ? 'error' : ''}`}>{status}</div>
      {voices.length === 0 && <div className="empty">No voices yet — clone one above.</div>}
    </div>
  )
}

function toKey(v) {
  return `${v.kind}:${v.name}`
}
