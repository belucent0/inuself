/**
 * HTML5 오디오/비디오 플레이어
 * mediaRef를 통해 세그먼트 시크와 연동
 */

import type { RefObject } from 'react'
import { isVideoFile } from '../../types'

interface AudioVideoPlayerProps {
  src: string
  filename: string
  mediaRef: RefObject<HTMLMediaElement | null>
}

export function AudioVideoPlayer({ src, filename, mediaRef }: AudioVideoPlayerProps) {
  const isVideo = isVideoFile(filename)

  if (isVideo) {
    return (
      <div className="flex justify-center">
        <video
          ref={mediaRef as RefObject<HTMLVideoElement | null>}
          controls
          className="max-w-full max-h-[25vh] rounded-lg"
          preload="metadata"
        >
          <source src={src} />
        </video>
      </div>
    )
  }

  return (
    <audio
      ref={mediaRef as RefObject<HTMLAudioElement | null>}
      controls
      className="w-full"
      preload="metadata"
    >
      <source src={src} />
    </audio>
  )
}
