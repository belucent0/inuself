import { useState, useEffect } from 'react'

export function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T) => void] {
    // 상태 초기화: localStorage에 값이 있으면 그것을 사용, 없으면 initialValue 사용
    const [storedValue, setStoredValue] = useState<T>(() => {
        if (typeof window === 'undefined') {
            return initialValue
        }
        try {
            const item = window.localStorage.getItem(key)
            return item ? JSON.parse(item) : initialValue
        } catch (error) {
            console.warn(`Error reading localStorage key "${key}":`, error)
            return initialValue
        }
    })

    // 값이 변경되면 localStorage에도 저장
    const setValue = (value: T) => {
        try {
            // value가 함수일 경우 처리 (useState와 동일한 동작)
            const valueToStore = value instanceof Function ? value(storedValue) : value

            setStoredValue(valueToStore)

            if (typeof window !== 'undefined') {
                window.localStorage.setItem(key, JSON.stringify(valueToStore))
            }
        } catch (error) {
            console.warn(`Error setting localStorage key "${key}":`, error)
        }
    }

    return [storedValue, setValue]
}
