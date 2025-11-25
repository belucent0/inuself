import Link from 'next/link'
import UploadForm from './UploadForm'

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div>
        <h1>ASR 파이프라인</h1>
        <p>전사/화자분리 작업 현황을 확인하세요.</p>
        <nav>
          <Link href="/contents">전사된 콘텐츠</Link>
        </nav>
      </div>
      <UploadForm />
    </aside>
  )
}


