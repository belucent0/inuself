import { notFound } from 'next/navigation'
import { Metadata } from 'next'
import ContentDetail from '@/components/ContentDetail'
import { getContentDetail } from '@/lib/api'

type Props = {
  params: Promise<{ id: string }>
}

export const dynamic = 'force-dynamic'

// 탭 제목을 명시적으로 설정하여 HTML 내부의 title 태그가 덮어쓰지 못하도록 함
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params
  try {
    const content = await getContentDetail(id)
    const title = content?.title || content?.filename || '콘텐츠 상세'
    return {
      title: `${title} - ASR 파이프라인`,
    }
  } catch {
    return {
      title: '콘텐츠 상세 - ASR 파이프라인',
    }
  }
}

export default async function ContentDetailPage({ params }: Props) {
  const { id } = await params
  const content = await getContentDetail(id)

  if (!content) {
    notFound()
  }

  return <ContentDetail content={content} />
}

