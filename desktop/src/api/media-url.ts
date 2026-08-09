const MEDIA_PROTOCOL_ORIGIN = "http://memesort-media.localhost";

export function mediaUrl(projectedPath: string | null): string | undefined {
  if (!projectedPath?.startsWith("/media/")) {
    return undefined;
  }
  return `${MEDIA_PROTOCOL_ORIGIN}${projectedPath}`;
}
