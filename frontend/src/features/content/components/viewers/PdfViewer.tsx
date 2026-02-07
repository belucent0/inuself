/**
 * PDF 뷰어 (iframe 기반)
 * 향후 react-pdf로 교체하여 줌/페이지네이션 지원 가능
 */

interface PdfViewerProps {
  src: string
}

export function PdfViewer({ src }: PdfViewerProps) {
  return (
    <iframe
      src={src}
      className="w-full h-96 rounded-lg border"
      title="PDF Viewer"
    />
  )
}
