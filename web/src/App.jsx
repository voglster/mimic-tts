import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  AuthError,
  checkAuth,
  clearToken,
  deleteClone,
  getHealth,
  getToken,
  listVoices,
  registerClone,
  setToken,
  speak,
  transcribe,
} from './api.js'
import { startRecording } from './recorder.js'

const DEFAULT_PHRASE =
  "The quick brown fox jumps over the lazy dog while the moon hangs low in the sky."

export default function App() {
  const [authed, setAuthed] = useState(false)
  const [voices, setVoices] = useState([])
  const [error, setError] = useState('')
  const [sttEnabled, setSttEnabled] = useState(false)

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
    // Pull feature flags from /health (unauth) so the UI knows whether to
    // show STT bits.
    getHealth()
      .then((h) => setSttEnabled(Boolean(h.stt_enabled)))
      .catch(() => {})
  }, [])

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

      <CloneCard onCloned={refreshVoices} onError={handleError} sttEnabled={sttEnabled} />
      <SpeakCard
        voices={voices}
        onRefresh={refreshVoices}
        onError={handleError}
      />
      <ManageClonesCard
        voices={voices}
        onChanged={refreshVoices}
        onError={handleError}
      />
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

function CloneCard({ onCloned, onError, sttEnabled }) {
  const [phrase, setPhrase] = useState(DEFAULT_PHRASE)
  const [name, setName] = useState('')
  const [recording, setRecording] = useState(false)
  const [previewUrl, setPreviewUrl] = useState('')
  const [wavBlob, setWavBlob] = useState(null)
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const recRef = useRef(null)
  const fileRef = useRef(null)

  const setAudio = (blob) => {
    setWavBlob(blob)
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setPreviewUrl(URL.createObjectURL(blob))
  }

  const toggleRecord = async () => {
    setStatus('')
    if (recording) {
      const blob = await recRef.current.stop()
      recRef.current = null
      setRecording(false)
      setAudio(blob)
    } else {
      try {
        recRef.current = await startRecording()
        setRecording(true)
      } catch (e) {
        setStatus('Microphone permission denied.')
      }
    }
  }

  const onFile = (e) => {
    const file = e.target.files?.[0]
    if (file) setAudio(file)
    e.target.value = ''
  }

  const doTranscribe = async () => {
    if (!wavBlob) return
    setTranscribing(true)
    setStatus('Transcribing…')
    try {
      const text = await transcribe(wavBlob)
      if (text.trim()) {
        setPhrase(text.trim())
        setStatus('Transcribed.')
      } else {
        setStatus('Transcript was empty.')
      }
    } catch (e) {
      if (e instanceof AuthError) onError(e)
      else setStatus(String(e.message || e))
    } finally {
      setTranscribing(false)
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
        Record yourself reading the phrase, or upload an audio file. ~10–20
        seconds of clear audio works best.
        {sttEnabled && ' Hit Transcribe to fill the phrase from your recording.'}
      </div>

      <label>
        Phrase the recording says{' '}
        <span style={{ textTransform: 'none', color: 'var(--muted)' }}>
          (must match the audio)
        </span>
      </label>
      <textarea value={phrase} onChange={(e) => setPhrase(e.target.value)} />

      <label>Audio</label>
      <div className="row">
        <button className={recording ? 'danger' : ''} onClick={toggleRecord}>
          {recording ? <><span className="rec-dot" />Stop</> : 'Record'}
        </button>
        <button className="secondary" onClick={() => fileRef.current?.click()} disabled={recording}>
          Upload
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="audio/*"
          style={{ display: 'none' }}
          onChange={onFile}
        />
        {sttEnabled && (
          <button
            className="secondary"
            onClick={doTranscribe}
            disabled={!wavBlob || transcribing || recording}
            title="Fill the phrase from the recording"
          >
            {transcribing ? '…' : 'Transcribe'}
          </button>
        )}
      </div>
      {previewUrl && <audio src={previewUrl} controls />}

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

function ManageClonesCard({ voices, onChanged, onError }) {
  const [busyName, setBusyName] = useState('')
  const [status, setStatus] = useState('')
  const clones = voices.filter((v) => v.kind === 'clone')

  const onDelete = async (name) => {
    if (!window.confirm(`Delete cloned voice "${name}"? This can't be undone.`)) return
    setBusyName(name)
    setStatus('')
    try {
      await deleteClone(name)
      setStatus(`Deleted "${name}".`)
      onChanged()
    } catch (e) {
      if (e instanceof AuthError) onError(e)
      else setStatus(String(e.message || e))
    } finally {
      setBusyName('')
    }
  }

  if (clones.length === 0) return null

  return (
    <div className="card">
      <h2>Cloned voices</h2>
      <div className="hint">Trash a clone you don't want anymore.</div>
      <ul className="voice-list">
        {clones.map((v) => (
          <li key={v.name}>
            <span>🎙 {v.name}</span>
            <button
              className="danger"
              onClick={() => onDelete(v.name)}
              disabled={busyName === v.name}
            >
              {busyName === v.name ? '…' : 'Delete'}
            </button>
          </li>
        ))}
      </ul>
      {status && (
        <div className={`status ${status.startsWith('Deleted') ? 'ok' : 'error'}`}>
          {status}
        </div>
      )}
    </div>
  )
}

function toKey(v) {
  return `${v.kind}:${v.name}`
}
