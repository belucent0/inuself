'use client'

import { useEffect } from 'react'
import { AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function Error({
    error,
    reset,
}: {
    error: Error & { digest?: string }
    reset: () => void
}) {
    useEffect(() => {
        console.error(error)
    }, [error])

    return (
        <div className="flex h-[calc(100vh-4rem)] flex-col items-center justify-center gap-4 text-center">
            <div className="rounded-full bg-destructive/10 p-4">
                <AlertCircle className="h-12 w-12 text-destructive" />
            </div>
            <div className="space-y-2">
                <h1 className="text-3xl font-bold tracking-tight">오류가 발생했습니다</h1>
                <p className="text-muted-foreground">
                    문제가 지속되면 관리자에게 문의해주세요.
                </p>
                <p className="text-sm text-muted-foreground font-mono bg-muted px-2 py-1 rounded">
                    {error.digest && <span>Error ID: {error.digest}</span>}
                </p>
            </div>
            <div className="flex gap-2">
                <Button onClick={() => reset()} variant="outline">
                    다시 시도
                </Button>
            </div>
        </div>
    )
}
