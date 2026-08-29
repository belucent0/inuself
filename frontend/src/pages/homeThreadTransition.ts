import type { AIMode } from '@/features/chat'
import type { AcceptedMessage } from '@/shared/services/chatStreamService'
import { useChatStore } from '@/shared/stores/chatStore'

export function enterAcceptedHomeThread(
  accepted: AcceptedMessage,
  content: string,
  mode: AIMode,
  navigate: (to: string) => void
): void {
  useChatStore.getState().switchThread(accepted.thread_id, [{
    message_id: accepted.user_message_id,
    role: 'user',
    content,
    timestamp: Date.now(),
    status: 'completed',
    metadata: { mode },
  }])

  navigate(`/chat/${accepted.thread_id}?messageId=${accepted.message_id}`)
}
