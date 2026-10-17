import { useQuery } from '@tanstack/react-query'

import { quizzesApi } from '@/lib/api/quizzes'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { quizRefetchIntervalMs } from '@/lib/types/quiz'

export function useNotebookQuizzes(notebookId: string) {
  return useQuery({
    queryKey: QUERY_KEYS.quizzes(notebookId),
    queryFn: () => quizzesApi.list(notebookId),
    enabled: Boolean(notebookId),
  })
}

export function useQuiz(quizId: string | null, attemptId: string | null) {
  return useQuery({
    queryKey: QUERY_KEYS.quiz(quizId ?? '', attemptId),
    queryFn: () => quizzesApi.get(quizId as string, attemptId),
    enabled: Boolean(quizId),
    refetchInterval: (query) => quizRefetchIntervalMs(query.state.data?.status),
  })
}
