/**
 * UploadPage - /upload 라우트
 */

import { FileUploader } from '@/features/upload'

export function UploadPage() {
  return (
    <div className="container mx-auto py-6 px-4">
      <div className="max-w-md mx-auto">
        <div className="mb-6">
          <h1 className="text-2xl font-bold">파일 업로드</h1>
          <p className="text-muted-foreground">
            오디오, 비디오, 문서 파일을 업로드하세요
          </p>
        </div>

        <FileUploader />

        <div className="mt-6 text-sm text-muted-foreground space-y-2">
          <p>
            <strong>지원 파일:</strong>
          </p>
          <ul className="list-disc list-inside space-y-1">
            <li>오디오/비디오: MP3, WAV, M4A, MP4, AVI, MKV 등</li>
            <li>문서: PDF, TXT</li>
            <li>이미지: PNG, JPG, GIF, BMP, TIFF, WebP</li>
          </ul>
        </div>
      </div>
    </div>
  )
}
