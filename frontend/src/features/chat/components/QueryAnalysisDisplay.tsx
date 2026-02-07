/**
 * 쿼리 분석 결과 표시 컴포넌트 (Perplexity 스타일)
 */

import { Globe } from 'lucide-react'
import type { QueryAnalysis } from '../types'

interface QueryAnalysisDisplayProps {
  analysis: QueryAnalysis
}

export function QueryAnalysisDisplay({ analysis }: QueryAnalysisDisplayProps) {
  const hasQueries = analysis.search_queries && analysis.search_queries.length > 0

  if (!hasQueries) return null

  return (
    <div className="flex items-center gap-2 px-4 py-2 mb-3 rounded-lg border bg-blue-50 dark:bg-blue-950/20 border-blue-200 dark:border-blue-800 animate-in fade-in slide-in-from-left-2 duration-300">
      <Globe className="h-4 w-4 text-blue-500 flex-shrink-0" />
      <div className="flex-1 flex items-center gap-2 flex-wrap">
        <span className="text-sm font-medium text-blue-600 dark:text-blue-400">
          검색 쿼리:
        </span>
        <div className="flex flex-wrap gap-1.5">
          {analysis.search_queries.map((q, i) => (
            <span
              key={i}
              className="text-xs px-2 py-0.5 rounded-md bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300"
            >
              {q}
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
