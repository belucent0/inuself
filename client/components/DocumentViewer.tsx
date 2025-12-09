'use client'

import { useState, useEffect, useRef } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import { renderAsync } from 'docx-preview'
import { ZoomIn, ZoomOut, RotateCcw, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import 'react-pdf/dist/esm/Page/AnnotationLayer.css'
import 'react-pdf/dist/esm/Page/TextLayer.css'

// PDF.js 워커 설정 (온프레미스 환경 지원 - 모든 리소스를 로컬에서 제공)
if (typeof window !== 'undefined') {
  // public 폴더의 워커 파일 사용 (CDN 의존성 없음)
  // 워커 파일은 npm install 시 자동으로 public/로 복사됨
  pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs'
}

type DocumentViewerProps = {
  fileUrl: string
  filename: string
  isPdf: boolean
  isDocx: boolean
}

export default function DocumentViewer({ fileUrl, filename, isPdf, isDocx }: DocumentViewerProps) {
  const [numPages, setNumPages] = useState<number>(0)
  const [pageNumber, setPageNumber] = useState<number>(1)
  const [scale, setScale] = useState<number>(1.0)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)
  const docxContainerRef = useRef<HTMLDivElement>(null)

  // PDF 로드 완료 핸들러
  const onDocumentLoadSuccess = ({ numPages }: { numPages: number }) => {
    console.log('PDF 로드 성공:', numPages, '페이지')
    setNumPages(numPages)
    setLoading(false)
    setError(null)
  }

  // PDF 로드 실패 핸들러
  const onDocumentLoadError = (error: Error) => {
    console.error('PDF 로드 오류:', error)
    console.error('파일 URL:', fileUrl)
    // 에러 메시지 상세화
    let errorMessage = 'PDF 파일을 로드할 수 없습니다.'
    if (error.message) {
      errorMessage += ` (${error.message})`
    }
    setError(errorMessage)
    setLoading(false)
  }

  // DOCX 렌더링 및 페이지 감지
  useEffect(() => {
    if (isDocx && docxContainerRef.current && fileUrl) {
      setLoading(true)
      setError(null)

      fetch(fileUrl)
        .then((response) => response.blob())
        .then((blob) => {
          if (docxContainerRef.current) {
            renderAsync(blob, docxContainerRef.current, undefined, {
              className: 'docx-wrapper',
              inWrapper: true,
              ignoreWidth: false,
              ignoreHeight: false,
              ignoreFonts: false,
              breakPages: true,
              ignoreLastRenderedPageBreak: false,  // 페이지 브레이크 감지를 위해 false로 변경
              experimental: false,
              trimXmlDeclaration: true,
              useBase64URL: false,
              useMathMLPolyfill: true,
            })
              .then(() => {
                setLoading(false)

                // 렌더링 완료 후 페이지 감지
                setTimeout(() => {
                  if (docxContainerRef.current) {
                    // docx-preview가 생성하는 section 요소들을 페이지로 간주
                    let sections = docxContainerRef.current.querySelectorAll('.docx-wrapper > section')

                    // 직접 자식이 아닐 경우 대체 셀렉터 사용
                    if (sections.length === 0) {
                      sections = docxContainerRef.current.querySelectorAll('section')
                      console.log('페이지 감지: 대체 셀렉터 사용')
                    }

                    if (sections.length > 0) {
                      console.log(`DOCX 페이지 감지: ${sections.length}개 페이지`)
                      setNumPages(sections.length)
                      setPageNumber(1)

                      // 각 섹션에 페이지 번호 데이터 속성 추가
                      sections.forEach((section, index) => {
                        (section as HTMLElement).setAttribute('data-page-number', String(index + 1))
                      })
                    } else {
                      // section이 없으면 전체를 1페이지로 간주
                      console.log('DOCX 페이지 구분 없음, 전체를 1페이지로 표시')
                      setNumPages(1)
                      setPageNumber(1)
                    }
                  }
                }, 100)  // 렌더링 완료 후 약간의 지연
              })
              .catch((err) => {
                console.error('DOCX 렌더링 오류:', err)
                setError('DOCX 파일을 렌더링할 수 없습니다.')
                setLoading(false)
              })
          }
        })
        .catch((err) => {
          console.error('DOCX 파일 로드 오류:', err)
          setError('DOCX 파일을 로드할 수 없습니다.')
          setLoading(false)
        })
    }
  }, [isDocx, fileUrl])

  // DOCX 페이지 표시/숨김 처리
  useEffect(() => {
    if (isDocx && docxContainerRef.current && numPages > 0) {
      console.log('페이지 표시/숨김 처리 시작:', { pageNumber, numPages })

      // 여러 가능한 셀렉터 시도
      let sections = docxContainerRef.current.querySelectorAll('.docx-wrapper > section')

      if (sections.length === 0) {
        // section이 직접 자식이 아닐 수 있음
        sections = docxContainerRef.current.querySelectorAll('section')
        console.log('대체 셀렉터 사용, 찾은 섹션 수:', sections.length)
      }

      if (sections.length > 0) {
        console.log(`총 ${sections.length}개 섹션 발견, 현재 페이지: ${pageNumber}`)

        // 모든 섹션을 순회하며 현재 페이지만 표시
        sections.forEach((section, index) => {
          const htmlSection = section as HTMLElement
          const shouldShow = index + 1 === pageNumber

          console.log(`섹션 ${index + 1}: ${shouldShow ? '표시' : '숨김'}`)

          if (shouldShow) {
            htmlSection.style.display = 'block'
            htmlSection.style.visibility = 'visible'
          } else {
            htmlSection.style.display = 'none'
            htmlSection.style.visibility = 'hidden'
          }
        })
      } else {
        console.warn('섹션을 찾을 수 없습니다. DOM 구조:', docxContainerRef.current.innerHTML.substring(0, 500))
      }
    }
  }, [isDocx, pageNumber, numPages])

  // 줌 인
  const handleZoomIn = () => {
    setScale((prev) => Math.min(prev + 0.2, 3.0))
  }

  // 줌 아웃
  const handleZoomOut = () => {
    setScale((prev) => Math.max(prev - 0.2, 0.5))
  }

  // 줌 리셋
  const handleZoomReset = () => {
    setScale(1.0)
  }

  // 이전 페이지
  const handlePrevPage = () => {
    setPageNumber((prev) => Math.max(1, prev - 1))
  }

  // 다음 페이지
  const handleNextPage = () => {
    setPageNumber((prev) => Math.min(numPages, prev + 1))
  }

  // 페이지 번호 입력
  const handlePageInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseInt(e.target.value)
    if (!isNaN(value) && value >= 1 && value <= numPages) {
      setPageNumber(value)
    }
  }

  if (isPdf) {
    return (
      <div className="w-full flex flex-col">
        {/* 컨트롤 바 */}
        <div className="flex items-center justify-between gap-2 p-2 bg-muted/50 border-b rounded-t-lg">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleZoomOut}
              disabled={scale <= 0.5}
              aria-label="줌 아웃"
            >
              <ZoomOut className="h-4 w-4" />
            </Button>
            <span className="text-sm font-medium min-w-[60px] text-center">
              {Math.round(scale * 100)}%
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleZoomIn}
              disabled={scale >= 3.0}
              aria-label="줌 인"
            >
              <ZoomIn className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleZoomReset}
              aria-label="줌 리셋 (100%)"
              title="줌 리셋 (100%)"
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          </div>

          {numPages > 0 && (
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handlePrevPage}
                disabled={pageNumber <= 1}
                aria-label="이전 페이지"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min={1}
                  max={numPages}
                  value={pageNumber}
                  onChange={handlePageInput}
                  className="w-16 h-8 text-center text-sm"
                />
                <span className="text-sm text-muted-foreground">/ {numPages}</span>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleNextPage}
                disabled={pageNumber >= numPages}
                aria-label="다음 페이지"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </div>

        {/* PDF 뷰어 */}
        <div className="flex-1 overflow-auto border-x bg-muted/30 flex justify-center p-4 min-h-[600px]">
          {error ? (
            <div className="flex items-center justify-center h-[600px]">
              <div className="text-center">
                <p className="text-destructive mb-2">{error}</p>
                <p className="text-xs text-muted-foreground">파일 URL: {fileUrl}</p>
                <p className="text-xs text-muted-foreground mt-2">브라우저 콘솔을 확인해주세요.</p>
              </div>
            </div>
          ) : (
            <Document
              file={fileUrl}
              onLoadSuccess={onDocumentLoadSuccess}
              onLoadError={onDocumentLoadError}
              loading={
                <div className="flex items-center justify-center h-[600px]">
                  <div className="text-center">
                    <p className="text-muted-foreground">PDF 로딩 중...</p>
                    <p className="text-xs text-muted-foreground mt-2">파일 URL: {fileUrl}</p>
                  </div>
                </div>
              }
              error={
                <div className="flex items-center justify-center h-[600px]">
                  <div className="text-center">
                    <p className="text-destructive mb-2">PDF 파일을 로드할 수 없습니다.</p>
                    <p className="text-xs text-muted-foreground">파일 URL: {fileUrl}</p>
                    <p className="text-xs text-muted-foreground mt-2">브라우저 콘솔을 확인해주세요.</p>
                  </div>
                </div>
              }
              options={{
                // 온프레미스 환경 지원: 모든 리소스를 로컬(public 폴더)에서 제공
                // cMapUrl과 standardFontDataUrl은 선택사항 (대부분의 PDF는 없어도 동작)
                // 실제로 파일을 복사하지 않으므로 설정하지 않음
                // 특수 폰트가 있는 PDF에서 문제가 발생하면 Dockerfile의 주석을 해제하여 복사
                httpHeaders: {},
                withCredentials: false,
              }}
            >
              {numPages > 0 && pageNumber >= 1 && pageNumber <= numPages && (
                <Page
                  key={`page-${pageNumber}-${scale}`}
                  pageNumber={pageNumber}
                  scale={scale}
                  className="shadow-lg"
                  renderTextLayer={true}
                  renderAnnotationLayer={true}
                  loading={
                    <div className="flex items-center justify-center h-[400px]">
                      <p className="text-muted-foreground">페이지 로딩 중...</p>
                    </div>
                  }
                  onRenderError={(error) => {
                    console.error('페이지 렌더링 오류:', error)
                  }}
                />
              )}
            </Document>
          )}
        </div>
      </div>
    )
  }

  if (isDocx) {
    return (
      <div className="w-full flex flex-col">
        {/* 컨트롤 바 */}
        <div className="flex items-center justify-between gap-2 p-2 bg-muted/50 border-b rounded-t-lg">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleZoomOut}
              disabled={scale <= 0.5}
              aria-label="줌 아웃"
            >
              <ZoomOut className="h-4 w-4" />
            </Button>
            <span className="text-sm font-medium min-w-[60px] text-center">
              {Math.round(scale * 100)}%
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleZoomIn}
              disabled={scale >= 3.0}
              aria-label="줌 인"
            >
              <ZoomIn className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleZoomReset}
              aria-label="줌 리셋 (100%)"
              title="줌 리셋 (100%)"
            >
              <RotateCcw className="h-4 w-4" />
            </Button>
          </div>

          {numPages > 0 && (
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handlePrevPage}
                disabled={pageNumber <= 1}
                aria-label="이전 페이지"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min={1}
                  max={numPages}
                  value={pageNumber}
                  onChange={handlePageInput}
                  className="w-16 h-8 text-center text-sm"
                />
                <span className="text-sm text-muted-foreground">/ {numPages}</span>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleNextPage}
                disabled={pageNumber >= numPages}
                aria-label="다음 페이지"
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          )}
        </div>

        {/* DOCX 뷰어 */}
        <div className="flex-1 overflow-auto border-x bg-muted/30 flex justify-center items-start px-4 py-0">
          {loading && (
            <div className="flex items-center justify-center h-[600px]">
              <p className="text-muted-foreground">DOCX 로딩 중...</p>
            </div>
          )}
          {error && (
            <div className="flex items-center justify-center h-[600px]">
              <p className="text-destructive">{error}</p>
            </div>
          )}
          <div
            ref={docxContainerRef}
            className={cn(
              'docx-container',
              loading && 'hidden'
            )}
            style={{
              maxWidth: '800px',
              width: '100%',
              margin: '0 auto',
              transform: `scale(${scale})`,  // 세로 비율 조정은 CSS로 이동
              transformOrigin: 'top center',
              transition: 'transform 0.2s ease-in-out',
            }}
          />
        </div>
      </div>
    )
  }

  return null
}

