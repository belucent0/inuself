import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Legend,
  ReferenceLine,
} from "recharts"
import type { ChartConfig } from "@/shared/components/ui/chart"
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/shared/components/ui/chart"
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui/card"
import { useMediaQuery } from "@/shared/hooks/useMediaQuery"

// WPI 유형 매핑 (I-Test ↔ Me-Test 대응)
const WPI_AXIS_PAIRS = [
  { iType: "Realist", meType: "Relation" },
  { iType: "Romanticist", meType: "Trust" },
  { iType: "Humanist", meType: "Manual" },
  { iType: "Idealist", meType: "Self" },
  { iType: "Agent", meType: "Culture" },
] as const

interface WpiScores {
  Realist: number
  Romanticist: number
  Humanist: number
  Idealist: number
  Agent: number
}

interface MeScores {
  Relation: number
  Trust: number
  Manual: number
  Self: number
  Culture: number
}

interface WpiResultChartProps {
  iTestScores: WpiScores
  meTestScores: MeScores
  iTestDominant?: string
  meTestDominant?: string
}

const chartConfig = {
  iTest: {
    label: "자기평가 (I-Test)",
    color: "hsl(0, 70%, 50%)", // 빨간색 (차트 선)
  },
  meTest: {
    label: "타인평가 (Me-Test)",
    color: "hsl(220, 70%, 50%)", // 파란색 (차트 선)
  },
} satisfies ChartConfig

// X축 레이블 색상 (어두운 톤다운 색상)
const AXIS_LABEL_COLORS = {
  iTest: "hsl(0, 55%, 40%)", // 어두운 톤다운된 빨강
  meTest: "hsl(220, 55%, 40%)", // 어두운 톤다운된 파랑
}

// 커스텀 X축 틱 컴포넌트 (2행 표시)
interface CustomXAxisTickProps {
  x?: number
  y?: number
  payload?: { value: string; index: number }
  isMobile?: boolean
}

function CustomXAxisTick({ x = 0, y = 0, payload, isMobile = false }: CustomXAxisTickProps) {
  if (!payload) return null
  const index = payload.index
  const pair = WPI_AXIS_PAIRS[index]
  if (!pair) return null

  const fontSize = isMobile ? 9 : 11
  const yOffset1 = isMobile ? 10 : 12
  const yOffset2 = isMobile ? 22 : 28

  return (
    <g transform={`translate(${x},${y})`}>
      {/* 1행: 자기평가 유형 (어두운 빨강) */}
      <text
        x={0}
        y={yOffset1}
        textAnchor="middle"
        fill={AXIS_LABEL_COLORS.iTest}
        fontSize={fontSize}
        fontWeight={500}
      >
        {pair.iType}
      </text>
      {/* 2행: 타인평가 유형 (어두운 파랑) */}
      <text
        x={0}
        y={yOffset2}
        textAnchor="middle"
        fill={AXIS_LABEL_COLORS.meTest}
        fontSize={fontSize}
        fontWeight={500}
      >
        {pair.meType}
      </text>
    </g>
  )
}

export function WpiResultChart({
  iTestScores,
  meTestScores,
  iTestDominant,
  meTestDominant,
}: WpiResultChartProps) {
  const isMobile = useMediaQuery("(max-width: 767px)")

  // 차트 데이터 변환
  const chartData = WPI_AXIS_PAIRS.map((pair, index) => ({
    name: index.toString(), // X축 데이터키 (CustomXAxisTick에서 index로 사용)
    iTest: iTestScores[pair.iType],
    meTest: meTestScores[pair.meType],
    iType: pair.iType,
    meType: pair.meType,
  }))

  // 최대값 계산 (Y축 범위 설정용)
  const maxScore = Math.max(
    ...Object.values(iTestScores),
    ...Object.values(meTestScores),
    50 // 최소 50까지는 표시
  )
  const yAxisMax = Math.ceil(maxScore / 25) * 25 // 25 단위로 올림

  // 모바일/데스크톱 설정
  const chartMargin = isMobile
    ? { top: 10, right: 10, left: 0, bottom: 40 }
    : { top: 20, right: 30, left: 20, bottom: 60 }
  const chartHeight = isMobile ? 260 : 300

  return (
    <Card className="w-full">
      <CardHeader className="pb-2 px-3 md:px-6">
        <CardTitle className="text-base md:text-lg font-medium">WPI 프로파일 결과</CardTitle>
        <div className="flex flex-col gap-1 text-sm text-muted-foreground">
          <div>
            <span className="font-medium text-red-600">자기평가:</span>{" "}
            <span className="font-semibold">{iTestDominant || "미완료"}</span>
          </div>
          <div>
            <span className="font-medium text-blue-600">타인평가:</span>{" "}
            <span className="font-semibold">{meTestDominant || "미완료"}</span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-2 md:px-6">
        <ChartContainer config={chartConfig} className="w-full" style={{ height: chartHeight }}>
          <LineChart
            data={chartData}
            margin={chartMargin}
          >
            <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
            <XAxis
              dataKey="name"
              tick={<CustomXAxisTick isMobile={isMobile} />}
              tickLine={false}
              axisLine={false}
              interval={0}
              height={isMobile ? 35 : 50}
            />
            <YAxis
              domain={[0, yAxisMax]}
              tick={{ fontSize: isMobile ? 10 : 11 }}
              tickLine={false}
              axisLine={false}
              tickCount={yAxisMax / 25 + 1}
              width={isMobile ? 25 : 40}
            />
            <ReferenceLine y={0} stroke="hsl(var(--border))" />
            <ChartTooltip
              content={
                <ChartTooltipContent
                  formatter={(value, name, item) => (
                    <div className="flex items-center gap-2">
                      <span className="text-muted-foreground">
                        {name === "iTest"
                          ? `자기평가 (${item.payload.iType})`
                          : `타인평가 (${item.payload.meType})`}
                      </span>
                      <span className="font-mono font-medium">{value}</span>
                    </div>
                  )}
                />
              }
            />
            <Legend
              verticalAlign="top"
              height={isMobile ? 28 : 36}
              wrapperStyle={{ fontSize: isMobile ? 12 : 14 }}
              formatter={(value) =>
                value === "iTest" ? "자기평가" : "타인평가"
              }
            />
            <Line
              type="linear"
              dataKey="iTest"
              stroke="var(--color-iTest)"
              strokeWidth={2}
              dot={{ r: isMobile ? 4 : 5, fill: "var(--color-iTest)" }}
              activeDot={{ r: isMobile ? 5 : 7 }}
            />
            <Line
              type="linear"
              dataKey="meTest"
              stroke="var(--color-meTest)"
              strokeWidth={2}
              strokeDasharray="5 5"
              dot={{ r: isMobile ? 4 : 5, fill: "var(--color-meTest)" }}
              activeDot={{ r: isMobile ? 5 : 7 }}
            />
          </LineChart>
        </ChartContainer>

        {/* 점수 테이블 - 모바일: 카드 형태, 데스크톱: 테이블 */}
        <div className="mt-4 md:mt-6">
          {isMobile ? (
            // 모바일: 세로 카드 형태
            <div className="space-y-2">
              {WPI_AXIS_PAIRS.map((pair) => (
                <div key={pair.iType} className="p-3 rounded-lg border bg-muted/30">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-muted-foreground">유형</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="p-2 rounded bg-red-50/50 dark:bg-red-950/20">
                      <div className="text-xs text-red-600 font-medium">{pair.iType}</div>
                      <div className="text-lg font-semibold">{iTestScores[pair.iType].toFixed(1)}</div>
                    </div>
                    <div className="p-2 rounded bg-blue-50/50 dark:bg-blue-950/20">
                      <div className="text-xs text-blue-600 font-medium">{pair.meType}</div>
                      <div className="text-lg font-semibold">{meTestScores[pair.meType].toFixed(1)}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            // 데스크톱: 기존 테이블 형태
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b">
                    <th className="py-2 px-3 text-left font-medium"></th>
                    {WPI_AXIS_PAIRS.map((pair) => (
                      <th
                        key={pair.iType}
                        className="py-2 px-3 text-center font-medium"
                      >
                        {pair.iType}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b bg-red-50/50 dark:bg-red-950/20">
                    <td className="py-2 px-3 font-medium text-red-600">자기평가</td>
                    {WPI_AXIS_PAIRS.map((pair) => (
                      <td key={pair.iType} className="py-2 px-3 text-center">
                        {iTestScores[pair.iType].toFixed(1)}
                      </td>
                    ))}
                  </tr>
                  <tr className="border-b">
                    <th className="py-2 px-3 text-left font-medium"></th>
                    {WPI_AXIS_PAIRS.map((pair) => (
                      <th
                        key={pair.meType}
                        className="py-2 px-3 text-center font-medium"
                      >
                        {pair.meType}
                      </th>
                    ))}
                  </tr>
                  <tr className="bg-blue-50/50 dark:bg-blue-950/20">
                    <td className="py-2 px-3 font-medium text-blue-600">타인평가</td>
                    {WPI_AXIS_PAIRS.map((pair) => (
                      <td key={pair.meType} className="py-2 px-3 text-center">
                        {meTestScores[pair.meType].toFixed(1)}
                      </td>
                    ))}
                  </tr>
                </tbody>
              </table>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
