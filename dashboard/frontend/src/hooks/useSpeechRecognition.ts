import { useCallback, useEffect, useRef, useState } from 'react'

// Tipos para Web Speech API (não incluída nos lib padrão do TS)
interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string
}

interface SpeechRecognitionInstance extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((e: SpeechRecognitionEvent) => void) | null
  onerror: ((e: SpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance

function getSpeechRecognitionClass(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  return (
    (window as Window & { SpeechRecognition?: SpeechRecognitionConstructor }).SpeechRecognition ??
    (window as Window & { webkitSpeechRecognition?: SpeechRecognitionConstructor }).webkitSpeechRecognition ??
    null
  )
}

export interface UseSpeechRecognitionOptions {
  onFinalResult: (text: string) => void
  onPermissionDenied: () => void
}

export interface UseSpeechRecognitionReturn {
  isSupported: boolean
  isListening: boolean
  start: () => void
  stop: () => void
}

export function useSpeechRecognition({
  onFinalResult,
  onPermissionDenied,
}: UseSpeechRecognitionOptions): UseSpeechRecognitionReturn {
  const SpeechRecognitionClass = getSpeechRecognitionClass()
  const isSupported = SpeechRecognitionClass !== null

  const [isListening, setIsListening] = useState(false)
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)
  const onFinalResultRef = useRef(onFinalResult)
  const onPermissionDeniedRef = useRef(onPermissionDenied)

  // Manter refs atualizadas sem recriar a instância
  useEffect(() => {
    onFinalResultRef.current = onFinalResult
  }, [onFinalResult])

  useEffect(() => {
    onPermissionDeniedRef.current = onPermissionDenied
  }, [onPermissionDenied])

  // Criar instância única de reconhecimento
  useEffect(() => {
    if (!SpeechRecognitionClass) return

    const recognition = new SpeechRecognitionClass()
    recognition.lang = 'pt-BR'
    recognition.continuous = false
    recognition.interimResults = false

    recognition.onresult = (e: SpeechRecognitionEvent) => {
      const transcript = Array.from(e.results)
        .filter((r) => r.isFinal)
        .map((r) => r[0].transcript.trim())
        .filter(Boolean)
        .join(' ')
      if (transcript) {
        onFinalResultRef.current(transcript)
      }
    }

    recognition.onerror = (e: SpeechRecognitionErrorEvent) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
        onPermissionDeniedRef.current()
      } else {
        console.warn('[useSpeechRecognition] error:', e.error)
      }
      setIsListening(false)
    }

    recognition.onend = () => {
      setIsListening(false)
    }

    recognitionRef.current = recognition

    return () => {
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      try { recognition.abort() } catch { /* noop */ }
      recognitionRef.current = null
    }
  }, [SpeechRecognitionClass])

  const start = useCallback(() => {
    if (!recognitionRef.current || isListening) return
    try {
      recognitionRef.current.start()
      setIsListening(true)
    } catch (e) {
      console.warn('[useSpeechRecognition] start error:', e)
    }
  }, [isListening])

  const stop = useCallback(() => {
    if (!recognitionRef.current || !isListening) return
    try {
      recognitionRef.current.stop()
    } catch (e) {
      console.warn('[useSpeechRecognition] stop error:', e)
    }
    setIsListening(false)
  }, [isListening])

  return { isSupported, isListening, start, stop }
}
