/* The Hush mark: five rounded waveform bars, light blue on navy. Geometry
   follows desktop/gen_icons.py (bar heights 0.35/0.65/1/0.65/0.35). */

const BARS = [
  { x: 6.5, h: 6.6 },
  { x: 10.25, h: 12.2 },
  { x: 14, h: 18.8 },
  { x: 17.75, h: 12.2 },
  { x: 21.5, h: 6.6 },
] as const;

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true" className="logo">
      <rect width="28" height="28" rx="7.5" fill="#0f172a" />
      {BARS.map((b) => (
        <rect
          key={b.x}
          x={b.x - 1.15}
          y={14 - b.h / 2}
          width="2.3"
          height={b.h}
          rx="1.15"
          fill="#7dd3fc"
        />
      ))}
    </svg>
  );
}
