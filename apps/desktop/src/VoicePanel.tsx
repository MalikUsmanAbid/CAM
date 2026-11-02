import { useEffect, useRef, useState } from 'react'
import { getSessionEpoch, transcribeCapture, voiceStatus } from './api'

function wav(samples: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const label = (offset: number, value: string) => { for (let index = 0; index < value.length; index++) view.setUint8(offset + index, value.charCodeAt(index)) }
  label(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); label(8, 'WAVE'); label(12, 'fmt ')
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true)
  label(36, 'data'); view.setUint32(40, samples.length * 2, true)
  for (let index = 0; index < samples.length; index++) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    view.setInt16(44 + index * 2, sample < 0 ? sample * 32768 : sample * 32767, true)
  }
  return buffer
}

export default function VoicePanel() {
  const [available, setAvailable] = useState(false)
  const [recording, setRecording] = useState(false)
  const [busy, setBusy] = useState(false)
  const [holding, setHolding] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [transcript, setTranscript] = useState('')
  const [status, setStatus] = useState('Microphone off')
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const generation = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const pressed = useRef(false)

  function stop(discard = false) {
    pressed.current = false
    setHolding(false)
    if (discard) { generation.current++; controller.current?.abort() }
    clearTimeout(timer.current)
    if (recorder.current?.state === 'recording') recorder.current.stop()
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    setRecording(false)
    setStatus(discard ? 'Capture discarded; microphone off' : 'Microphone off')
    if (discard) setBusy(false)
  }

  useEffect(() => {
    const captureGeneration = generation
    const request = new AbortController()
    void voiceStatus(request.signal).then((value) => { if (!request.signal.aborted) setAvailable(value.available) })
      .catch(() => { if (!request.signal.aborted) setAvailable(false) })
    const onHidden = () => { if (document.hidden) stop(true) }
    document.addEventListener('visibilitychange', onHidden)
    return () => {
      request.abort(); captureGeneration.current++; pressed.current = false
      clearTimeout(timer.current); controller.current?.abort()
      if (recorder.current?.state === 'recording') recorder.current.stop()
      stream.current?.getTracks().forEach((track) => track.stop())
      window.speechSynthesis?.cancel()
      document.removeEventListener('visibilitychange', onHidden)
    }
  }, [])

  async function start() {
    if (busy || recording || pressed.current || !available) return
    pressed.current = true
    setHolding(true)
    const own = ++generation.current
    const epoch = getSessionEpoch()
    setBusy(true); setError(null); setTranscript(''); setStatus('Requesting microphone permission')
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 }, video: false })
      if (generation.current !== own || !pressed.current || epoch !== getSessionEpoch()) {
        media.getTracks().forEach((track) => track.stop())
        if (generation.current === own && epoch === getSessionEpoch()) setBusy(false)
        return
      }
      stream.current = media
      const capture = new MediaRecorder(media)
      const chunks: Blob[] = []
      let capturedBytes = 0
      capture.ondataavailable = (event) => {
        if (generation.current !== own || epoch !== getSessionEpoch()) return
        capturedBytes += event.data.size
        if (capturedBytes > 2000000) { stop(true); setError('Capture exceeded its memory limit.'); return }
        if (event.data.size) chunks.push(event.data)
      }
      capture.onstop = () => {
        media.getTracks().forEach((track) => track.stop())
        if (generation.current !== own || epoch !== getSessionEpoch()) return
        setBusy(true); setStatus('Transcribing through configured local service')
        controller.current = new AbortController()
        const signal = controller.current.signal
        void (async () => {
          const decoding = new AudioContext()
          let decoded: AudioBuffer
          try { decoded = await decoding.decodeAudioData(await new Blob(chunks, { type: capture.mimeType }).arrayBuffer()) }
          finally { await decoding.close() }
          if (decoded.duration > 20.1 || decoded.numberOfChannels > 2) throw new Error('Capture exceeds twenty seconds.')
          const frames = Math.min(320000, Math.ceil(decoded.duration * 16000))
          const rendering = new OfflineAudioContext(1, frames, 16000)
          const source = rendering.createBufferSource(); source.buffer = decoded; source.connect(rendering.destination); source.start()
          const mono = await rendering.startRendering()
          if (generation.current !== own || signal.aborted || epoch !== getSessionEpoch()) return
          const result = await transcribeCapture(wav(mono.getChannelData(0)), signal)
          if (generation.current !== own || epoch !== getSessionEpoch()) return
          if (result.status === 'approval_required') {
            setStatus('Transcription approval is waiting in Approvals; pending audio expires after two minutes.')
          } else if (result.status === 'success' && typeof result.result?.text === 'string') {
            setTranscript(result.result.text); setStatus('Transcript ready; microphone off')
          } else throw new Error(result.detail ?? 'Transcription did not succeed.')
        })().catch((reason: unknown) => {
          if (generation.current === own && epoch === getSessionEpoch()) setError(reason instanceof Error ? reason.message : 'Transcription failed.')
        }).finally(() => { if (generation.current === own && epoch === getSessionEpoch()) setBusy(false) })
      }
      recorder.current = capture
      capture.start(250); setRecording(true); setBusy(false); setStatus('Microphone active — release to stop')
      timer.current = setTimeout(() => stop(), 19000)
    } catch {
      if (generation.current === own) { stop(true); setError('Microphone capture is unavailable or permission was denied.') }
    }
  }

  return <section className="surface"><h2>Push-to-talk</h2>
    <p>No wake word or background microphone. Audio is held in memory and sent only to the explicitly configured loopback transcription service.</p>
    {!available && <p>Local transcription is not configured. Microphone capture is disabled.</p>}
    {error && <p role="alert">{error}</p>}
    <p role="status">{status}</p>
    <button className="button primary" disabled={!available || (busy && !holding)} aria-pressed={recording}
      onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); void start() }}
      onPointerUp={() => stop()} onPointerCancel={() => stop(true)}
      onKeyDown={(event) => { if (event.code === 'Space' && !event.repeat) { event.preventDefault(); void start() } }}
      onKeyUp={(event) => { if (event.code === 'Space') { event.preventDefault(); stop() } }}
      onBlur={() => { if (pressed.current) stop(true) }}>Hold to talk</button>
    <button className="button subtle" onClick={() => { stop(true); window.speechSynthesis?.cancel() }}>Stop and discard</button>
    <label>Transcript — review before using<textarea value={transcript} maxLength={4000} onChange={(event) => setTranscript(event.target.value)} /></label>
    <button className="button subtle" disabled={!transcript} onClick={() => {
      const local = window.speechSynthesis?.getVoices().find((voice) => voice.localService)
      if (!local) { setError('No installed local speech voice is available.'); return }
      window.speechSynthesis.cancel()
      const utterance = new SpeechSynthesisUtterance(transcript); utterance.voice = local
      window.speechSynthesis.speak(utterance)
    }}>Read with installed local voice</button>
  </section>
}
