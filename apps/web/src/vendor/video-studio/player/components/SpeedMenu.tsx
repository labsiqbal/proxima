import { useState, useRef, useEffect, memo } from "react";
import { trackStudioEvent } from "../../utils/studioTelemetry";
import { Tooltip } from "../../components/ui";

const SPEED_OPTIONS = [0.25, 0.5, 1, 1.5, 2] as const;

interface SpeedMenuProps {
  playbackRate: number;
  setPlaybackRate: (rate: number) => void;
  disabled: boolean;
}

export const SpeedMenu = memo(function SpeedMenu({
  playbackRate,
  setPlaybackRate,
  disabled,
}: SpeedMenuProps) {
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const speedMenuContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!showSpeedMenu) return;
    const handleMouseDown = (e: MouseEvent) => {
      if (
        speedMenuContainerRef.current &&
        !speedMenuContainerRef.current.contains(e.target as Node)
      ) {
        setShowSpeedMenu(false);
      }
    };
    document.addEventListener("mousedown", handleMouseDown);
    return () => {
      document.removeEventListener("mousedown", handleMouseDown);
    };
  }, [showSpeedMenu]);

  return (
    <div ref={speedMenuContainerRef} className="relative flex-shrink-0">
      <Tooltip label="Playback speed">
        <button
          type="button"
          onClick={() => setShowSpeedMenu((v) => !v)}
          disabled={disabled}
          className="w-10 px-2 py-1 rounded-md text-[10px] font-mono tabular-nums transition-colors"
          style={{
            color: "var(--proxima-video-muted, #4b5563)",
            background:
              "color-mix(in srgb, var(--proxima-video-text, #111827) 5%, var(--proxima-video-surface, #fff))",
          }}
        >
          {playbackRate === 1 ? "1x" : `${playbackRate}x`}
        </button>
      </Tooltip>
      {showSpeedMenu && (
        <div
          className="absolute bottom-full right-0 mb-1.5 rounded-lg shadow-xl z-50 min-w-[56px] overflow-hidden"
          style={{
            background: "var(--proxima-video-surface, #fff)",
            border: "1px solid var(--proxima-video-border, rgba(15,23,42,0.14))",
            boxShadow: "0 14px 30px rgba(15,23,42,0.16)",
          }}
        >
          {SPEED_OPTIONS.map((rate) => (
            <button
              key={rate}
              onClick={() => {
                trackStudioEvent("playback", { action: "speed_change", rate });
                setPlaybackRate(rate);
                setShowSpeedMenu(false);
              }}
              className="block w-full px-3 py-1.5 text-[11px] text-left font-mono tabular-nums transition-colors"
              style={{
                color:
                  rate === playbackRate
                    ? "var(--proxima-video-accent-strong, var(--ui-accent))"
                    : "var(--proxima-video-muted, #4b5563)",
                background:
                  rate === playbackRate
                    ? "color-mix(in srgb, var(--ui-accent) 10%, var(--proxima-video-surface, #fff))"
                    : "transparent",
              }}
              onMouseEnter={(e) => {
                if (rate !== playbackRate)
                  e.currentTarget.style.background =
                    "color-mix(in srgb, var(--ui-accent) 7%, var(--proxima-video-surface, #fff))";
              }}
              onMouseLeave={(e) => {
                if (rate !== playbackRate) e.currentTarget.style.background = "transparent";
              }}
            >
              {rate}x
            </button>
          ))}
        </div>
      )}
    </div>
  );
});
