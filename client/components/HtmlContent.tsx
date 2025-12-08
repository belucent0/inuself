'use client'

import { useMemo } from 'react'
import { cn } from '@/lib/utils'

type HtmlContentProps = {
  content: string
  className?: string
}

/**
 * HTML 콘텐츠를 렌더링하는 재사용 가능한 컴포넌트
 * 
 * 완전한 HTML 문서(html, head, title 포함)에서 body 내용만 추출하여 렌더링합니다.
 * 이렇게 하면 탭 제목이나 페이지 레이아웃이 깨지는 것을 방지할 수 있습니다.
 * 
 * @param content - 렌더링할 HTML 텍스트 (완전한 HTML 문서 또는 body 내용)
 * @param className - 추가 CSS 클래스 (기본적으로 html-content 클래스가 적용됨)
 */
export default function HtmlContent({ content, className }: HtmlContentProps) {
  // HTML에서 body 내용만 추출
  const bodyContent = useMemo(() => {
    if (!content || !content.trim()) {
      return null
    }

    // 완전한 HTML 문서인지 확인 (<html>, <head>, <body> 태그 포함)
    const hasFullDocument = /<html[\s>]|<head[\s>]|<body[\s>]/i.test(content)
    
    if (!hasFullDocument) {
      // 이미 body 내용만 있는 경우 그대로 반환
      return content
    }

    try {
      // DOMParser를 사용하여 HTML 파싱 (브라우저 환경)
      if (typeof window !== 'undefined' && window.DOMParser) {
        const parser = new DOMParser()
        const doc = parser.parseFromString(content, 'text/html')
        
        // body 내용 추출
        const body = doc.body
        if (body) {
          return body.innerHTML
        }
      }
      
      // DOMParser를 사용할 수 없는 경우 정규식으로 fallback
      // <body> 태그 내용 추출
      const bodyMatch = content.match(/<body[^>]*>([\s\S]*)<\/body>/i)
      if (bodyMatch && bodyMatch[1]) {
        return bodyMatch[1]
      }
      
      // body 태그가 없으면 전체 내용 반환 (이미 body 내용만 있는 경우)
      return content
    } catch (error) {
      console.error('HTML 파싱 오류:', error)
      // 파싱 실패 시 원본 반환
      return content
    }
  }, [content])

  if (!bodyContent) {
    return null
  }

  return (
    <div 
      className={cn("html-content", className)}
      dangerouslySetInnerHTML={{ __html: bodyContent }}
    />
  )
}

