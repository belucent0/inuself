/**
 * 이미지 뷰어
 */

interface ImageViewerProps {
  src: string
  alt: string
}

export function ImageViewer({ src, alt }: ImageViewerProps) {
  return (
    <div className="flex justify-center">
      <img
        src={src}
        alt={alt}
        className="max-w-full max-h-80 object-contain rounded-lg"
      />
    </div>
  )
}
