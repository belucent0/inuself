import Link from 'next/link'
import { FileQuestion } from 'lucide-react'
import { Button } from '@/components/ui/button'

export default function NotFound() {
    return (
        <div className="flex h-[calc(100vh-4rem)] flex-col items-center justify-center gap-4 text-center">
            <div className="rounded-full bg-muted p-4">
                <FileQuestion className="h-12 w-12 text-muted-foreground" />
            </div>
            <div className="space-y-2">
                <h1 className="text-3xl font-bold tracking-tight">페이지를 찾을 수 없습니다</h1>
                <p className="text-muted-foreground">
                    요청하신 페이지가 존재하지 않거나 이동되었을 수 있습니다.
                </p>
            </div>
            <div className="flex gap-2">
                <Button asChild variant="default">
                    <Link href="/" className="text-white">
                        홈으로 이동
                    </Link>
                </Button>
            </div>
        </div>
    )
}
