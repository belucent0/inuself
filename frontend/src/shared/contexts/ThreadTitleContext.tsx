/**
 * ThreadTitleContext - 스레드 제목 관리 Context
 */

import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

interface ThreadTitleContextType {
  threadTitle: string
  setThreadTitle: (title: string) => void
  isEditingTitle: boolean
  setIsEditingTitle: (editing: boolean) => void
  onEditTitle?: () => void
  onDeleteThread?: () => void
  registerHandlers: (editHandler: () => void, deleteHandler: () => void) => void
  unregisterHandlers: () => void
}

const ThreadTitleContext = createContext<ThreadTitleContextType | undefined>(undefined)

export function ThreadTitleProvider({ children }: { children: ReactNode }) {
  const [threadTitle, setThreadTitle] = useState('AI 채팅')
  const [isEditingTitle, setIsEditingTitle] = useState(false)
  const [onEditTitle, setOnEditTitle] = useState<(() => void) | undefined>()
  const [onDeleteThread, setOnDeleteThread] = useState<(() => void) | undefined>()

  const registerHandlers = useCallback((editHandler: () => void, deleteHandler: () => void) => {
    setOnEditTitle(() => editHandler)
    setOnDeleteThread(() => deleteHandler)
  }, [])

  const unregisterHandlers = useCallback(() => {
    setOnEditTitle(undefined)
    setOnDeleteThread(undefined)
  }, [])

  return (
    <ThreadTitleContext.Provider
      value={{
        threadTitle,
        setThreadTitle,
        isEditingTitle,
        setIsEditingTitle,
        onEditTitle,
        onDeleteThread,
        registerHandlers,
        unregisterHandlers,
      }}
    >
      {children}
    </ThreadTitleContext.Provider>
  )
}

export function useThreadTitle() {
  const context = useContext(ThreadTitleContext)
  if (context === undefined) {
    throw new Error('useThreadTitle must be used within a ThreadTitleProvider')
  }
  return context
}
