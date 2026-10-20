'use client';

import '../styles/video/base.css';
import { Controls, Tooltip } from '@videojs/react';
import type { ComponentProps, ReactNode } from 'react';

import { BufferingIndicator } from '@/components/videojs/ui/buffering-indicator';
import { Container } from '@/components/videojs/ui/container';
import { ErrorDialog } from '@/components/videojs/ui/error-dialog';
import { Poster } from '@/components/videojs/ui/poster';
import { Title } from '@/components/videojs/ui/title';
import { cn } from '@/lib/resolve-class-name';

import { VideoGestures } from './behaviors/gestures';
import { VideoHotkeys } from './behaviors/hotkeys';
import { VideoStatusIndicators } from './display/status-indicators';
import { DefaultVideoControls } from './layout/controls';

export interface VideoSkinProps extends Omit<NonNullable<ComponentProps<typeof Container>>, 'children'> {
  children?: ReactNode;
  renderPoster?: NonNullable<ComponentProps<typeof Poster>>['renderImage'];
  renderThumbnail?: NonNullable<ComponentProps<typeof DefaultVideoControls>>['renderThumbnail'];
  playlist?: NonNullable<ComponentProps<typeof DefaultVideoControls>>['playlist'];
  /** the page's own controls, top right: shown and hidden with the player's */
  corner?: ReactNode;
  /** leaving the player, top left before the title: shown and hidden with the player's controls */
  back?: ReactNode;
}

export function VideoSkin({ children, className, renderPoster, renderThumbnail, playlist, corner, back, ...props }: VideoSkinProps = {}) {
  return (
    <Container
      className={cn('pointer-fine:not-data-controls-visible:cursor-none', className)}
      data-theme="default"
      data-preset="video"
      {...props}
    >
      {children}
      <Poster renderImage={renderPoster} />
      <BufferingIndicator />
      <ErrorDialog />
      <Title className={cn(corner && 'media-lg:pe-40', back && 'ps-14 media-md:ps-14')} />

      <DefaultVideoControls renderThumbnail={renderThumbnail} playlist={playlist} />
      {back && <Corner className="left-2 media-2xl:left-3">{back}</Corner>}
      {corner && <Corner className="right-2 media-max-lg:top-14 media-2xl:right-3">{corner}</Corner>}

      <VideoHotkeys />
      <VideoGestures />
      <VideoStatusIndicators />
    </Container>
  );
}

/** The page's own controls in a corner of the video (top, its side in className): shown and hidden with the player's. */
function Corner({ className, children }: { className: string; children: ReactNode }) {
  return (
    <Controls.Root>
      <Controls.Content
        className={cn(
          'absolute top-2 z-30 flex origin-top items-center rtl:flex-row-reverse gap-px rounded-media-controls p-0.5',
          'media-2xl:top-3',
          'bg-media-popover text-media-popover-foreground surface-media after:surface-media-inset',
          'transition-[filter,opacity,scale,translate] duration-media-controls-enter ease-out',
          'not-data-visible:pointer-events-none not-data-visible:opacity-0 not-data-visible:scale-media-hidden',
          'not-data-visible:-translate-y-media-hidden-offset not-data-visible:duration-media-controls',
          'pointer-fine:not-data-visible:blur-media-hidden',
          className
        )}
      >
        <Tooltip.Provider>{children}</Tooltip.Provider>
      </Controls.Content>
    </Controls.Root>
  );
}
