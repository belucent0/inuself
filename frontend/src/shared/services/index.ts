/**
 * API 서비스 진입점
 *
 * 사용 예:
 * import { apiService } from '@/shared/services'
 *
 * await apiService.threads.getThreads()
 * await apiService.threads.sendMessage(threadId, { content: 'Hello' })
 */

import { threadsApi } from './endpoints/threads'
import { contentsApi } from './endpoints/contents'
import { authApi } from './endpoints/auth'
import { langfuseApi } from './endpoints/langfuse'
import { httpClient } from './api/httpClient'

export const apiService = {
  threads: threadsApi,
  contents: contentsApi,
  auth: authApi,
  langfuse: langfuseApi,
}

export { httpClient }
export { threadsApi }
export { contentsApi }
export { authApi }
export { langfuseApi }
export * from './chatStreamService'

export default apiService
