import ContentList from '@/components/ContentList'
import DeleteQueuedButton from '@/components/DeleteQueuedButton'
import { listContents } from '@/lib/api'

export const dynamic = 'force-dynamic'

export default async function ContentsPage() {
  const contents = await listContents()
  return (
    <section>
      <h2>전사된 콘텐츠</h2>
      <DeleteQueuedButton />
      <ContentList contents={contents} />
    </section>
  )
}


