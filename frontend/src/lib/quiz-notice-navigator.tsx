'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

import { registerQuizNavigator } from '@/lib/quiz-notice'

export function QuizNoticeNavigator() {
  const router = useRouter()

  useEffect(() => {
    registerQuizNavigator((notebookId) => {
      router.push(`/notebooks/${encodeURIComponent(notebookId)}`)
    })
    return () => registerQuizNavigator(null)
  }, [router])

  return null
}
