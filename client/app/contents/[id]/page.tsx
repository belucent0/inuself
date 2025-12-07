import ContentDetail from '@/components/ContentDetail'
import { getContentDetail } from '@/lib/api'
import PageHeader from '@/components/PageHeader'

type Props = {
  params: { id: string }
}

export const dynamic = 'force-dynamic'

export default async function ContentDetailPage({ params }: Props) {
  const content = await getContentDetail(params.id)
  const breadcrumbItems = [
    { label: '홈', href: '/' },
    { label: '전사된 콘텐츠', href: '/contents' },
    { label: '콘텐츠 상세' },
  ]

  return (
    <div>
      <PageHeader items={breadcrumbItems} />
      <ContentDetail content={content} />
    </div>
  )
}


