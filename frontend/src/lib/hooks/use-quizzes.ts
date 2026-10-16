import { useQuery } from '@tanstack/react-query'

import { quizzesApi } from '@/lib/api/quizzes'
import { QUERY_KEYS } from '@/lib/api/query-client'

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
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status === 'new' || status === 'running') {
        return 2000
      }
      return false
    },
  })
}
