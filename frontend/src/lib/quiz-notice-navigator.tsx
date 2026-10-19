'use client'

import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

import { useTranslation } from '@/lib/hooks/use-translation'
import { registerQuizNavigator, resumeQuizNotices } from '@/lib/quiz-notice'

export function QuizNoticeNavigator() {
  const router = useRouter()
  const { t } = useTranslation()

  useEffect(() => {
    registerQuizNavigator((notebookId) => {
      router.push(`/notebooks/${encodeURIComponent(notebookId)}`)
    })
    resumeQuizNotices({
      running: t('quiz.noticeBackground'),
      ready: t('quiz.noticeReady'),
      openLabel: t('quiz.noticeOpen'),
      failed: t('quiz.failed'),
    })
    return () => registerQuizNavigator(null)
  }, [router, t])

  return null
}
