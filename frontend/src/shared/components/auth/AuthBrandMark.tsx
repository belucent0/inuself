interface AuthBrandMarkProps {
  tone?: 'emerald' | 'blue'
}

export function AuthBrandMark({ tone = 'emerald' }: AuthBrandMarkProps) {
  const ringGradient =
    tone === 'blue'
      ? 'from-sky-300 via-blue-400 to-indigo-500'
      : 'from-emerald-300 via-teal-400 to-cyan-500'

  const glowColor = tone === 'blue' ? 'bg-blue-400/40' : 'bg-emerald-400/40'

  return (
    <div className="relative mx-auto h-14 w-14">
      <div className={`absolute inset-0 rounded-2xl blur-md ${glowColor}`} aria-hidden="true" />
      <div className={`relative h-14 w-14 rounded-2xl bg-gradient-to-br p-[1.5px] ${ringGradient}`}>
        <div className="relative flex h-full w-full items-center justify-center rounded-[14px] border border-white/10 bg-slate-900/90 backdrop-blur">
          <div className="absolute inset-0 rounded-[14px] bg-[radial-gradient(circle_at_top,_rgba(255,255,255,0.18),transparent_52%)]" />
          <div className="relative flex items-center gap-[3px]">
            <span className="h-5 w-[5px] rounded-full bg-white/90" />
            <span className="h-3.5 w-[5px] rounded-full bg-white/65" />
            <span className="h-5 w-[5px] rounded-full bg-white/90" />
          </div>
        </div>
      </div>
    </div>
  )
}
