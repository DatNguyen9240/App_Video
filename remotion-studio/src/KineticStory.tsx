import React from 'react';
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

export type KineticStoryProps = {
  badge: string;
  title: string;
  subtitle: string;
};

export const KineticStory: React.FC<KineticStoryProps> = ({ badge, title, subtitle }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({ frame, fps, config: { damping: 14, stiffness: 120 } });
  const opacity = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: 'clamp' });
  const translateY = interpolate(enter, [0, 1], [60, 0]);

  return (
    <AbsoluteFill
      style={{
        background: 'linear-gradient(160deg, #090d16 0%, #111827 55%, #1e1b4b 100%)',
        justifyContent: 'center',
        alignItems: 'center',
        padding: 64,
        fontFamily: 'Segoe UI, sans-serif',
      }}
    >
      <div
        style={{
          opacity,
          transform: `translateY(${translateY}px)`,
          textAlign: 'center',
          maxWidth: 920,
        }}
      >
        <div
          style={{
            display: 'inline-block',
            backgroundColor: '#e11d48',
            color: '#fff',
            fontWeight: 800,
            fontSize: 28,
            padding: '10px 24px',
            borderRadius: 999,
            marginBottom: 28,
          }}
        >
          {badge}
        </div>
        <h1 style={{ color: '#f8fafc', fontSize: 68, fontWeight: 900, lineHeight: 1.15, margin: 0 }}>
          {title}
        </h1>
        <p style={{ color: '#94a3b8', fontSize: 34, marginTop: 24 }}>{subtitle}</p>
      </div>
    </AbsoluteFill>
  );
};
