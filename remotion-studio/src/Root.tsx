import React from 'react';
import { Composition } from 'remotion';
import { KineticStory, KineticStoryProps } from './KineticStory';

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="KineticStory"
      component={KineticStory}
      durationInFrames={300}
      fps={60}
      width={1080}
      height={1920}
      defaultProps={{
        badge: 'HỒ SƠ KỲ THÚ',
        title: 'MOTION GRAPHICS STUDIO',
        subtitle: 'Powered by Remotion + GSAP + Three.js + D3',
      } satisfies KineticStoryProps}
    />
  );
};
