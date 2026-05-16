// Browser mic → compressed blob via MediaRecorder.
//
// We hand whatever container/codec the browser natively produces (Chrome /
// Firefox: audio/webm;codecs=opus, Safari iOS/macOS: audio/mp4 AAC) directly
// to the server. The server runs ffmpeg to transcode to the 24 kHz mono WAV
// the TTS backend wants. This keeps mobile uploads tiny (~10× smaller than
// raw WAV) and avoids a WASM encoder on the client.

// Mime types we'll try, in preference order. The first one MediaRecorder
// accepts wins. Empty string at the end means "let the browser pick" — that's
// what Safari needs (it ignores all explicit mimeType requests).
const CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4;codecs=mp4a.40.2',
  'audio/mp4',
  '',
]

function pickMimeType() {
  if (typeof MediaRecorder === 'undefined') return null
  for (const t of CANDIDATES) {
    if (t === '' || MediaRecorder.isTypeSupported(t)) return t
  }
  return null
}

export async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
    },
  })

  const mimeType = pickMimeType()
  if (mimeType === null) {
    stream.getTracks().forEach((t) => t.stop())
    throw new Error('MediaRecorder is not supported in this browser.')
  }
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
  const chunks = []
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data)
  }

  const done = new Promise((resolve) => {
    recorder.onstop = () => {
      // recorder.mimeType is the canonical type the browser actually used,
      // including any codecs= parameter — preserve it on the blob so the
      // server's ffmpeg has the strongest possible hint.
      const type = recorder.mimeType || mimeType || 'audio/webm'
      resolve(new Blob(chunks, { type }))
    }
  })

  recorder.start()
  return {
    mimeType: recorder.mimeType || mimeType,
    async stop() {
      if (recorder.state !== 'inactive') recorder.stop()
      const blob = await done
      stream.getTracks().forEach((t) => t.stop())
      return blob
    },
  }
}
