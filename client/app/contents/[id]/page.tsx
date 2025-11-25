import ContentDetail from '@/components/ContentDetail'
import { getContentDetail } from '@/lib/api'

type Props = {
  params: { id: string }
}

export const dynamic = 'force-dynamic'

export default async function ContentDetailPage({ params }: Props) {
  const content = await getContentDetail(params.id)
  return (
    <section>
      <ContentDetail content={content} />
    </section>
  )
}


